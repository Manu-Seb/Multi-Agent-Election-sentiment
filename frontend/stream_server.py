from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Iterator

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

GRAPH_STATE_API_URL = "http://localhost:8010"
REDPANDA_CONTAINER = "redpanda"
RAW_INGESTION_TOPIC = "raw_ingestion"
ENTITY_SENTIMENT_TOPIC = "entity-sentiment"
GRAPH_CHANGES_TOPIC = "graph-changes"
TOP_NODES = 10
TOP_EDGES = 10
ARTICLE_TIMEOUT_SECONDS = 45
POLL_SECONDS = 2

app = FastAPI(title="Frontend Stream Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sse_payload(payload: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        # Fallback to a safe error payload if any field is not strict JSON encodable.
        encoded = json.dumps(
            {
                "type": "error",
                "message": "non-json-serializable payload encountered",
            },
            ensure_ascii=False,
        )
    return f"data: {encoded}\n\n"


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


class TopicIndex:
    def __init__(self, container: str, topic: str, poll_seconds: int, stop_event: threading.Event) -> None:
        self.container = container
        self.topic = topic
        self.poll_seconds = poll_seconds
        self.stop_event = stop_event
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._by_article_id: dict[str, dict[str, Any]] = {}
        self._seen_messages = 0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self.stop_event.is_set():
            cmd = [
                "docker", "exec", self.container, "rpk", "topic", "consume", self.topic,
                "--offset", "start", "-f", "%v\\n",
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            assert proc.stdout is not None
            try:
                while not self.stop_event.is_set():
                    line = proc.stdout.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    article_id = str(payload.get("article_id", "")).strip()
                    if not article_id:
                        continue
                    with self._cv:
                        self._by_article_id[article_id] = payload
                        self._seen_messages += 1
                        self._cv.notify_all()
            finally:
                proc.kill()
                proc.wait(timeout=2)
            time.sleep(max(1, self.poll_seconds))

    def wait_for_article(self, article_id: str, timeout_seconds: int) -> dict[str, Any] | None:
        deadline = time.time() + timeout_seconds
        with self._cv:
            while True:
                payload = self._by_article_id.get(article_id)
                if payload is not None:
                    return payload
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._cv.wait(timeout=min(1.0, remaining))


def stream_topic_values(container: str, topic: str, poll_seconds: int, stop_event: threading.Event) -> Iterator[dict[str, Any]]:
    while not stop_event.is_set():
        cmd = ["docker", "exec", container, "rpk", "topic", "consume", topic, "--offset", "start", "-f", "%v\\n"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        assert proc.stdout is not None
        try:
            while not stop_event.is_set():
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    yield payload
        finally:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        time.sleep(max(1, poll_seconds))


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/stream")
def stream(topic: str = Query(..., min_length=1)) -> StreamingResponse:
    topic = topic.strip().lower()

    def event_stream() -> Iterator[str]:
        stop_event = threading.Event()
        seen_ids: set[str] = set()

        entity_index = TopicIndex(REDPANDA_CONTAINER, ENTITY_SENTIMENT_TOPIC, POLL_SECONDS, stop_event)
        graph_index = TopicIndex(REDPANDA_CONTAINER, GRAPH_CHANGES_TOPIC, POLL_SECONDS, stop_event)
        entity_index.start()
        graph_index.start()

        yield sse_payload({"type": "status", "message": f"Watching topic '{topic}' from raw ingestion at {now_iso()}"})
        # Keep-alive so proxies/browsers don't close idle SSE connections.
        yield ": keep-alive\n\n"

        try:
            for article in stream_topic_values(REDPANDA_CONTAINER, RAW_INGESTION_TOPIC, POLL_SECONDS, stop_event):
                article_id = str(article.get("id", "")).strip()
                if not article_id or article_id in seen_ids:
                    continue
                seen_ids.add(article_id)

                title = str(article.get("title", "")).strip()
                content = str(article.get("content", "")).strip()
                accepted = topic in title.lower()

                yield sse_payload(
                    {
                        "type": "step1",
                        "article_id": article_id,
                        "accepted": accepted,
                        "title": title,
                        "published_at": article.get("published_at"),
                        "source": article.get("feed_title"),
                        "link": article.get("link"),
                        "article_preview": content.replace("\\n", " ")[:400],
                    }
                )

                if not accepted:
                    continue

                entity_payload = entity_index.wait_for_article(article_id, ARTICLE_TIMEOUT_SECONDS)
                if entity_payload is None:
                    yield sse_payload(
                        {
                            "type": "timeout",
                            "step": "step2",
                            "article_id": article_id,
                            "message": f"Timeout waiting for entity-sentiment for article_id={article_id}",
                        }
                    )
                    continue

                entities = entity_payload.get("entities", []) or []
                scores = [float(ent.get("sentiment_score", 0.0)) for ent in entities]
                yield sse_payload(
                    {
                        "type": "step2",
                        "article_id": article_id,
                        "article_sentiment_mean": mean(scores) if scores else 0.0,
                        "entities": [
                            {
                                "text": ent.get("text", ""),
                                "type": ent.get("type", ""),
                                "sentiment": float(ent.get("sentiment_score", 0.0)),
                                "confidence": float(ent.get("confidence", 0.0)),
                                "sentence": str(ent.get("sentence", "")).replace("\\n", " ")[:120],
                            }
                            for ent in entities
                        ],
                    }
                )

                graph_payload = graph_index.wait_for_article(article_id, ARTICLE_TIMEOUT_SECONDS)
                if graph_payload is None:
                    yield sse_payload(
                        {
                            "type": "timeout",
                            "step": "step3",
                            "article_id": article_id,
                            "message": f"Timeout waiting for graph-changes for article_id={article_id}",
                        }
                    )
                    continue

                yield sse_payload(
                    {
                        "type": "step3",
                        "article_id": article_id,
                        "node_count": graph_payload.get("node_count", 0),
                        "edge_count": graph_payload.get("edge_count", 0),
                        "entity_changes": graph_payload.get("entity_changes", []) or [],
                        "relationship_changes": graph_payload.get("relationship_changes", []) or [],
                    }
                )

                try:
                    state = http_get_json(f"{GRAPH_STATE_API_URL.rstrip('/')}/graph/current")
                    nodes = state.get("nodes", []) or []
                    edges = state.get("edges", []) or []
                    yield sse_payload(
                        {
                            "type": "step4",
                            "article_id": article_id,
                            "total_nodes": state.get("node_count", 0),
                            "total_edges": state.get("edge_count", 0),
                            "nodes": sorted(nodes, key=lambda x: float(x.get("mention_count", 0)), reverse=True)[:TOP_NODES],
                            "edges": sorted(edges, key=lambda x: float(x.get("strength", 0)), reverse=True)[:TOP_EDGES],
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    yield sse_payload(
                        {
                            "type": "error",
                            "step": "step4",
                            "article_id": article_id,
                            "message": f"Failed to fetch graph state: {exc}",
                        }
                    )
                yield ": keep-alive\n\n"
        except Exception as exc:  # noqa: BLE001
            yield sse_payload({"type": "error", "message": f"stream crashed: {exc}"})
        finally:
            stop_event.set()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
