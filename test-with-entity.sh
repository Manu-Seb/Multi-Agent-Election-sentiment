#!/usr/bin/env bash
set -euo pipefail

TOPIC="${1:-}"
if [[ -z "$TOPIC" ]]; then
  echo "Usage: $0 <topic> [poll_seconds] [per_article_timeout_seconds]"
  echo "Example:"
  echo "  $0 india 5 45"
  exit 1
fi

POLL_SECONDS="${2:-5}"
ARTICLE_TIMEOUT_SECONDS="${3:-45}"
GRAPH_STATE_API_URL="${GRAPH_STATE_API_URL:-http://localhost:8010}"
REDPANDA_CONTAINER="${REDPANDA_CONTAINER:-redpanda}"
RAW_INGESTION_TOPIC="${RAW_INGESTION_TOPIC:-raw_ingestion}"
ENTITY_SENTIMENT_TOPIC="${ENTITY_SENTIMENT_TOPIC:-entity-sentiment}"
GRAPH_CHANGES_TOPIC="${GRAPH_CHANGES_TOPIC:-graph-changes}"
TOP_NODES="${TOP_NODES:-10}"
TOP_EDGES="${TOP_EDGES:-10}"

python3 -u - "$TOPIC" "$POLL_SECONDS" "$ARTICLE_TIMEOUT_SECONDS" \
  "$GRAPH_STATE_API_URL" "$REDPANDA_CONTAINER" "$RAW_INGESTION_TOPIC" \
  "$ENTITY_SENTIMENT_TOPIC" "$GRAPH_CHANGES_TOPIC" "$TOP_NODES" "$TOP_EDGES" <<'PY'
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from statistics import mean
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_header(topic: str) -> None:
    print("=" * 90)
    print("EVENT SENTIMENT FLOW WATCHER")
    print(f"topic={topic} source_topic=raw_ingestion offset=start")
    print("=" * 90)


def http_get_json(url: str) -> Any:
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


class TopicIndex:
    def __init__(self, container: str, topic: str, poll_seconds: int) -> None:
        self.container = container
        self.topic = topic
        self.poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._by_article_id: dict[str, dict[str, Any]] = {}
        self._seen_messages = 0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while True:
            cmd = [
                "docker",
                "exec",
                self.container,
                "rpk",
                "topic",
                "consume",
                self.topic,
                "--offset",
                "start",
                "-f",
                "%v\n",
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            assert proc.stdout is not None
            assert proc.stderr is not None
            try:
                for line in proc.stdout:
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

            err = (proc.stderr.read() or "").strip()
            print(
                f"[warn] index stream ended topic={self.topic} rc={proc.returncode} "
                f"err={err[:160]} retrying in {self.poll_seconds}s"
            )
            time.sleep(max(1, self.poll_seconds))

    def get_seen_messages(self) -> int:
        with self._lock:
            return self._seen_messages

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


def stream_topic_values(container: str, topic: str, poll_seconds: int):
    while True:
        cmd = [
            "docker",
            "exec",
            container,
            "rpk",
            "topic",
            "consume",
            topic,
            "--offset",
            "start",
            "-f",
            "%v\n",
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        assert proc.stderr is not None

        try:
            for line in proc.stdout:
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
            proc.kill()
            proc.wait(timeout=2)

        err = proc.stderr.read().strip()
        print(f"[warn] raw topic stream ended. rc={proc.returncode} err={err[:200]} retrying in {poll_seconds}s")
        time.sleep(max(1, poll_seconds))


def wait_for_article_in_topic(
    article_id: str,
    topic: str,
    index: TopicIndex,
    timeout_seconds: int,
) -> dict[str, Any] | None:
    seen_before = index.get_seen_messages()
    print(
        f"[wait] index lookup topic={topic} article_id={article_id} timeout={timeout_seconds}s "
        f"(indexed_messages={seen_before})"
    )
    payload = index.wait_for_article(article_id=article_id, timeout_seconds=timeout_seconds)
    if payload is None:
        print(f"[wait] timeout topic={topic} article_id={article_id}")
        return None
    seen_after = index.get_seen_messages()
    print(
        f"[wait] found topic={topic} article_id={article_id} "
        f"(indexed_messages={seen_after})"
    )
    return payload


def print_article_step(article: dict[str, Any], topic: str) -> bool:
    title = str(article.get("title", "")).strip()
    content = str(article.get("content", "")).strip()
    article_id = str(article.get("id", ""))
    accepted = topic.lower() in title.lower()

    print("\n" + "-" * 90)
    print(f"STEP 1: ARTICLE FILTER article_id={article_id}")
    print(f"accepted_by_headline={accepted}")
    print(f"title={title}")
    print(f"published_at={article.get('published_at')}")
    print(f"source={article.get('feed_title')} link={article.get('link')}")
    snippet = content.replace("\n", " ")[:400]
    print(f"article_preview={snippet}")
    return accepted


def print_ner_and_sentiment(payload: dict[str, Any]) -> None:
    print("\nSTEP 2: NER + SENTIMENT (entity-sentiment)")
    entities = payload.get("entities", []) or []
    if not entities:
        print("No entities in payload.")
        return

    print("ENTITY | TYPE | SENTIMENT | CONFIDENCE | SENTENCE")
    scores: list[float] = []
    for ent in entities:
        text = str(ent.get("text", "")).strip()
        etype = str(ent.get("type", "")).strip()
        score = float(ent.get("sentiment_score", 0.0))
        conf = float(ent.get("confidence", 0.0))
        sent = str(ent.get("sentence", "")).replace("\n", " ").strip()[:120]
        scores.append(score)
        print(f"{text} | {etype} | {score:.4f} | {conf:.4f} | {sent}")

    article_sentiment = mean(scores) if scores else 0.0
    print(f"article_sentiment_mean={article_sentiment:.4f} (mean of entity sentiment scores)")


def print_graph_change(payload: dict[str, Any]) -> None:
    print("\nSTEP 3: GRAPH CHANGE EVENT (graph-changes)")
    print(
        f"article_id={payload.get('article_id')} node_count={payload.get('node_count')} edge_count={payload.get('edge_count')}"
    )
    print("ENTITY CHANGES: id | old_mentions | new_mentions | delta_mentions | new_sentiment | delta_sentiment")
    for ent in payload.get("entity_changes", []) or []:
        print(
            f"{ent.get('id')} | {ent.get('old_mentions')} | {ent.get('new_mentions')} | "
            f"{ent.get('delta_mentions')} | {ent.get('new_sentiment')} | {ent.get('delta_sentiment')}"
        )

    print("RELATIONSHIP CHANGES: source | target | new_strength | delta_strength | new_joint_sentiment")
    for rel in payload.get("relationship_changes", []) or []:
        print(
            f"{rel.get('source')} | {rel.get('target')} | {rel.get('new_strength')} | "
            f"{rel.get('delta_strength')} | {rel.get('new_joint_sentiment')}"
        )


def print_current_state(graph_state_api: str, top_nodes: int, top_edges: int) -> None:
    print("\nSTEP 4: CURRENT GRAPH STATE")
    url = f"{graph_state_api.rstrip('/')}/graph/current"
    state = http_get_json(url)
    nodes = state.get("nodes", []) or []
    edges = state.get("edges", []) or []
    print(f"total_nodes={state.get('node_count', 0)} total_edges={state.get('edge_count', 0)}")

    nodes_sorted = sorted(nodes, key=lambda x: float(x.get("mention_count", 0)), reverse=True)[:top_nodes]
    print("NODES: id | type | mention_count | sentiment | centrality")
    for n in nodes_sorted:
        print(
            f"{n.get('id')} | {n.get('type')} | {n.get('mention_count')} | "
            f"{n.get('sentiment')} | {n.get('centrality')}"
        )

    edges_sorted = sorted(edges, key=lambda x: float(x.get("strength", 0)), reverse=True)[:top_edges]
    print("EDGES: source | target | strength | joint_sentiment")
    for e in edges_sorted:
        print(f"{e.get('source')} | {e.get('target')} | {e.get('strength')} | {e.get('joint_sentiment')}")


def main() -> None:
    (
        topic,
        poll_seconds_raw,
        timeout_seconds_raw,
        graph_state_api,
        redpanda_container,
        raw_ingestion_topic,
        entity_sentiment_topic,
        graph_changes_topic,
        top_nodes_raw,
        top_edges_raw,
    ) = sys.argv[1:]

    poll_seconds = int(poll_seconds_raw)
    timeout_seconds = int(timeout_seconds_raw)
    top_nodes = int(top_nodes_raw)
    top_edges = int(top_edges_raw)
    seen_ids: set[str] = set()
    print_header(topic=topic)
    print(f"[info] replay+live mode active. reading {raw_ingestion_topic} from beginning.")
    print("[info] starting entity/graph indexers from topic start")

    entity_index = TopicIndex(redpanda_container, entity_sentiment_topic, poll_seconds)
    graph_index = TopicIndex(redpanda_container, graph_changes_topic, poll_seconds)
    entity_index.start()
    graph_index.start()

    processed_total = 0
    for article in stream_topic_values(redpanda_container, raw_ingestion_topic, poll_seconds):
        article_id = str(article.get("id", "")).strip()
        if not article_id or article_id in seen_ids:
            continue
        seen_ids.add(article_id)
        processed_total += 1
        print(f"\n[stream] raw article #{processed_total} article_id={article_id} at={now_iso()}")

        accepted = print_article_step(article=article, topic=topic)
        if not accepted:
            print("Rejected at headline filter. Skipping downstream checks.")
            continue

        entity_payload = wait_for_article_in_topic(
            article_id=article_id,
            topic=entity_sentiment_topic,
            index=entity_index,
            timeout_seconds=timeout_seconds,
        )
        if entity_payload is None:
            print("\nSTEP 2: NER + SENTIMENT")
            print(f"Timeout waiting for article_id={article_id} in topic={entity_sentiment_topic}")
            continue
        print_ner_and_sentiment(entity_payload)

        graph_change = wait_for_article_in_topic(
            article_id=article_id,
            topic=graph_changes_topic,
            index=graph_index,
            timeout_seconds=timeout_seconds,
        )
        if graph_change is None:
            print("\nSTEP 3: GRAPH CHANGE EVENT")
            print(f"Timeout waiting for article_id={article_id} in topic={graph_changes_topic}")
            continue
        print_graph_change(graph_change)

        try:
            print_current_state(graph_state_api=graph_state_api, top_nodes=top_nodes, top_edges=top_edges)
        except Exception as exc:
            print(f"\nSTEP 4: CURRENT GRAPH STATE")
            print(f"Failed to fetch graph state: {exc}")


if __name__ == "__main__":
    main()
PY
