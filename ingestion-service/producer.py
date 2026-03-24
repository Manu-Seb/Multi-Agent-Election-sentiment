# TT-RSS + Bluesky Ingestion -> Kafka (raw_ingestion)
# This service polls TT-RSS and optional Bluesky search queries, normalizes them
# to the shared Article schema, and publishes them to Redpanda/Kafka.

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from hashlib import sha1
from typing import Any, Dict, List, Optional

import httpx
from confluent_kafka import Producer
from dotenv import load_dotenv
from pydantic import BaseModel

from bluesky_priority import load_priority_topics, prune_priority_topics, save_priority_topics
from services.content_fetcher import fetch_full_content

try:
    from bluesky_search import BlueskyPostsFetcher
except ImportError:
    try:
        from src.bluesky_search import BlueskyPostsFetcher  # type: ignore[no-redef]
    except ImportError:
        BlueskyPostsFetcher = None  # type: ignore[assignment]


load_dotenv(override=False)

TTRSS_URL = os.getenv("TTRSS_URL", "http://localhost/tt-rss/api/")
TTRSS_USER = os.getenv("TTRSS_USER", "admin")
TTRSS_PASSWORD = os.getenv("TTRSS_PASSWORD", "password")
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "raw_ingestion")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))
TTRSS_BATCH_LIMIT = int(os.getenv("TTRSS_BATCH_LIMIT", "30"))

BLUESKY_ENABLED = os.getenv("BLUESKY_ENABLED", "true").lower() not in {"0", "false", "no"}
BLUESKY_USERNAME = os.getenv("BLUESKY_USERNAME", "")
BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD", "")
BLUESKY_SEARCH_TERMS = os.getenv("BLUESKY_SEARCH_TERMS", "")
BLUESKY_LIMIT = int(os.getenv("BLUESKY_LIMIT", "20"))
BLUESKY_STATE_MAX_IDS = int(os.getenv("BLUESKY_STATE_MAX_IDS", "500"))

STATE_FILE = "producer.state"
BLUESKY_STATE_FILE = "producer.bluesky.state.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("ingest-worker")


class Article(BaseModel):
    id: str | int
    title: str
    content: str
    link: str
    feed_id: int
    feed_title: str
    published_at: datetime
    author: Optional[str]
    tags: List[str]
    source: str

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat().replace("+00:00", "Z")}


def parse_search_terms(raw_terms: str) -> List[str]:
    seen: set[str] = set()
    terms: List[str] = []
    for raw in raw_terms.split(","):
        term = raw.strip()
        normalized = term.lower()
        if not term or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(term)
    return terms


def active_bluesky_queries() -> List[str]:
    queries: List[str] = []
    seen: set[str] = set()

    priority_items = prune_priority_topics(load_priority_topics())
    save_priority_topics(priority_items)

    for item in priority_items:
        topic = str(item.get("topic", "")).strip()
        normalized = topic.lower()
        if topic and normalized not in seen:
            queries.append(topic)
            seen.add(normalized)

    for topic in parse_search_terms(BLUESKY_SEARCH_TERMS):
        normalized = topic.lower()
        if normalized in seen:
            continue
        queries.append(topic)
        seen.add(normalized)

    return queries


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def derive_bluesky_post_id(raw: Dict[str, Any]) -> str:
    author = raw.get("author") or {}
    handle = author.get("handle", "")
    uri = raw.get("uri") or raw.get("web_url") or raw.get("id")
    if uri:
        return str(uri)
    fingerprint = f"{handle}:{raw.get('text', '')}:{raw.get('created_at', '')}"
    return sha1(fingerprint.encode("utf-8")).hexdigest()


def build_bluesky_link(raw: Dict[str, Any]) -> str:
    if raw.get("web_url"):
        return str(raw["web_url"])
    author = raw.get("author") or {}
    handle = author.get("handle")
    uri = str(raw.get("uri", ""))
    if handle and uri:
        post_id = uri.rstrip("/").split("/")[-1]
        return f"https://bsky.app/profile/{handle}/post/{post_id}"
    return ""


def build_bluesky_title(text: str) -> str:
    clean = " ".join(text.split())
    if not clean:
        return "Bluesky post"
    if len(clean) <= 120:
        return clean
    return f"{clean[:117]}..."


def get_session_id() -> str:
    try:
        payload = {"op": "login", "user": TTRSS_USER, "password": TTRSS_PASSWORD}
        resp = httpx.post(TTRSS_URL, json=payload, timeout=10.0, verify=False)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != 0:
            raise RuntimeError(f"Login failed: {data}")
        return data["content"]["session_id"]
    except Exception as exc:
        logger.error("Failed to login to TT-RSS: %s", exc)
        raise


def fetch_ttrss_articles(sid: str) -> List[Dict[str, Any]]:
    payload = {
        "op": "getHeadlines",
        "sid": sid,
        "feed_id": -4,
        "is_cat": False,
        "show_content": True,
        "view_mode": "all_articles",
        "limit": TTRSS_BATCH_LIMIT,
    }
    try:
        resp = httpx.post(TTRSS_URL, json=payload, timeout=20.0, verify=False)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != 0:
            if "NOT_LOGGED_IN" in str(data):
                raise RuntimeError("NOT_LOGGED_IN")
            raise RuntimeError(f"Fetch failed: {data}")
        return data["content"]
    except Exception as exc:
        logger.error("Error fetching TT-RSS articles: %s", exc)
        raise


def normalize_ttrss_article(raw: Dict[str, Any]) -> Article:
    ts = raw.get("updated", 0)
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    excerpt = raw.get("content", "")
    full_content = fetch_full_content(raw.get("link", ""), fallback=excerpt)
    return Article(
        id=raw.get("id"),
        title=raw.get("title", ""),
        content=full_content,
        link=raw.get("link", ""),
        feed_id=raw.get("feed_id", 0),
        feed_title=raw.get("feed_title", "Unknown Feed"),
        published_at=dt,
        author=raw.get("author") or None,
        tags=["rss feed"],
        source="ttrss",
    )


def create_bluesky_fetcher():
    if not BLUESKY_ENABLED:
        return None
    if not active_bluesky_queries():
        logger.info("Bluesky intake disabled: no active default or priority queries configured.")
        return None
    if BlueskyPostsFetcher is None:
        raise RuntimeError(
            "bluesky-search is not installed or importable. Install the dependency before enabling Bluesky intake."
        )
    if not BLUESKY_USERNAME or not BLUESKY_PASSWORD:
        raise RuntimeError("BLUESKY_USERNAME and BLUESKY_PASSWORD are required when Bluesky intake is enabled.")
    return BlueskyPostsFetcher(username=BLUESKY_USERNAME, password=BLUESKY_PASSWORD)


def fetch_bluesky_posts(fetcher: Any, query: str) -> List[Dict[str, Any]]:
    try:
        posts = fetcher.search_posts(query, limit=BLUESKY_LIMIT)
        return posts or []
    except Exception as exc:
        logger.error("Error fetching Bluesky posts for query=%r: %s", query, exc)
        raise


def normalize_bluesky_post(raw: Dict[str, Any], query: str) -> Article:
    author = raw.get("author") or {}
    text = str(raw.get("text", "")).strip()
    article_id = derive_bluesky_post_id(raw)
    published_at = parse_datetime(raw.get("created_at") or raw.get("indexed_at"))
    author_name = author.get("display_name") or author.get("handle")
    link = build_bluesky_link(raw)
    return Article(
        id=article_id,
        title=build_bluesky_title(text),
        content=text,
        link=link,
        feed_id=0,
        feed_title="Bluesky Search",
        published_at=published_at,
        author=author_name,
        tags=["social media", query],
        source="bluesky",
    )


def publish(producer: Producer, article: Article):
    key = article.feed_title.strip() or article.source
    value = article.model_dump_json()
    try:
        producer.produce(topic=KAFKA_TOPIC, key=key.encode("utf-8"), value=value.encode("utf-8"))
    except BufferError:
        logger.error("Kafka producer queue full, retrying after poll.")
        producer.poll(1.0)
        producer.produce(topic=KAFKA_TOPIC, key=key.encode("utf-8"), value=value.encode("utf-8"))
    except Exception as exc:
        logger.critical("Failed to produce message: %s", exc)
        raise


def load_last_id() -> int:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as handle:
                return int(handle.read().strip())
        except Exception:
            return 0
    return 0


def save_last_id(last_id: int):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            handle.write(str(last_id))
    except Exception as exc:
        logger.error("Failed to save TT-RSS state: %s", exc)


def load_bluesky_state() -> Dict[str, List[str]]:
    if not os.path.exists(BLUESKY_STATE_FILE):
        return {}
    try:
        with open(BLUESKY_STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    state: Dict[str, List[str]] = {}
    for query, ids in data.items():
        if isinstance(query, str) and isinstance(ids, list):
            state[query] = [str(item) for item in ids]
    return state


def save_bluesky_state(state: Dict[str, List[str]]):
    try:
        with open(BLUESKY_STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
    except Exception as exc:
        logger.error("Failed to save Bluesky state: %s", exc)


def process_ttrss(producer: Producer, sid: str, last_seen_id: int) -> tuple[int, int]:
    raw_items = fetch_ttrss_articles(sid)
    raw_items.sort(key=lambda item: int(item.get("id", 0)))
    processed = 0
    new_max_id = last_seen_id

    for item in raw_items:
        item_id = int(item.get("id", 0))
        if item_id <= last_seen_id:
            continue
        publish(producer, normalize_ttrss_article(item))
        processed += 1
        new_max_id = max(new_max_id, item_id)

    if not processed:
        logger.info("No new TT-RSS articles. Head ID remains %s", last_seen_id)
    return processed, new_max_id


def process_bluesky(producer: Producer, fetcher: Any, state: Dict[str, List[str]]) -> tuple[int, Dict[str, List[str]]]:
    queries = active_bluesky_queries()
    if not fetcher or not queries:
        return 0, state

    processed = 0
    updated_state = dict(state)
    for query in queries:
        known_ids = updated_state.get(query, [])
        known_lookup = set(known_ids)
        for raw_post in fetch_bluesky_posts(fetcher, query):
            post_id = derive_bluesky_post_id(raw_post)
            if post_id in known_lookup:
                continue
            publish(producer, normalize_bluesky_post(raw_post, query))
            known_ids.append(post_id)
            known_lookup.add(post_id)
            processed += 1
        updated_state[query] = known_ids[-BLUESKY_STATE_MAX_IDS:]

    if processed:
        logger.info("Ingested %s Bluesky posts across queries=%s", processed, queries)
    else:
        logger.info("No new Bluesky posts for queries=%s", queries)
    return processed, updated_state


def main():
    logger.info("Starting Ingestion Worker. Broker=%s Topic=%s", KAFKA_BROKER, KAFKA_TOPIC)
    producer = Producer({"bootstrap.servers": KAFKA_BROKER, "client.id": "multi-source-ingest-1"})

    sid = get_session_id()
    logger.info("Logged in to TT-RSS.")

    last_seen_id = load_last_id()
    bluesky_state = load_bluesky_state()
    bluesky_fetcher = create_bluesky_fetcher()
    if bluesky_fetcher:
        logger.info("Bluesky intake enabled for queries=%s", active_bluesky_queries())

    try:
        while True:
            start_time = time.time()
            ttrss_processed, new_last_seen_id = process_ttrss(producer, sid, last_seen_id)
            bluesky_processed, new_bluesky_state = process_bluesky(producer, bluesky_fetcher, bluesky_state)

            if ttrss_processed > 0 or bluesky_processed > 0:
                producer.flush()
                if ttrss_processed > 0:
                    last_seen_id = new_last_seen_id
                    save_last_id(last_seen_id)
                    logger.info("Ingested %s TT-RSS articles. New head ID=%s", ttrss_processed, last_seen_id)
                if bluesky_processed > 0:
                    bluesky_state.clear()
                    bluesky_state.update(new_bluesky_state)
                    save_bluesky_state(bluesky_state)

            elapsed = time.time() - start_time
            time.sleep(max(0, POLL_INTERVAL - elapsed))
    except KeyboardInterrupt:
        logger.info("Stopping worker...")
        producer.flush()
    except Exception as exc:
        logger.critical("Worker crashed: %s", exc)
        producer.flush()
        sys.exit(1)


if __name__ == "__main__":
    main()
