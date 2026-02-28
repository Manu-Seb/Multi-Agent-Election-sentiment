from __future__ import annotations

import json
import logging
import signal
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from confluent_kafka import Message, TopicPartition

from entity_graph.models import EntitySentimentResult
from entity_graph.wire_models import ArticleValue, EntitySentimentResultValue
from kafka.consumer import RawArticlesConsumer
from kafka.producer import EntitySentimentProducer
from processors.co_occurrence import detect_co_occurrence_pairs
from processors.entity_extractor import EntityExtractor
from processors.sentiment_analyzer import SentimentAnalyzer


@dataclass(slots=True)
class RuntimeConfig:
    batch_size: int
    poll_timeout_seconds: float
    metrics_interval_seconds: float
    headline_filter_terms: tuple[str, ...]


def load_config() -> RuntimeConfig:
    import os

    raw_terms = os.getenv("PROCESSOR_HEADLINE_FILTER_TERMS", "")
    terms = tuple(
        token.strip().lower()
        for token in raw_terms.split(",")
        if token.strip()
    )

    return RuntimeConfig(
        batch_size=int(os.getenv("PROCESSOR_BATCH_SIZE", "25")),
        poll_timeout_seconds=float(os.getenv("PROCESSOR_POLL_TIMEOUT_SECONDS", "1.0")),
        metrics_interval_seconds=float(os.getenv("PROCESSOR_METRICS_INTERVAL_SECONDS", "10.0")),
        headline_filter_terms=terms,
    )


class MetricsTracker:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self.window_start = time.time()
        self.processed_ok = 0
        self.failed = 0
        self.skipped = 0
        self.total_latency_ms = 0.0

    def record_success(self, latency_ms: float) -> None:
        self.processed_ok += 1
        self.total_latency_ms += latency_ms

    def record_failure(self) -> None:
        self.failed += 1

    def record_skipped(self) -> None:
        self.skipped += 1

    def maybe_emit(self, logger: logging.Logger) -> None:
        now = time.time()
        elapsed = now - self.window_start
        if elapsed < self.interval_seconds:
            return

        articles_per_second = self.processed_ok / elapsed if elapsed > 0 else 0.0
        avg_latency = self.total_latency_ms / self.processed_ok if self.processed_ok > 0 else 0.0
        logger.info(
            "metrics articles_per_second=%.3f avg_latency_ms=%.2f success=%d failures=%d skipped=%d window_sec=%.2f",
            articles_per_second,
            avg_latency,
            self.processed_ok,
            self.failed,
            self.skipped,
            elapsed,
        )

        self.window_start = now
        self.processed_ok = 0
        self.failed = 0
        self.skipped = 0
        self.total_latency_ms = 0.0


class EntitySentimentStreamApp:
    def __init__(self) -> None:
        self.config = load_config()
        self.logger = logging.getLogger("entity-sentiment-stream")
        self.running = True

        self.extractor = EntityExtractor()
        self.sentiment = SentimentAnalyzer()
        self.consumer = RawArticlesConsumer()
        self.producer = EntitySentimentProducer()
        self.metrics = MetricsTracker(self.config.metrics_interval_seconds)
        if self.config.headline_filter_terms:
            self.logger.info(
                "headline filter enabled terms=%s",
                ",".join(self.config.headline_filter_terms),
            )
        else:
            self.logger.info("headline filter disabled; all articles will be analyzed")

    def _shutdown_handler(self, signum: int, _frame: Any) -> None:
        self.logger.info("received signal=%d; shutting down", signum)
        self.running = False

    @staticmethod
    def _build_commit_offsets(messages: list[Message]) -> list[TopicPartition]:
        partition_highwater: dict[tuple[str, int], int] = {}
        for msg in messages:
            key = (msg.topic(), msg.partition())
            next_offset = msg.offset() + 1
            partition_highwater[key] = max(next_offset, partition_highwater.get(key, next_offset))

        return [
            TopicPartition(topic, partition, offset)
            for (topic, partition), offset in partition_highwater.items()
        ]

    def _process_article(self, article: ArticleValue) -> EntitySentimentResultValue:
        started = time.perf_counter()
        domain_article = article.to_domain()

        entities, sentence_indices, sentence_texts = self.extractor.extract(domain_article.content)

        unique_sentence_indices = sorted(set(sentence_indices))
        sentiment_inputs = [sentence_texts[idx] for idx in unique_sentence_indices if idx < len(sentence_texts)]
        sentiment_scores = self.sentiment.score_batch(sentiment_inputs)
        sentence_to_sentiment = {
            idx: score
            for idx, score in zip(unique_sentence_indices, sentiment_scores)
        }

        for mention, sentence_idx in zip(entities, sentence_indices):
            score, confidence = sentence_to_sentiment.get(sentence_idx, (0.0, 0.0))
            mention.sentiment_score = score
            mention.confidence = confidence

        co_occurrence = detect_co_occurrence_pairs(
            entities=entities,
            sentence_indices=sentence_indices,
            adjacency_window=1,
        )

        result = EntitySentimentResult(
            article_id=domain_article.id,
            entities=entities,
            co_occurrence_pairs=co_occurrence,
        )

        latency_ms = int((time.perf_counter() - started) * 1000)
        payload = EntitySentimentResultValue.from_domain(
            result=result,
            processed_at=datetime.now(tz=timezone.utc),
            processing_time_ms=latency_ms,
            model_versions={
                "spacy": self.extractor.model_name,
                "distilbert": self.sentiment.model_name,
            },
        )
        self.metrics.record_success(latency_ms=latency_ms)
        return payload

    @staticmethod
    def _safe_article_id(message: Message) -> str:
        try:
            raw = json.loads((message.value() or b"{}").decode("utf-8"))
            return str(raw.get("id", "unknown"))
        except Exception:
            return "unknown"

    @staticmethod
    def _parse_published_at(value: Any) -> int:
        if isinstance(value, int):
            if value > 1_000_000_000_000:
                return value
            return value * 1000
        if isinstance(value, float):
            return int(value * 1000)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        return int(time.time() * 1000)

    def _parse_article_value(self, message: Message) -> ArticleValue:
        raw_dict = json.loads((message.value() or b"{}").decode("utf-8"))

        try:
            return ArticleValue.model_validate(raw_dict)
        except Exception:
            # Backward-compat for current ingestion-service payload shape:
            # {id,title,content,link,feed_id,feed_title,published_at,author,tags,source}
            normalized = {
                "id": str(raw_dict.get("id", "unknown")),
                "source_url": raw_dict.get("source_url") or raw_dict.get("link") or "",
                "content": raw_dict.get("content", ""),
                "published_at": self._parse_published_at(raw_dict.get("published_at")),
                "raw_metadata": {
                    "title": raw_dict.get("title", ""),
                    "feed_id": raw_dict.get("feed_id"),
                    "feed_title": raw_dict.get("feed_title"),
                    "author": raw_dict.get("author"),
                    "tags": raw_dict.get("tags", []),
                    "source": raw_dict.get("source"),
                },
            }
            return ArticleValue.model_validate(normalized)

    def _headline_accepted(self, article: ArticleValue) -> bool:
        if not self.config.headline_filter_terms:
            return True
        title = str(article.raw_metadata.get("title", "")).lower()
        return any(term in title for term in self.config.headline_filter_terms)

    def _publish_dlq(self, message: Message, error: Exception) -> None:
        article_id = self._safe_article_id(message)
        self.logger.exception(
            "record failed article_id=%s topic=%s partition=%s offset=%s",
            article_id,
            message.topic(),
            message.partition(),
            message.offset(),
        )
        dlq_payload = {
            "failed_at": int(time.time() * 1000),
            "source_topic": message.topic(),
            "source_partition": message.partition(),
            "source_offset": message.offset(),
            "article_id": article_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "stack_trace": traceback.format_exc(),
            "raw_value": (message.value() or b"").decode("utf-8", errors="replace"),
        }
        self.producer.publish_dlq(key=article_id, payload=dlq_payload)
        self.metrics.record_failure()

    def run(self) -> None:
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

        self.logger.info("entity sentiment stream started")

        try:
            while self.running:
                batch = self.consumer.poll_batch(
                    batch_size=self.config.batch_size,
                    timeout=self.config.poll_timeout_seconds,
                )
                if not batch:
                    self.metrics.maybe_emit(self.logger)
                    continue

                self.producer.begin_transaction()
                try:
                    for message in batch:
                        try:
                            article = self._parse_article_value(message)
                            if not self._headline_accepted(article):
                                self.metrics.record_skipped()
                                continue
                            result_payload = self._process_article(article)
                            self.producer.publish_result(
                                key=result_payload.article_id,
                                payload=result_payload,
                            )
                        except Exception as item_error:
                            self._publish_dlq(message, item_error)

                    offsets = self._build_commit_offsets(batch)
                    self.producer.send_offsets_to_transaction(
                        offsets=offsets,
                        group_metadata=self.consumer.consumer_group_metadata(),
                    )
                    self.producer.commit_transaction()
                except Exception:
                    self.producer.abort_transaction()
                    raise

                self.metrics.maybe_emit(self.logger)
        finally:
            self.consumer.close()
            self.producer.close()
            self.logger.info("entity sentiment stream stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = EntitySentimentStreamApp()
    app.run()


if __name__ == "__main__":
    main()
