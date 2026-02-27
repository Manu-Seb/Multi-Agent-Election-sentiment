from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from confluent_kafka import Consumer, Producer

from entity_graph.wire_models import EntitySentimentResultValue

from .store import InMemoryGraphStore


class GraphStreamWorker:
    def __init__(self, store: InMemoryGraphStore) -> None:
        self._store = store
        self._logger = logging.getLogger("graph-state-worker")
        self._running = False
        self._task: asyncio.Task | None = None

        bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        self._consumer = Consumer(
            {
                "bootstrap.servers": bootstrap_servers,
                "group.id": os.getenv("GRAPH_STATE_GROUP_ID", "graph-state-service"),
                "auto.offset.reset": os.getenv("KAFKA_AUTO_OFFSET_RESET", "latest"),
                "enable.auto.commit": True,
            }
        )
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})

        self._topic = os.getenv("ENTITY_SENTIMENT_TOPIC", "entity-sentiment")
        self._changes_topic = os.getenv("GRAPH_CHANGES_TOPIC", "graph-changes")
        self._schema_version = int(os.getenv("GRAPH_CHANGES_SCHEMA_VERSION", "1"))
        self._batch_size = int(os.getenv("GRAPH_STATE_BATCH_SIZE", "16"))

    async def _consume_loop(self) -> None:
        self._consumer.subscribe([self._topic])
        self._logger.info("graph stream worker subscribed topic=%s", self._topic)

        while self._running:
            messages = self._consumer.consume(num_messages=self._batch_size, timeout=1.0)
            valid = [msg for msg in messages if msg is not None and msg.error() is None]
            if not valid:
                await asyncio.sleep(0)
                continue

            for msg in valid:
                try:
                    value = EntitySentimentResultValue.model_validate_json(msg.value().decode("utf-8"))
                    result, processed_at, metadata = value.to_domain()

                    change_event = await self._store.apply_result(
                        result=result,
                        observed_at=processed_at,
                        extraction_ms=metadata.processing_time_ms,
                        schema_version=self._schema_version,
                    )

                    self._producer.produce(
                        topic=self._changes_topic,
                        key=result.article_id.encode("utf-8"),
                        value=json.dumps(change_event).encode("utf-8"),
                    )
                    self._producer.poll(0)
                except Exception:
                    self._logger.exception("failed to process graph stream message")

        self._producer.flush(5)
        self._consumer.close()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._consume_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            await self._task
            self._task = None
