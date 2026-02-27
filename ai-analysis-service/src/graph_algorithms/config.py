from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class AlgorithmConfig:
    pagerank_damping: float
    pagerank_iterations: int
    pagerank_hops: int
    community_interval: int
    betweenness_enabled: bool
    betweenness_samples: int
    proximity_same_sentence: float
    proximity_adjacent_sentence: float
    proximity_same_paragraph: float
    proximity_default_class: str
    recency_halflife_hours: float
    graph_metrics_topic: str
    entity_sentiment_topic: str
    visualization_enabled: bool
    visualization_interval: int
    visualization_dir: str


@dataclass(slots=True)
class RuntimeConfig:
    bootstrap_servers: str
    group_id: str
    auto_offset_reset: str


def load_algorithm_config() -> AlgorithmConfig:
    return AlgorithmConfig(
        pagerank_damping=float(os.getenv("ALGO_PAGERANK_DAMPING", "0.85")),
        pagerank_iterations=int(os.getenv("ALGO_PAGERANK_ITERATIONS", "20")),
        pagerank_hops=int(os.getenv("ALGO_PAGERANK_HOPS", "2")),
        community_interval=int(os.getenv("ALGO_COMMUNITY_INTERVAL", "100")),
        betweenness_enabled=os.getenv("ALGO_BETWEENNESS_ENABLED", "true").lower() == "true",
        betweenness_samples=int(os.getenv("ALGO_BETWEENNESS_SAMPLES", "16")),
        proximity_same_sentence=float(os.getenv("ALGO_PROXIMITY_SAME_SENTENCE", "1.0")),
        proximity_adjacent_sentence=float(os.getenv("ALGO_PROXIMITY_ADJACENT", "0.5")),
        proximity_same_paragraph=float(os.getenv("ALGO_PROXIMITY_SAME_PARAGRAPH", "0.1")),
        proximity_default_class=os.getenv("ALGO_PROXIMITY_DEFAULT_CLASS", "same_sentence"),
        recency_halflife_hours=float(os.getenv("ALGO_RECENCY_HALFLIFE_HOURS", "24")),
        graph_metrics_topic=os.getenv("GRAPH_METRICS_TOPIC", "graph-metrics"),
        entity_sentiment_topic=os.getenv("ENTITY_SENTIMENT_TOPIC", "entity-sentiment"),
        visualization_enabled=os.getenv("ALGO_VISUALIZATION_ENABLED", "true").lower() == "true",
        visualization_interval=int(os.getenv("ALGO_VISUALIZATION_INTERVAL", "100")),
        visualization_dir=os.getenv("ALGO_VISUALIZATION_DIR", "./logs/graph-debug"),
    )


def load_runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        group_id=os.getenv("GRAPH_ALGO_GROUP_ID", "graph-algorithms-service"),
        auto_offset_reset=os.getenv("KAFKA_AUTO_OFFSET_RESET", "latest"),
    )
