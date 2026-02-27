from __future__ import annotations

from collections import OrderedDict, defaultdict
from datetime import datetime
from typing import Any

from .utils import parse_window, utc_now


class ReconstructionCache:
    def __init__(self, ttl_seconds: int, max_size: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._items: OrderedDict[str, tuple[datetime, dict[str, Any]]] = OrderedDict()

    def get(self, key: str) -> dict[str, Any] | None:
        item = self._items.get(key)
        if not item:
            return None
        created, value = item
        if (utc_now() - created).total_seconds() > self.ttl_seconds:
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return value

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._items[key] = (utc_now(), value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_size:
            self._items.popitem(last=False)


def empty_graph_state() -> dict[str, Any]:
    return {"nodes": {}, "edges": {}}


def apply_change_event(graph_state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    state = {
        "nodes": dict(graph_state.get("nodes", {})),
        "edges": dict(graph_state.get("edges", {})),
    }

    for change in event.get("entity_changes", []):
        node_id = change["id"]
        state["nodes"][node_id] = {
            "id": node_id,
            "mention_count": change["new_mentions"],
            "sentiment": change["new_sentiment"],
            "centrality": change.get("new_centrality", 0.0),
        }

    for change in event.get("relationship_changes", []):
        key = f"{change['source']}::{change['target']}"
        state["edges"][key] = {
            "source": change["source"],
            "target": change["target"],
            "strength": change["new_strength"],
            "joint_sentiment": change["new_joint_sentiment"],
        }

    return state


def build_entity_history(events: list[dict[str, Any]], entity_id: str) -> list[dict[str, Any]]:
    history = []
    for event in events:
        for change in event.get("changes", {}).get("entity_changes", []):
            if change["id"] != entity_id:
                continue
            history.append(
                {
                    "time": event["event_time"].isoformat(),
                    "mentions": change["new_mentions"],
                    "sentiment": change["new_sentiment"],
                    "centrality": change.get("new_centrality", 0.0),
                    "article_id": event["article_id"],
                }
            )
    return history


def build_sentiment_trend(events: list[dict[str, Any]], interval: str) -> list[dict[str, Any]]:
    bucket_delta = parse_window(interval)
    if bucket_delta.total_seconds() <= 0:
        return []

    buckets: dict[datetime, list[float]] = defaultdict(list)
    for event in events:
        ts = event["event_time"]
        bucket_start_seconds = int(ts.timestamp() // bucket_delta.total_seconds()) * int(bucket_delta.total_seconds())
        bucket_start = datetime.fromtimestamp(bucket_start_seconds, tz=ts.tzinfo)
        for change in event.get("changes", {}).get("entity_changes", []):
            buckets[bucket_start].append(float(change["new_sentiment"]))

    out = []
    for bucket_start in sorted(buckets.keys()):
        values = buckets[bucket_start]
        if not values:
            continue
        out.append(
            {
                "bucket_start": bucket_start.isoformat(),
                "avg_sentiment": sum(values) / len(values),
                "samples": len(values),
            }
        )
    return out
