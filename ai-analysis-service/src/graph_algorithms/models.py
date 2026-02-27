from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class AlgoNode:
    node_id: str
    node_type: str
    sentiment: float
    pagerank: float = 0.0
    betweenness: float = 0.0
    community_id: int = -1
    mention_count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None


@dataclass(slots=True)
class AlgoEdge:
    source: str
    target: str
    strength: float
    joint_sentiment: float
    first_seen: datetime
    last_strengthened: datetime


@dataclass(slots=True)
class MetricsSnapshot:
    timestamp_ms: int
    updates_processed: int
    node_count: int
    edge_count: int
    touched_nodes: list[str]
    community_changes: list[dict[str, int]] = field(default_factory=list)
