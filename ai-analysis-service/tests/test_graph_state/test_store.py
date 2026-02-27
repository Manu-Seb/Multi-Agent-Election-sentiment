from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from entity_graph.models import CoOccurrencePair, EntityMention, EntitySentimentResult
from graph_state.store import InMemoryGraphStore


def _sample_result(article_id: str, sentiment: float) -> EntitySentimentResult:
    return EntitySentimentResult(
        article_id=article_id,
        entities=[
            EntityMention(
                text="Alice",
                type="PERSON",
                sentence="Alice met Bob.",
                sentiment_score=sentiment,
                confidence=0.9,
                char_start=0,
                char_end=5,
            ),
            EntityMention(
                text="Bob",
                type="PERSON",
                sentence="Alice met Bob.",
                sentiment_score=sentiment,
                confidence=0.9,
                char_start=10,
                char_end=13,
            ),
        ],
        co_occurrence_pairs=[CoOccurrencePair(entity_1="Alice", entity_2="Bob", count=1)],
    )


def test_graph_update_and_lookup() -> None:
    store = InMemoryGraphStore()
    now = datetime.now(tz=timezone.utc)

    change_1 = asyncio.run(store.apply_result(_sample_result("a1", 1.0), observed_at=now))
    assert change_1["node_count"] == 2
    assert change_1["edge_count"] == 1

    state = asyncio.run(store.get_current_state())
    assert state["node_count"] == 2
    assert state["edge_count"] == 1

    alice = asyncio.run(store.get_entity("Alice"))
    assert alice is not None
    assert alice["mention_count"] == 1
    assert alice["sentiment"] == 1.0

    change_2 = asyncio.run(store.apply_result(_sample_result("a2", -1.0), observed_at=now))
    assert change_2["node_count"] == 2

    alice_2 = asyncio.run(store.get_entity("Alice"))
    assert alice_2 is not None
    assert alice_2["mention_count"] == 2
    expected = (1.0 * 0.95) + (-1.0 * 0.05)
    assert abs(alice_2["sentiment"] - expected) < 1e-9

    edges = asyncio.run(store.get_edges_for_entity("Alice"))
    assert len(edges) == 1
    assert edges[0]["source"] in {"Alice", "Bob"}


def test_metrics_include_memory_usage() -> None:
    store = InMemoryGraphStore()
    metrics = asyncio.run(store.get_metrics())
    assert "memory_current_bytes" in metrics
    assert "memory_peak_bytes" in metrics
    assert "memory_rss_kb" in metrics


def test_change_event_contains_deltas_and_validation() -> None:
    store = InMemoryGraphStore()
    now = datetime.now(tz=timezone.utc)

    event = asyncio.run(
        store.apply_result(
            _sample_result("trace-1", 0.8),
            observed_at=now,
            extraction_ms=17,
            schema_version=3,
        )
    )

    assert event["schema_version"] == 3
    assert event["article_id"] == "trace-1"
    assert event["processing_metrics"]["extraction_ms"] == 17
    assert "graph_update_ms" in event["processing_metrics"]
    assert event["validation"]["passed"] is True
    assert len(event["entity_changes"]) == 2
    assert len(event["relationship_changes"]) == 1

    alice = next(item for item in event["entity_changes"] if item["id"] == "Alice")
    assert alice["old_mentions"] == 0
    assert alice["new_mentions"] == 1
    assert alice["delta_mentions"] == 1


def test_change_event_threshold_filters_tiny_sentiment_updates() -> None:
    store = InMemoryGraphStore()
    now = datetime.now(tz=timezone.utc)

    asyncio.run(store.apply_result(_sample_result("seed", 0.0), observed_at=now))
    event = asyncio.run(store.apply_result(_sample_result("tiny", 0.001), observed_at=now))

    # Mention count changes are still captured.
    assert len(event["entity_changes"]) == 2
    # Delta sentiment is tiny and should round near zero for display.
    alice = next(item for item in event["entity_changes"] if item["id"] == "Alice")
    assert abs(alice["delta_sentiment"]) < 0.01
