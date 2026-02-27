from __future__ import annotations

import json
import logging
import signal

from confluent_kafka import Consumer, Producer

from entity_graph.wire_models import EntitySentimentResultValue

from .config import load_algorithm_config, load_runtime_config
from .engine import GraphAlgorithmsEngine


class GraphAlgorithmsService:
    def __init__(self) -> None:
        algo_cfg = load_algorithm_config()
        runtime_cfg = load_runtime_config()

        self.logger = logging.getLogger("graph-algorithms")
        self.engine = GraphAlgorithmsEngine(algo_cfg)
        self.running = True

        self.consumer = Consumer(
            {
                "bootstrap.servers": runtime_cfg.bootstrap_servers,
                "group.id": runtime_cfg.group_id,
                "auto.offset.reset": runtime_cfg.auto_offset_reset,
                "enable.auto.commit": True,
            }
        )
        self.consumer.subscribe([algo_cfg.entity_sentiment_topic])

        self.metrics_topic = algo_cfg.graph_metrics_topic
        self.producer = Producer({"bootstrap.servers": runtime_cfg.bootstrap_servers})

    def _handle_signal(self, signum, _frame) -> None:
        self.logger.info("signal=%s received, stopping", signum)
        self.running = False

    def run(self) -> None:
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        self.logger.info("graph algorithms service started")
        try:
            while self.running:
                msg = self.consumer.poll(1.0)
                if msg is None or msg.error():
                    continue

                try:
                    value = EntitySentimentResultValue.model_validate_json(msg.value().decode("utf-8"))
                    result, processed_at, _ = value.to_domain()
                    snap = self.engine.apply_result(result, processed_at_ms=int(processed_at.timestamp() * 1000))
                    payload = self.engine.build_metrics_payload(snap)
                    self.producer.produce(
                        topic=self.metrics_topic,
                        key=result.article_id.encode("utf-8"),
                        value=json.dumps(payload).encode("utf-8"),
                    )
                    self.producer.poll(0)
                except Exception:
                    self.logger.exception("failed to compute/publish graph metrics")
        finally:
            self.consumer.close()
            self.producer.flush(5)
            self.logger.info("graph algorithms service stopped")
