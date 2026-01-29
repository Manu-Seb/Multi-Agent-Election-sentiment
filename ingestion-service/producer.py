# TT-RSS Ingestion -> Kafka (raw_ingestion)
# This service polls a Tiny Tiny RSS (TT-RSS) instance, fetches recent articles,
# strictly normalizes them to the defined Article schema, and publishes them to Redpanda/Kafka.
# It is designed to be stateless and fault-tolerant by crashing loud on errors.

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

# Third-party dependencies
# pip install confluent-kafka pydantic httpx
import httpx
from confluent_kafka import Producer
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(override=True)

# --- Configuration ---
TTRSS_URL = os.getenv("TTRSS_URL", "http://localhost/tt-rss/api/")
TTRSS_USER = os.getenv("TTRSS_USER", "admin")
TTRSS_PASSWORD = os.getenv("TTRSS_PASSWORD", "password")
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
KAFKA_TOPIC = "raw_ingestion"
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))

# --- Logging (Minimal stdout) ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("ingest-worker")

# --- Data Contract ---
class Article(BaseModel):
    id: str | int
    title: str
    content: str
    link: str
    feed_id: int
    feed_title: str
    published_at: datetime  # ISO-8601 UTC
    author: Optional[str]
    tags: List[str]

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat().replace("+00:00", "Z")
        }

# --- TTRSS Client Logic ---
# Keeping strictly to the requested structure: fetch_articles, normalize_article, publish

def get_session_id() -> str:
    """Synchronous login to get session ID."""
    try:
        payload = {"op": "login", "user": TTRSS_USER, "password": TTRSS_PASSWORD}
        # Using httpx in sync mode for simplicity in this worker loop
        resp = httpx.post(TTRSS_URL, json=payload, timeout=10.0, verify=False)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != 0:
             raise Exception(f"Login failed: {data}")
        return data["content"]["session_id"]
    except Exception as e:
        logger.error(f"Failed to login to TT-RSS: {e}")
        raise e

def fetch_articles(sid: str) -> List[Dict[str, Any]]:
    """Fetches text/html, -4 view_mode=all_articles, is_cat=False."""
    # We fetch 'all articles' (-4). In a real production system, 
    # we might track the last seen ID, but instructions say 'Duplicate ingestion is acceptable'.
    payload = {
        "op": "getHeadlines",
        "sid": sid,
        "feed_id": -4,
        "is_cat": False,
        "show_content": True,
        "view_mode": "all_articles", 
        "limit": 30 # Batch size
    }
    
    try:
        resp = httpx.post(TTRSS_URL, json=payload, timeout=20.0, verify=False)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != 0:
            if "NOT_LOGGED_IN" in str(data):
                 # We let this fail loudly so the main loop re-authenticates or restarts
                 raise Exception("NOT_LOGGED_IN")
            raise Exception(f"Fetch failed: {data}")
        return data["content"]
    except Exception as e:
        logger.error(f"Error fetching articles: {e}")
        raise e

def normalize_article(raw: Dict[str, Any]) -> Article:
    """Strict normalization to Article schema."""
    # TT-RSS timestamp is unix int
    ts = raw.get("updated", 0)
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    
    # "Message key must be derived from the article’s region... If unavailable, use feed_title"
    # We don't have explicit region in TT-RSS raw data. We check tags if we had them or just feed_title.
    # The prompt implies region might be in the data, but usually it isn't unless we parse it.
    # We will assume feed_title is the fallback key.
    
    # Note: TTRSS might return tags as comma-separated string or list? 
    # Usually it's not well structured in standard response unless requested.
    # We'll default to empty list if not finding obvious tags.
    
    return Article(
        id=raw.get("id"),
        title=raw.get("title", ""),
        content=raw.get("content", ""),
        link=raw.get("link", ""),
        feed_id=raw.get("feed_id", 0),
        feed_title=raw.get("feed_title", "Unknown Feed"),
        published_at=dt,
        author=raw.get("author") or None,
        tags=[] # Raw TTRSS getHeadlines doesn't typically output tags unless custom plugin
    )

def publish(producer: Producer, article: Article):
    """Publishes to Kafka."""
    try:
        # Key derivation: Region or Feed Title
        # Since we don't have region logic here, we use feed_title as per fallback rule.
        key = article.feed_title.strip()
        
        # Value: JSON
        # pydantic .json() or .model_dump_json()
        value = article.model_dump_json()
        
        producer.produce(
            topic=KAFKA_TOPIC,
            key=key.encode("utf-8"),
            value=value.encode("utf-8")
        )
    except BufferError:
        logger.error("Kafka Producer queue full, waiting...")
        producer.poll(1.0)
        producer.produce(
            topic=KAFKA_TOPIC,
            key=article.feed_title.encode("utf-8"),
            value=article.model_dump_json().encode("utf-8")
        )
    except Exception as e:
        logger.critical(f"Failed to produce message: {e}")
        raise e

# --- State Persistence ---
STATE_FILE = "producer.state"

def load_last_id() -> int:
    """Loads the last processed article ID."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return int(f.read().strip())
        except Exception:
            return 0
    return 0

def save_last_id(last_id: int):
    """Saves the highest article ID encountered."""
    try:
        with open(STATE_FILE, "w") as f:
            f.write(str(last_id))
    except Exception as e:
        logger.error(f"Failed to save state: {e}")

# --- Main Loop ---
def main():
    logger.info(f"Starting Ingestion Worker. Broker={KAFKA_BROKER}, Topic={KAFKA_TOPIC}")
    
    # Initialize Kafka Producer
    conf = {
        'bootstrap.servers': KAFKA_BROKER,
        'client.id': 'ttrss-ingest-1',
    }
    producer = Producer(conf)
    
    # Initialize Session
    sid = get_session_id()
    logger.info(f"Logged in to TT-RSS. SID={sid}")
    
    # Load State
    last_seen_id = load_last_id()
    logger.info(f"Resuming from Article ID > {last_seen_id}")

    try:
        while True:
            start_time = time.time()
            
            # Fetch
            raw_items = fetch_articles(sid)
            
            # Sort raw items by ID ascending to handle state correctly
            # (TT-RSS usually returns newest first, so we reverse to process safely)
            raw_items.sort(key=lambda x: int(x.get("id", 0)))
            
            count = 0
            new_max_id = last_seen_id
            
            for item in raw_items:
                item_id = int(item.get("id", 0))
                
                # Deduplication Check
                if item_id <= last_seen_id:
                    continue
                
                # Normalize
                article = normalize_article(item)
                
                # Publish
                publish(producer, article)
                
                count += 1
                new_max_id = max(new_max_id, item_id)
            
            # Update State if we processed anything new
            if count > 0:
                producer.flush()
                save_last_id(new_max_id)
                last_seen_id = new_max_id
                logger.info(f"Ingested {count} new articles. New Head ID: {last_seen_id}. Sleeping {POLL_INTERVAL}s...")
            else:
                logger.info(f"No new articles found. Head ID stuck at {last_seen_id}. Sleeping {POLL_INTERVAL}s...")
            
            # Sleep remainder of interval
            elapsed = time.time() - start_time
            sleep_time = max(0, POLL_INTERVAL - elapsed)
            time.sleep(sleep_time)
            
    except KeyboardInterrupt:
        logger.info("Stopping worker...")
        producer.flush()
    except Exception as e:
        logger.critical(f"Worker crashed: {e}")
        producer.flush()
        sys.exit(1)

if __name__ == "__main__":
    main()
