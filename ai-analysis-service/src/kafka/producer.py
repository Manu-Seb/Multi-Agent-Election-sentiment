from __future__ import annotations

import json
import os
from typing import Any

from confluent_kafka import Producer, TopicPartition

from entity_graph.wire_models import EntitySentimentResultValue


class EntitySentimentProducer:
    def __init__(self) -> None:
        bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        self.output_topic = os.getenv("ENTITY_SENTIMENT_TOPIC", "entity-sentiment")
        self.dlq_topic = os.getenv("ENTITY_SENTIMENT_DLQ_TOPIC", "entity-sentiment-dlq")
        self.producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers,
                "enable.idempotence": True,
                "acks": "all",
                "transactional.id": os.getenv(
                    "KAFKA_TRANSACTIONAL_ID", "entity-sentiment-processor-tx-1"
                ),
            }
        )
        self.producer.init_transactions()

    def begin_transaction(self) -> None:
        self.producer.begin_transaction()

    def publish_result(self, key: str, payload: EntitySentimentResultValue) -> None:
        self.producer.produce(
            topic=self.output_topic,
            key=key.encode("utf-8"),
            value=payload.model_dump_json().encode("utf-8"),
        )

    def publish_dlq(self, key: str, payload: dict[str, Any]) -> None:
        self.producer.produce(
            topic=self.dlq_topic,
            key=key.encode("utf-8"),
            value=json.dumps(payload).encode("utf-8"),
        )

    def send_offsets_to_transaction(self, offsets: list[TopicPartition], group_metadata) -> None:
        self.producer.send_offsets_to_transaction(offsets, group_metadata)

    def commit_transaction(self) -> None:
        self.producer.commit_transaction()

    def abort_transaction(self) -> None:
        self.producer.abort_transaction()

    def close(self, timeout: float = 5.0) -> None:
        self.producer.flush(timeout)
