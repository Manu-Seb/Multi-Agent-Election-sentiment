from __future__ import annotations

import json
import threading
from typing import Callable

from confluent_kafka import Consumer


class KafkaChangeConsumer:
    """Consumes graph-changes in partition order; article-keyed partitioning preserves per-article ordering."""

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        group_id: str,
        topic: str,
        on_event: Callable[[dict], None],
    ) -> None:
        self._consumer = Consumer(
            {
                "bootstrap.servers": bootstrap_servers,
                "group.id": group_id,
                "auto.offset.reset": "latest",
                "enable.auto.commit": True,
            }
        )
        self._topic = topic
        self._on_event = on_event
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        self._consumer.subscribe([self._topic])
        while not self._stop.is_set():
            msg = self._consumer.poll(1.0)
            if msg is None or msg.error():
                continue
            try:
                event = json.loads((msg.value() or b"{}").decode("utf-8"))
                self._on_event(event)
            except Exception:
                continue
        self._consumer.close()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
