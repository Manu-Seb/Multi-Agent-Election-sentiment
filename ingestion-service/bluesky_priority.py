import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any


PRIORITY_STATE_FILE = os.getenv("BLUESKY_PRIORITY_STATE_FILE", "producer.bluesky.priority.json")
PRIORITY_TTL_SECONDS = int(os.getenv("BLUESKY_PRIORITY_TTL_SECONDS", "1800"))
PRIORITY_MAX_TOPICS = int(os.getenv("BLUESKY_PRIORITY_MAX_TOPICS", "10"))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_topic(topic: str) -> str:
    return " ".join(topic.strip().split()).lower()


def load_priority_topics() -> list[dict[str, Any]]:
    if not os.path.exists(PRIORITY_STATE_FILE):
        return []
    try:
        with open(PRIORITY_STATE_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    items: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        topic = normalize_topic(str(item.get("topic", "")))
        requested_at = str(item.get("requested_at", "")).strip()
        if topic and requested_at:
            items.append({"topic": topic, "requested_at": requested_at})
    return items


def save_priority_topics(items: list[dict[str, Any]]) -> None:
    with open(PRIORITY_STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(items, handle)


def prune_priority_topics(
    items: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    ttl_seconds: int = PRIORITY_TTL_SECONDS,
    max_topics: int = PRIORITY_MAX_TOPICS,
) -> list[dict[str, Any]]:
    now = now or utcnow()
    cutoff = now - timedelta(seconds=ttl_seconds)
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in sorted(items, key=lambda value: value.get("requested_at", ""), reverse=True):
        topic = normalize_topic(str(item.get("topic", "")))
        requested_at_raw = str(item.get("requested_at", "")).strip()
        if not topic or topic in seen or not requested_at_raw:
            continue
        try:
            requested_at = datetime.fromisoformat(requested_at_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if requested_at < cutoff:
            continue
        seen.add(topic)
        kept.append({"topic": topic, "requested_at": requested_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")})
        if len(kept) >= max_topics:
            break
    return kept


def register_priority_topic(topic: str) -> dict[str, Any]:
    normalized = normalize_topic(topic)
    if not normalized:
        raise ValueError("topic must not be empty")

    items = load_priority_topics()
    items = [item for item in items if normalize_topic(str(item.get("topic", ""))) != normalized]
    items.append({"topic": normalized, "requested_at": utcnow().isoformat().replace("+00:00", "Z")})
    items = prune_priority_topics(items)
    save_priority_topics(items)

    return {
        "topic": normalized,
        "requested_at": items[0]["requested_at"] if items and items[0]["topic"] == normalized else utcnow().isoformat().replace("+00:00", "Z"),
        "active_topics": [item["topic"] for item in items],
        "ttl_seconds": PRIORITY_TTL_SECONDS,
    }

