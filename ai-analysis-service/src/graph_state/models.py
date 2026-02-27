from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Node:
    id: str
    type: str
    mention_count: int
    sentiment: float
    centrality: float
    first_seen: datetime
    last_seen: datetime
    hourly_rate: float


@dataclass(slots=True)
class Edge:
    source: str
    target: str
    strength: float
    joint_sentiment: float
    first_seen: datetime
    last_strengthened: datetime


@dataclass(slots=True)
class GraphState:
    nodes: dict[str, Node]
    edges: dict[tuple[str, str], Edge]


def serialize_node(node: Node) -> dict[str, Any]:
    payload = asdict(node)
    payload["first_seen"] = int(node.first_seen.timestamp() * 1000)
    payload["last_seen"] = int(node.last_seen.timestamp() * 1000)
    return payload


def serialize_edge(edge: Edge) -> dict[str, Any]:
    payload = asdict(edge)
    payload["first_seen"] = int(edge.first_seen.timestamp() * 1000)
    payload["last_strengthened"] = int(edge.last_strengthened.timestamp() * 1000)
    return payload
