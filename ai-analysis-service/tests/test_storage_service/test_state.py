from __future__ import annotations

from datetime import datetime, timezone

from storage_service.state import (
    ReconstructionCache,
    apply_change_event,
    build_entity_history,
    build_sentiment_trend,
    empty_graph_state,
)


def _sample_change() -> dict:
    return {
        "timestamp": 1772201903528,
        "article_id": "a1",
        "entity_changes": [
            {
                "id": "Alice",
                "new_mentions": 2,
                "new_sentiment": 0.8,
                "new_centrality": 0.4,
            }
        ],
        "relationship_changes": [
            {
                "source": "Alice",
                "target": "Bob",
                "new_strength": 1.2,
                "new_joint_sentiment": 0.5,
            }
        ],
    }


def test_apply_change_event_updates_graph_state() -> None:
    state = empty_graph_state()
    out = apply_change_event(state, _sample_change())

    assert "Alice" in out["nodes"]
    assert out["nodes"]["Alice"]["mention_count"] == 2
    assert out["edges"]["Alice::Bob"]["strength"] == 1.2


def test_reconstruction_cache() -> None:
    cache = ReconstructionCache(ttl_seconds=60, max_size=2)
    cache.set("k1", {"ok": 1})
    assert cache.get("k1") == {"ok": 1}


def test_history_and_trend() -> None:
    now = datetime.now(tz=timezone.utc)
    events = [
        {
            "event_time": now,
            "article_id": "x",
            "changes": {
                "entity_changes": [{"id": "Alice", "new_mentions": 1, "new_sentiment": 0.5, "new_centrality": 0.1}]
            },
        },
        {
            "event_time": now,
            "article_id": "y",
            "changes": {
                "entity_changes": [{"id": "Alice", "new_mentions": 2, "new_sentiment": 0.7, "new_centrality": 0.2}]
            },
        },
    ]

    history = build_entity_history(events, "Alice")
    assert len(history) == 2

    trend = build_sentiment_trend(events, "1h")
    assert len(trend) == 1
    assert trend[0]["samples"] == 2
