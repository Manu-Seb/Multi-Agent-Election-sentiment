#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

REDPANDA_CONTAINER="${REDPANDA_CONTAINER:-redpanda}"
RAW_TOPIC="${RAW_ARTICLES_TOPIC:-raw_ingestion}"
ENTITY_TOPIC="${ENTITY_SENTIMENT_TOPIC:-entity-sentiment}"
DLQ_TOPIC="${ENTITY_SENTIMENT_DLQ_TOPIC:-entity-sentiment-dlq}"
GRAPH_CHANGES_TOPIC="${GRAPH_CHANGES_TOPIC:-graph-changes}"
GRAPH_METRICS_TOPIC="${GRAPH_METRICS_TOPIC:-graph-metrics}"

log_ok() { printf "✅ %s\n" "$1"; }
log_fail() { printf "❌ %s\n" "$1"; }
log_info() { printf "ℹ️  %s\n" "$1"; }

section() {
  echo
  echo "============================================================"
  echo "$1"
  echo "============================================================"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    log_fail "Missing required command: $1"
    exit 1
  }
}

check_pid_running() {
  local name="$1"
  local pidfile="$2"
  if [ ! -f "$pidfile" ]; then
    log_fail "$name PID file missing: $pidfile"
    return 1
  fi
  local pid
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" >/dev/null 2>&1; then
    log_ok "$name running (PID $pid)"
    return 0
  fi
  log_fail "$name PID not running (stale PID $pid)"
  return 1
}

check_http() {
  local name="$1"
  local url="$2"
  if curl -fsS "$url" >/dev/null 2>&1; then
    log_ok "$name healthy ($url)"
    return 0
  fi
  log_fail "$name not reachable ($url)"
  return 1
}

show_log_tail() {
  local label="$1"
  local file="$2"
  section "Log Tail: $label"
  if [ -f "$file" ]; then
    tail -n 80 "$file" || true
  else
    echo "(log file not found: $file)"
  fi
}

wait_for_topic_key() {
  local topic="$1"
  local key="$2"
  local tries="${3:-30}"
  local delay="${4:-2}"
  local tmp
  tmp="$(mktemp)"

  for _ in $(seq 1 "$tries"); do
    if timeout 8s docker exec "$REDPANDA_CONTAINER" rpk topic consume "$topic" -o @-10m:end -n 300 -f '%k\t%v\n' >"$tmp" 2>/dev/null; then
      local row
      row="$(grep "^${key}[[:space:]]" "$tmp" | tail -n 1 || true)"
      if [ -n "$row" ]; then
        log_ok "Found key=$key on topic=$topic"
        local value
        value="$(printf '%s' "$row" | cut -f2-)"
        python3 -c 'import json,sys; print(json.dumps(json.loads(sys.stdin.read()), indent=2, ensure_ascii=False))' <<<"$value" \
          || printf '%s\n' "$value"
        rm -f "$tmp"
        return 0
      fi
    fi
    log_info "Waiting for topic=$topic key=$key"
    sleep "$delay"
  done
  rm -f "$tmp"
  return 1
}

require_cmd docker
require_cmd curl
require_cmd python3

section "Preflight (Must Already Be Started)"
log_info "This script does not start services. Run ./start.sh first."

if ! docker ps --format '{{.Names}}' | grep -qx "$REDPANDA_CONTAINER"; then
  log_fail "Container '$REDPANDA_CONTAINER' is not running."
  exit 1
fi
log_ok "Redpanda container is running"

check_pid_running "Ingestion API" "$PROJECT_ROOT/pids/ingestion-api.pid" || exit 1
check_pid_running "Ingestion Producer" "$PROJECT_ROOT/pids/ingestion-producer.pid" || exit 1
check_pid_running "Entity Sentiment Processor" "$PROJECT_ROOT/pids/entity-sentiment-processor.pid" || exit 1
check_pid_running "Graph State Service" "$PROJECT_ROOT/pids/graph-state.pid" || exit 1
check_pid_running "Graph Algorithms Service" "$PROJECT_ROOT/pids/graph-algorithms.pid" || exit 1
check_pid_running "Storage Service" "$PROJECT_ROOT/pids/storage-service.pid" || exit 1
check_pid_running "WebSocket Service" "$PROJECT_ROOT/pids/websocket-service.pid" || exit 1

check_http "Ingestion API" "http://localhost:8000/health" || exit 1
check_http "Graph State API" "http://localhost:8010/health" || exit 1
check_http "Storage API" "http://localhost:8020/health" || exit 1
check_http "WebSocket API" "http://localhost:8030/health" || exit 1

section "1/5 Publish Test Article"
NOW_MS="$(( $(date +%s) * 1000 ))"
RUN_ID="$(date +%s)"
ARTICLE_KEY="pipeline-test-${RUN_ID}"
RAW_PAYLOAD="{\"id\":\"${ARTICLE_KEY}\",\"source_url\":\"https://example.com/pipeline\",\"content\":\"Alice from New York praised Bob. Bob responded from Washington with confidence.\",\"published_at\":${NOW_MS},\"raw_metadata\":{\"source\":\"test_everything\",\"run_id\":\"${RUN_ID}\"}}"
printf '%s\n' "$RAW_PAYLOAD" | docker exec -i "$REDPANDA_CONTAINER" rpk topic produce "$RAW_TOPIC" -k "$ARTICLE_KEY" >/dev/null
log_ok "Published key=$ARTICLE_KEY to $RAW_TOPIC"

section "2/5 Verify Entity-Sentiment Output"
if ! wait_for_topic_key "$ENTITY_TOPIC" "$ARTICLE_KEY" 40 3; then
  log_fail "No output found on $ENTITY_TOPIC for key=$ARTICLE_KEY"
  show_log_tail "Entity Sentiment Processor" "$PROJECT_ROOT/logs/entity-sentiment-processor.log"
  show_log_tail "Ingestion Producer" "$PROJECT_ROOT/logs/ingestion-producer.log"
  exit 1
fi

section "3/5 Verify Graph-Changes Output"
if ! wait_for_topic_key "$GRAPH_CHANGES_TOPIC" "$ARTICLE_KEY" 30 2; then
  log_fail "No output found on $GRAPH_CHANGES_TOPIC for key=$ARTICLE_KEY"
  show_log_tail "Graph State Service" "$PROJECT_ROOT/logs/graph-state.log"
  exit 1
fi

section "4/5 Verify Graph-Metrics Output"
if ! wait_for_topic_key "$GRAPH_METRICS_TOPIC" "$ARTICLE_KEY" 30 2; then
  log_fail "No output found on $GRAPH_METRICS_TOPIC for key=$ARTICLE_KEY"
  show_log_tail "Graph Algorithms Service" "$PROJECT_ROOT/logs/graph-algorithms.log"
  exit 1
fi

section "5/5 API and DLQ Checks"
GRAPH_CURRENT="$(curl -fsS "http://localhost:8010/graph/current")"
printf '%s' "$GRAPH_CURRENT" | python3 -c 'import json,sys; obj=json.load(sys.stdin); assert obj["node_count"] >= 1, obj; print("✅ Graph state API returned node_count >= 1")'

FROM_ISO="$(python3 - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc)-timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M:%SZ'))
PY
)"
TO_ISO="$(python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
PY
)"

STORAGE_CHANGES="$(curl -fsS "http://localhost:8020/changes?from=${FROM_ISO}&to=${TO_ISO}&entity=${ARTICLE_KEY}")"
printf '%s' "$STORAGE_CHANGES" | python3 -c 'import json,sys; obj=json.load(sys.stdin); assert obj["count"] >= 1, obj; print("✅ Storage API contains change events for test key")'

tmp_dlq="$(mktemp)"
if timeout 6s docker exec "$REDPANDA_CONTAINER" rpk topic consume "$DLQ_TOPIC" -o @-10m:end -n 50 -f '%k\t%v\n' >"$tmp_dlq" 2>/dev/null; then
  if grep -q "^${ARTICLE_KEY}[[:space:]]" "$tmp_dlq"; then
    log_fail "Test key appeared in DLQ topic ($DLQ_TOPIC)"
    cat "$tmp_dlq"
    rm -f "$tmp_dlq"
    exit 1
  fi
fi
rm -f "$tmp_dlq"
log_ok "No DLQ record for test key"

section "Summary"
log_ok "All tests passed for key=$ARTICLE_KEY"
echo "Run ID: $RUN_ID"
