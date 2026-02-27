from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import models


def datetime_to_epoch_millis(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def epoch_millis_to_datetime(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArticleValue(WireModel):
    id: str
    source_url: str
    content: str
    published_at: int = Field(description="timestamp-millis")
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, article: models.Article) -> "ArticleValue":
        return cls(
            id=article.id,
            source_url=article.source_url,
            content=article.content,
            published_at=datetime_to_epoch_millis(article.published_at),
            raw_metadata=article.raw_metadata,
        )

    def to_domain(self) -> models.Article:
        return models.Article(
            id=self.id,
            source_url=self.source_url,
            content=self.content,
            published_at=epoch_millis_to_datetime(self.published_at),
            raw_metadata=self.raw_metadata,
        )


class EntityMentionValue(WireModel):
    text: str
    type: str
    sentence: str
    sentiment_score: float
    confidence: float
    char_start: int
    char_end: int

    @classmethod
    def from_domain(cls, entity: models.EntityMention) -> "EntityMentionValue":
        return cls(
            text=entity.text,
            type=entity.type,
            sentence=entity.sentence,
            sentiment_score=entity.sentiment_score,
            confidence=entity.confidence,
            char_start=entity.char_start,
            char_end=entity.char_end,
        )

    def to_domain(self) -> models.EntityMention:
        return models.EntityMention(**self.model_dump())


class CoOccurrencePairValue(WireModel):
    entity_1: str
    entity_2: str
    count: int

    @classmethod
    def from_domain(cls, pair: models.CoOccurrencePair) -> "CoOccurrencePairValue":
        return cls(entity_1=pair.entity_1, entity_2=pair.entity_2, count=pair.count)

    def to_domain(self) -> models.CoOccurrencePair:
        return models.CoOccurrencePair(**self.model_dump())


class ProcessingMetadataValue(WireModel):
    processing_time_ms: int
    model_versions: dict[str, str] = Field(default_factory=dict)


class EntitySentimentResultValue(WireModel):
    article_id: str
    processed_at: int = Field(description="timestamp-millis")
    entities: list[EntityMentionValue]
    co_occurrence_pairs: list[CoOccurrencePairValue] = Field(default_factory=list)
    processing_metadata: ProcessingMetadataValue

    @classmethod
    def from_domain(
        cls,
        result: models.EntitySentimentResult,
        processed_at: datetime,
        processing_time_ms: int,
        model_versions: dict[str, str],
    ) -> "EntitySentimentResultValue":
        return cls(
            article_id=result.article_id,
            processed_at=datetime_to_epoch_millis(processed_at),
            entities=[EntityMentionValue.from_domain(item) for item in result.entities],
            co_occurrence_pairs=[CoOccurrencePairValue.from_domain(item) for item in result.co_occurrence_pairs],
            processing_metadata=ProcessingMetadataValue(
                processing_time_ms=processing_time_ms,
                model_versions=model_versions,
            ),
        )

    def to_domain(self) -> tuple[models.EntitySentimentResult, datetime, ProcessingMetadataValue]:
        return (
            models.EntitySentimentResult(
                article_id=self.article_id,
                entities=[item.to_domain() for item in self.entities],
                co_occurrence_pairs=[item.to_domain() for item in self.co_occurrence_pairs],
            ),
            epoch_millis_to_datetime(self.processed_at),
            self.processing_metadata,
        )


class EntityChangeValue(WireModel):
    entity: str
    type: str
    delta_sentiment: float
    mention_delta: int
    change_type: str

    @classmethod
    def from_domain(cls, change: models.EntityChange) -> "EntityChangeValue":
        return cls(
            entity=change.entity,
            type=change.type,
            delta_sentiment=change.delta_sentiment,
            mention_delta=change.mention_delta,
            change_type=change.change_type,
        )

    def to_domain(self) -> models.EntityChange:
        return models.EntityChange(**self.model_dump())


class RelationshipChangeValue(WireModel):
    source_entity: str
    target_entity: str
    delta_weight: float
    co_occurrence_delta: int
    change_type: str

    @classmethod
    def from_domain(cls, change: models.RelationshipChange) -> "RelationshipChangeValue":
        return cls(
            source_entity=change.source_entity,
            target_entity=change.target_entity,
            delta_weight=change.delta_weight,
            co_occurrence_delta=change.co_occurrence_delta,
            change_type=change.change_type,
        )

    def to_domain(self) -> models.RelationshipChange:
        return models.RelationshipChange(**self.model_dump())


class GraphChangeEventValue(WireModel):
    timestamp: int = Field(description="timestamp-millis")
    event_id: str
    article_id: str
    entity_changes: list[EntityChangeValue] = Field(default_factory=list)
    relationship_changes: list[RelationshipChangeValue] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, event: models.GraphChangeEvent) -> "GraphChangeEventValue":
        return cls(
            timestamp=datetime_to_epoch_millis(event.timestamp),
            event_id=event.event_id,
            article_id=event.article_id,
            entity_changes=[EntityChangeValue.from_domain(item) for item in event.entity_changes],
            relationship_changes=[RelationshipChangeValue.from_domain(item) for item in event.relationship_changes],
        )

    def to_domain(self) -> models.GraphChangeEvent:
        return models.GraphChangeEvent(
            timestamp=epoch_millis_to_datetime(self.timestamp),
            event_id=self.event_id,
            article_id=self.article_id,
            entity_changes=[item.to_domain() for item in self.entity_changes],
            relationship_changes=[item.to_domain() for item in self.relationship_changes],
        )


class GraphNodeValue(WireModel):
    entity: str
    type: str
    sentiment_avg: float
    mention_count: int
    last_seen_at: int = Field(description="timestamp-millis")

    @classmethod
    def from_domain(cls, node: models.GraphNode) -> "GraphNodeValue":
        return cls(
            entity=node.entity,
            type=node.type,
            sentiment_avg=node.sentiment_avg,
            mention_count=node.mention_count,
            last_seen_at=datetime_to_epoch_millis(node.last_seen_at),
        )

    def to_domain(self) -> models.GraphNode:
        return models.GraphNode(
            entity=self.entity,
            type=self.type,
            sentiment_avg=self.sentiment_avg,
            mention_count=self.mention_count,
            last_seen_at=epoch_millis_to_datetime(self.last_seen_at),
        )


class GraphEdgeValue(WireModel):
    source_entity: str
    target_entity: str
    weight: float
    co_occurrence_count: int
    last_updated_at: int = Field(description="timestamp-millis")

    @classmethod
    def from_domain(cls, edge: models.GraphEdge) -> "GraphEdgeValue":
        return cls(
            source_entity=edge.source_entity,
            target_entity=edge.target_entity,
            weight=edge.weight,
            co_occurrence_count=edge.co_occurrence_count,
            last_updated_at=datetime_to_epoch_millis(edge.last_updated_at),
        )

    def to_domain(self) -> models.GraphEdge:
        return models.GraphEdge(
            source_entity=self.source_entity,
            target_entity=self.target_entity,
            weight=self.weight,
            co_occurrence_count=self.co_occurrence_count,
            last_updated_at=epoch_millis_to_datetime(self.last_updated_at),
        )


class GraphSnapshotValue(WireModel):
    timestamp: int = Field(description="timestamp-millis")
    last_event_id: str
    nodes: list[GraphNodeValue] = Field(default_factory=list)
    edges: list[GraphEdgeValue] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, snapshot: models.GraphSnapshot) -> "GraphSnapshotValue":
        return cls(
            timestamp=datetime_to_epoch_millis(snapshot.timestamp),
            last_event_id=snapshot.last_event_id,
            nodes=[GraphNodeValue.from_domain(item) for item in snapshot.nodes],
            edges=[GraphEdgeValue.from_domain(item) for item in snapshot.edges],
        )

    def to_domain(self) -> models.GraphSnapshot:
        return models.GraphSnapshot(
            timestamp=epoch_millis_to_datetime(self.timestamp),
            last_event_id=self.last_event_id,
            nodes=[item.to_domain() for item in self.nodes],
            edges=[item.to_domain() for item in self.edges],
        )
