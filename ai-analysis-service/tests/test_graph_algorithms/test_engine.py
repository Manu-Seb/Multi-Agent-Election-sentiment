from __future__ import annotations

from graph_algorithms.config import AlgorithmConfig
from graph_algorithms.engine import GraphAlgorithmsEngine
from entity_graph.models import CoOccurrencePair, EntityMention, EntitySentimentResult


def make_engine() -> GraphAlgorithmsEngine:
    cfg = AlgorithmConfig(
        pagerank_damping=0.85,
        pagerank_iterations=20,
        pagerank_hops=2,
        community_interval=2,
        betweenness_enabled=True,
        betweenness_samples=4,
        proximity_same_sentence=1.0,
        proximity_adjacent_sentence=0.5,
        proximity_same_paragraph=0.1,
        proximity_default_class="same_sentence",
        recency_halflife_hours=24.0,
        graph_metrics_topic="graph-metrics",
        entity_sentiment_topic="entity-sentiment",
        visualization_enabled=False,
        visualization_interval=100,
        visualization_dir="./logs/graph-debug",
    )
    return GraphAlgorithmsEngine(cfg)


def sample_result(article_id: str, sentiment_a: float, sentiment_b: float) -> EntitySentimentResult:
    return EntitySentimentResult(
        article_id=article_id,
        entities=[
            EntityMention("Alice", "PERSON", "Alice met Bob.", sentiment_a, 0.9, 0, 5),
            EntityMention("Bob", "PERSON", "Alice met Bob.", sentiment_b, 0.9, 10, 13),
        ],
        co_occurrence_pairs=[CoOccurrencePair("Alice", "Bob", 1)],
    )


def test_relationship_strength_and_pagerank() -> None:
    engine = make_engine()

    s1 = engine.apply_result(sample_result("a1", 0.9, 0.8), 1_700_000_000_000)
    assert s1.node_count == 2
    assert s1.edge_count == 1

    edge = engine.edges[("Alice", "Bob")]
    assert edge.strength > 0

    assert engine.nodes["Alice"].pagerank > 0
    assert engine.nodes["Bob"].pagerank > 0


def test_community_changes() -> None:
    engine = make_engine()

    engine.apply_result(sample_result("a1", 0.1, 0.2), 1_700_000_000_000)
    snap = engine.apply_result(sample_result("a2", 0.2, 0.3), 1_700_000_100_000)
    assert snap.community_changes  # runs every 2 updates


def test_metrics_payload_shape() -> None:
    engine = make_engine()
    snap = engine.apply_result(sample_result("a1", 0.7, -0.4), 1_700_000_000_000)
    payload = engine.build_metrics_payload(snap)

    assert payload["node_count"] == 2
    assert payload["edge_count"] == 1
    assert "top_pagerank" in payload
    assert "top_bridge_nodes" in payload
