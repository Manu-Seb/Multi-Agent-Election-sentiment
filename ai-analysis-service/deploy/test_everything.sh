#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="$PROJECT_ROOT/logs"
PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

if [ -x "$PROJECT_ROOT/ai-analysis-service/venv/bin/python" ]; then
  PYTHON_BIN="$PROJECT_ROOT/ai-analysis-service/venv/bin/python"
elif [ -x "$PROJECT_ROOT/venv/bin/python" ]; then
  PYTHON_BIN="$PROJECT_ROOT/venv/bin/python"
else
  echo "Python venv not found. Expected ai-analysis-service/venv or venv."
  exit 1
fi

export PYTHONPATH="$PROJECT_ROOT/ai-analysis-service/src:$PROJECT_ROOT/ai-analysis-service"

GRAPH_STATE_PID_FILE="$PID_DIR/graph-state-test.pid"
GRAPH_ALGO_PID_FILE="$PID_DIR/graph-algo-test.pid"
STORAGE_PID_FILE="$PID_DIR/storage-test.pid"

cleanup() {
  for f in "$GRAPH_STATE_PID_FILE" "$GRAPH_ALGO_PID_FILE" "$STORAGE_PID_FILE"; do
    if [ -f "$f" ]; then
      kill "$(cat "$f")" 2>/dev/null || true
      rm -f "$f"
    fi
  done
}
trap cleanup EXIT

log() {
  printf '\n[%s] %s\n' "TEST" "$1"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1"; exit 1; }
}

wait_for_http() {
  local url="$1"
  local tries=30
  local delay=1
  for _ in $(seq 1 "$tries"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay"
  done
  echo "Timed out waiting for $url"
  return 1
}

require_cmd curl
require_cmd docker

if ! docker ps --format '{{.Names}}' | grep -q '^redpanda$'; then
  echo "Redpanda container is not running. Run ./start.sh first."
  exit 1
fi

log "1/7 Unit tests (Graph State + Graph Algorithms + Storage State)"
"$PYTHON_BIN" - <<'PY'
from tests.test_graph_state.test_store import (
    test_graph_update_and_lookup,
    test_metrics_include_memory_usage,
    test_change_event_contains_deltas_and_validation,
    test_change_event_threshold_filters_tiny_sentiment_updates,
)
from tests.test_graph_state.test_api import test_graph_update_and_endpoints
from tests.test_graph_algorithms.test_engine import (
    test_relationship_strength_and_pagerank,
    test_community_changes,
    test_metrics_payload_shape,
)
from tests.test_storage_service.test_state import (
    test_apply_change_event_updates_graph_state,
    test_reconstruction_cache,
    test_history_and_trend,
)

test_graph_update_and_lookup()
test_metrics_include_memory_usage()
test_change_event_contains_deltas_and_validation()
test_change_event_threshold_filters_tiny_sentiment_updates()
test_graph_update_and_endpoints()

test_relationship_strength_and_pagerank()
test_community_changes()
test_metrics_payload_shape()

test_apply_change_event_updates_graph_state()
test_reconstruction_cache()
test_history_and_trend()
print("unit tests passed")
PY

log "2/7 Entity processor smoke test"
"$PROJECT_ROOT/ai-analysis-service/deploy/smoke_test_pipeline.sh"

log "3/7 Graph State API integration"
GRAPH_STREAM_ENABLED=false nohup "$PYTHON_BIN" -m uvicorn graph_state.api:app --host 0.0.0.0 --port 8010 > "$LOG_DIR/graph-state-test.log" 2>&1 &
echo $! > "$GRAPH_STATE_PID_FILE"
wait_for_http "http://localhost:8010/graph/current"

NOW_MS="$(( $(date +%s) * 1000 ))"
curl -fsS -X POST "http://localhost:8010/graph/update" \
  -H 'Content-Type: application/json' \
  -d "{\"article_id\":\"graph-state-manual\",\"processed_at\":$NOW_MS,\"entities\":[{\"text\":\"Alice\",\"type\":\"PERSON\",\"sentence\":\"Alice met Bob.\",\"sentiment_score\":0.8,\"confidence\":0.9,\"char_start\":0,\"char_end\":5},{\"text\":\"Bob\",\"type\":\"PERSON\",\"sentence\":\"Alice met Bob.\",\"sentiment_score\":0.6,\"confidence\":0.9,\"char_start\":10,\"char_end\":13}],\"co_occurrence_pairs\":[{\"entity_1\":\"Alice\",\"entity_2\":\"Bob\",\"count\":1}],\"processing_metadata\":{\"processing_time_ms\":12,\"model_versions\":{\"spacy\":\"test\",\"distilbert\":\"test\"}}}" >/dev/null

STATE_JSON="$(curl -fsS "http://localhost:8010/graph/current")"
"$PYTHON_BIN" - <<PY
import json
obj = json.loads('''$STATE_JSON''')
assert obj["node_count"] >= 2
assert obj["edge_count"] >= 1
print("graph state api integration passed")
PY

log "4/7 Graph Algorithms integration (entity-sentiment -> graph-metrics)"
docker exec redpanda rpk topic create graph-metrics -p 3 -r 1 --if-not-exists >/dev/null 2>&1 || true

nohup "$PYTHON_BIN" -m graph_algorithms.main > "$LOG_DIR/graph-algo-test.log" 2>&1 &
echo $! > "$GRAPH_ALGO_PID_FILE"
sleep 3

EVENT_ID="graph-algo-test-$(date +%s)"
PAYLOAD="{\"article_id\":\"$EVENT_ID\",\"processed_at\":$NOW_MS,\"entities\":[{\"text\":\"Alice\",\"type\":\"PERSON\",\"sentence\":\"Alice met Bob.\",\"sentiment_score\":0.8,\"confidence\":0.9,\"char_start\":0,\"char_end\":5},{\"text\":\"Bob\",\"type\":\"PERSON\",\"sentence\":\"Alice met Bob.\",\"sentiment_score\":0.7,\"confidence\":0.9,\"char_start\":10,\"char_end\":13}],\"co_occurrence_pairs\":[{\"entity_1\":\"Alice\",\"entity_2\":\"Bob\",\"count\":1}],\"processing_metadata\":{\"processing_time_ms\":20,\"model_versions\":{\"spacy\":\"en_core_web_sm\",\"distilbert\":\"distilbert\"}}}"
printf '%s\n' "$PAYLOAD" | docker exec -i redpanda rpk topic produce entity-sentiment -k "$EVENT_ID" >/dev/null

if ! timeout 20s docker exec redpanda rpk topic consume graph-metrics -o @-10m:end -n 20 -f '%k\t%v\n' | grep -q "$EVENT_ID"; then
  echo "Did not observe graph-metrics output for $EVENT_ID"
  exit 1
fi

echo "graph algorithms integration passed"

log "5/7 Storage Postgres container"
cd "$PROJECT_ROOT/ai-analysis-service/deploy/storage-postgres"
docker compose up -d
cd "$PROJECT_ROOT"

log "6/7 Storage Service API integration"
GRAPH_STORAGE_POSTGRES_DSN="postgresql://graph_user:graph_pass@localhost:5433/graph_storage" \
KAFKA_BOOTSTRAP_SERVERS="localhost:9092" \
nohup "$PYTHON_BIN" -m uvicorn storage_service.api:app --host 0.0.0.0 --port 8020 > "$LOG_DIR/storage-test.log" 2>&1 &
echo $! > "$STORAGE_PID_FILE"
wait_for_http "http://localhost:8020/metrics"

CHANGE_ID="storage-test-$(date +%s)"
CHANGE_PAYLOAD="{\"schema_version\":1,\"timestamp\":$NOW_MS,\"article_id\":\"$CHANGE_ID\",\"entity_changes\":[{\"id\":\"Alice\",\"old_mentions\":0,\"new_mentions\":1,\"delta_mentions\":1,\"old_sentiment\":0.0,\"new_sentiment\":0.8,\"delta_sentiment\":0.8,\"old_centrality\":0.0,\"new_centrality\":0.5,\"delta_centrality\":0.5}],\"relationship_changes\":[{\"source\":\"Alice\",\"target\":\"Bob\",\"old_strength\":0.0,\"new_strength\":1.0,\"delta_strength\":1.0,\"old_joint_sentiment\":0.0,\"new_joint_sentiment\":0.7,\"delta_joint_sentiment\":0.7}],\"processing_metrics\":{\"extraction_ms\":10,\"graph_update_ms\":2}}"
printf '%s\n' "$CHANGE_PAYLOAD" | docker exec -i redpanda rpk topic produce graph-changes -k "$CHANGE_ID" >/dev/null
sleep 4

FROM_ISO="$(date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || python3 - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc)-timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ'))
PY
)"
TO_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
PY
)"

CHANGES_JSON="$(curl -fsS "http://localhost:8020/changes?from=$FROM_ISO&to=$TO_ISO&entity=$CHANGE_ID")"
"$PYTHON_BIN" - <<PY
import json
obj = json.loads('''$CHANGES_JSON''')
assert obj["count"] >= 1
print("storage api integration passed")
PY

log "7/7 Optional benchmarks (quick run)"
PYTHONPATH="$PYTHONPATH" "$PYTHON_BIN" ai-analysis-service/benchmarks/benchmark_graph_state.py >/dev/null
PYTHONPATH="$PYTHONPATH" "$PYTHON_BIN" ai-analysis-service/benchmarks/benchmark_graph_algorithms.py >/dev/null

echo
echo "All tests passed."
echo "Logs:"
echo "  - $LOG_DIR/ai-analysis.log"
echo "  - $LOG_DIR/graph-state-test.log"
echo "  - $LOG_DIR/graph-algo-test.log"
echo "  - $LOG_DIR/storage-test.log"
