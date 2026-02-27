from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Article:
    id: str
    source_url: str
    content: str
    published_at: datetime
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EntityMention:
    text: str
    type: str
    sentence: str
    sentiment_score: float
    confidence: float
    char_start: int
    char_end: int


@dataclass(slots=True)
class CoOccurrencePair:
    entity_1: str
    entity_2: str
    count: int


@dataclass(slots=True)
class EntitySentimentResult:
    article_id: str
    entities: list[EntityMention]
    co_occurrence_pairs: list[CoOccurrencePair] = field(default_factory=list)


@dataclass(slots=True)
class EntityChange:
    entity: str
    type: str
    delta_sentiment: float
    mention_delta: int
    change_type: str


@dataclass(slots=True)
class RelationshipChange:
    source_entity: str
    target_entity: str
    delta_weight: float
    co_occurrence_delta: int
    change_type: str


@dataclass(slots=True)
class GraphChangeEvent:
    timestamp: datetime
    event_id: str
    article_id: str
    entity_changes: list[EntityChange] = field(default_factory=list)
    relationship_changes: list[RelationshipChange] = field(default_factory=list)


@dataclass(slots=True)
class GraphNode:
    entity: str
    type: str
    sentiment_avg: float
    mention_count: int
    last_seen_at: datetime


@dataclass(slots=True)
class GraphEdge:
    source_entity: str
    target_entity: str
    weight: float
    co_occurrence_count: int
    last_updated_at: datetime


@dataclass(slots=True)
class GraphSnapshot:
    timestamp: datetime
    last_event_id: str
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
