from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage_service.db import StorageRepository
from storage_service.state import apply_change_event, empty_graph_state


def _topic_terms(topic: str) -> tuple[str, ...]:
    terms = []
    for raw in topic.lower().split():
        token = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
        if len(token) >= 3:
            terms.append(token)
    return tuple(terms)


def _matches_topic(change_event: dict[str, Any], topic_terms: tuple[str, ...]) -> bool:
    if not topic_terms:
        return True
    article_id = str(change_event.get("article_id", "")).lower()
    entity_ids = " ".join(str(ch.get("id", "")) for ch in change_event.get("entity_changes", []))
    haystack = f"{article_id} {entity_ids}".lower()
    return any(term in haystack for term in topic_terms)


def current_full_state(repo: StorageRepository, topic: str) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    snap = repo.fetch_nearest_snapshot_before(now)
    terms = _topic_terms(topic)
    if snap:
        state = snap["graph_state"]
        from_time = snap["snapshot_time"]
    else:
        state = empty_graph_state()
        from_time = datetime.fromtimestamp(0, tz=timezone.utc)

    for row in repo.fetch_changes_between(from_time, now):
        if _matches_topic(row["changes"], terms):
            state = apply_change_event(state, row["changes"])
    return state


def replay_from_last_event(repo: StorageRepository, last_event_id: int, limit: int, topic: str) -> list[dict[str, Any]]:
    terms = _topic_terms(topic)
    rows = repo.fetch_changes_after_id(last_event_id, limit=limit)
    return [row["changes"] for row in rows if _matches_topic(row["changes"], terms)]
