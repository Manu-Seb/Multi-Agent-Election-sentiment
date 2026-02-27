from __future__ import annotations

import random
import time

from entity_graph.models import CoOccurrencePair, EntityMention, EntitySentimentResult
from graph_algorithms.config import AlgorithmConfig
from graph_algorithms.engine import GraphAlgorithmsEngine


def make_engine() -> GraphAlgorithmsEngine:
    cfg = AlgorithmConfig(
        pagerank_damping=0.85,
        pagerank_iterations=20,
        pagerank_hops=2,
        community_interval=100,
        betweenness_enabled=True,
        betweenness_samples=16,
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


def make_result(i: int, names: list[str]) -> EntitySentimentResult:
    picked = random.sample(names, 4)
    entities = [
        EntityMention(
            text=name,
            type="PERSON",
            sentence=f"{name} appears in article {i}",
            sentiment_score=random.uniform(-1.0, 1.0),
            confidence=0.9,
            char_start=0,
            char_end=len(name),
        )
        for name in picked
    ]
    pairs = [CoOccurrencePair(picked[a], picked[b], 1) for a in range(4) for b in range(a + 1, 4)]
    return EntitySentimentResult(article_id=f"bench-{i}", entities=entities, co_occurrence_pairs=pairs)


def main(iterations: int = 3000) -> None:
    random.seed(42)
    engine = make_engine()
    names = [f"Entity-{i}" for i in range(800)]

    start = time.perf_counter()
    now_ms = int(time.time() * 1000)
    for i in range(iterations):
        snap = engine.apply_result(make_result(i, names), now_ms + i * 1000)
    elapsed = time.perf_counter() - start

    print(f"iterations={iterations}")
    print(f"elapsed_sec={elapsed:.4f}")
    print(f"updates_per_second={iterations/elapsed:.2f}")
    print(f"node_count={snap.node_count}")
    print(f"edge_count={snap.edge_count}")


if __name__ == "__main__":
    main()
