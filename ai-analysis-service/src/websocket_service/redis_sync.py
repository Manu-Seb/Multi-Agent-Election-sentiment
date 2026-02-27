from __future__ import annotations

import json
import threading
from typing import Callable


class RedisBroadcaster:
    def __init__(self, enabled: bool, redis_url: str, channel: str) -> None:
        self.enabled = enabled
        self.redis_url = redis_url
        self.channel = channel
        self._client = None
        self._pubsub = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        if self.enabled:
            try:
                import redis

                self._client = redis.from_url(redis_url)
                self._pubsub = self._client.pubsub(ignore_subscribe_messages=True)
            except Exception:  # noqa: BLE001
                self.enabled = False

    def publish(self, event: dict) -> None:
        if not self.enabled or not self._client:
            return
        self._client.publish(self.channel, json.dumps(event))

    def start_subscriber(self, on_event: Callable[[dict], None]) -> None:
        if not self.enabled or not self._pubsub:
            return
        self._pubsub.subscribe(self.channel)

        def run() -> None:
            while not self._stop.is_set():
                msg = self._pubsub.get_message(timeout=1.0)
                if not msg or msg.get("type") != "message":
                    continue
                try:
                    data = json.loads(msg["data"])
                    on_event(data)
                except Exception:
                    continue

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self._pubsub:
            self._pubsub.close()
