from __future__ import annotations

import os

from confluent_kafka import Consumer, Message


class RawArticlesConsumer:
    def __init__(self) -> None:
        self.topic = os.getenv("RAW_ARTICLES_TOPIC", "raw-articles")
        self.consumer = Consumer(
            {
                "bootstrap.servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
                "group.id": os.getenv("KAFKA_GROUP_ID", "entity-sentiment-processor"),
                "auto.offset.reset": os.getenv("KAFKA_AUTO_OFFSET_RESET", "earliest"),
                "enable.auto.commit": False,
                "enable.auto.offset.store": False,
                "isolation.level": "read_committed",
            }
        )
        self.consumer.subscribe([self.topic])

    def poll_batch(self, batch_size: int, timeout: float) -> list[Message]:
        messages = self.consumer.consume(num_messages=batch_size, timeout=timeout)
        return [msg for msg in messages if msg is not None and msg.error() is None]

    def consumer_group_metadata(self):
        return self.consumer.consumer_group_metadata()

    def close(self) -> None:
        self.consumer.close()
