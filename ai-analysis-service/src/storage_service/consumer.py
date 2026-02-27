from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone

from confluent_kafka import Consumer, Producer

from .config import StorageConfig
from .db import StorageRepository
from .metrics import STORAGE_LAG_SECONDS, STORED_CHANGE_EVENTS
from .snapshot_manager import SnapshotManager


class StorageConsumerWorker:
    def __init__(self, cfg: StorageConfig, repo: StorageRepository, snapshot_mgr: SnapshotManager) -> None:
        self._cfg = cfg
        self._repo = repo
        self._snapshot_mgr = snapshot_mgr
        self._logger = logging.getLogger("graph-storage-consumer")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._consumer = Consumer(
            {
                "bootstrap.servers": cfg.kafka_bootstrap_servers,
                "group.id": cfg.kafka_group_id,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )
        self._producer = Producer({"bootstrap.servers": cfg.kafka_bootstrap_servers})
        self._batch_size = int(os.getenv("GRAPH_STORAGE_BATCH_SIZE", "32"))
        self._snapshot_mgr.set_snapshot_publisher(self._publish_snapshot)

    @staticmethod
    def _event_time_from_ms(value: int) -> datetime:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)

    def _handle_change_message(self, payload: dict) -> None:
        event_time = self._event_time_from_ms(int(payload["timestamp"]))
        lag_seconds = (datetime.now(tz=timezone.utc) - event_time).total_seconds()
        STORAGE_LAG_SECONDS.set(max(0.0, lag_seconds))

        article_id = str(payload["article_id"])
        event_id = self._repo.store_change_event(event_time=event_time, article_id=article_id, changes=payload)
        self._snapshot_mgr.on_change_event(event_id=event_id, event_time=event_time, changes=payload)
        STORED_CHANGE_EVENTS.inc()

    def _handle_snapshot_message(self, payload: dict) -> None:
        if payload.get("source_instance") == self._cfg.storage_instance_id:
            return
        snapshot_time = self._event_time_from_ms(int(payload["timestamp"]))
        graph_state = payload.get("graph_state", {})
        last_event_id = payload.get("last_event_id")
        self._snapshot_mgr.on_external_snapshot(
            snapshot_time=snapshot_time,
            last_event_id=int(last_event_id) if last_event_id is not None else None,
            graph_state=graph_state,
        )

    def _publish_snapshot(self, *, snapshot_time: datetime, last_event_id: int | None, graph_state: dict) -> None:
        payload = {
            "timestamp": int(snapshot_time.timestamp() * 1000),
            "last_event_id": last_event_id,
            "graph_state": graph_state,
            "source_instance": self._cfg.storage_instance_id,
        }
        self._producer.produce(
            topic=self._cfg.snapshots_topic,
            key=(str(last_event_id) if last_event_id is not None else "none").encode("utf-8"),
            value=json.dumps(payload).encode("utf-8"),
        )
        self._producer.poll(0)

    def _run(self) -> None:
        self._consumer.subscribe([self._cfg.changes_topic, self._cfg.snapshots_topic])
        self._logger.info(
            "storage consumer started topics=%s,%s group=%s",
            self._cfg.changes_topic,
            self._cfg.snapshots_topic,
            self._cfg.kafka_group_id,
        )

        while not self._stop.is_set():
            batch = self._consumer.consume(num_messages=self._batch_size, timeout=1.0)
            messages = [msg for msg in batch if msg is not None and msg.error() is None]
            if not messages:
                continue

            for msg in messages:
                try:
                    payload = json.loads((msg.value() or b"{}").decode("utf-8"))
                    topic = msg.topic()

                    if topic == self._cfg.changes_topic:
                        self._handle_change_message(payload)
                    elif topic == self._cfg.snapshots_topic:
                        self._handle_snapshot_message(payload)

                    # Commit only after successful storage.
                    self._consumer.commit(message=msg, asynchronous=False)
                except Exception:
                    self._logger.exception("failed to store message from topic=%s", msg.topic())

        self._consumer.close()
        self._producer.flush(5)
        self._logger.info("storage consumer stopped")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="graph-storage-consumer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
