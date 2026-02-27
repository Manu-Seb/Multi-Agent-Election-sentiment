#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

SERVICE_NAME="${SERVICE_NAME:-storage-service}"
REDPANDA_CONTAINER="${REDPANDA_CONTAINER:-redpanda}"
STORAGE_URL="${STORAGE_URL:-http://localhost:8020}"
CHANGES_TOPIC="${CHANGES_TOPIC:-graph-changes}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1"
    exit 1
  }
}

wait_for_storage_health() {
  local tries=90
  local delay=2
  local url="${STORAGE_URL}/health"

  echo "Waiting for ${SERVICE_NAME} health: ${url}"
  for i in $(seq 1 "$tries"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "Storage service is healthy."
      return 0
    fi
    printf '  ... %d/%d\n' "$i" "$tries"
    sleep "$delay"
  done

  echo "Storage service did not become healthy in time."
  echo
  echo "=== docker compose ps ==="
  docker compose ps || true
  echo
  echo "=== ${SERVICE_NAME} logs (last 200 lines) ==="
  docker compose logs --tail=200 "$SERVICE_NAME" || true
  return 1
}

iso_now() {
  python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
PY
}

iso_minus_minutes() {
  local mins="$1"
  python3 - "$mins" <<'PY'
import sys
from datetime import datetime, timedelta, timezone
mins = int(sys.argv[1])
print((datetime.now(timezone.utc) - timedelta(minutes=mins)).strftime("%Y-%m-%dT%H:%M:%SZ"))
PY
}

pretty_json() {
  python3 - <<'PY'
import json, sys
data = sys.stdin.read().strip()
if not data:
    print("{}")
else:
    print(json.dumps(json.loads(data), indent=2))
PY
}

require_cmd docker
require_cmd curl
require_cmd python3

echo "Ensuring core dependencies are up..."
docker compose up -d redpanda topic-init graph-storage-postgres "$SERVICE_NAME" >/dev/null

wait_for_storage_health

RUN_ID="$(date +%s)"
ARTICLE_ID="storage-test-${RUN_ID}"
NOW_MS="$(( $(date +%s) * 1000 ))"

CHANGE_PAYLOAD="$(cat <<JSON
{"schema_version":1,"timestamp":${NOW_MS},"article_id":"${ARTICLE_ID}","entity_changes":[{"id":"Alice","old_mentions":0,"new_mentions":1,"delta_mentions":1,"old_sentiment":0.0,"new_sentiment":0.8,"delta_sentiment":0.8,"old_centrality":0.0,"new_centrality":0.1,"delta_centrality":0.1}],"relationship_changes":[{"source":"Alice","target":"Bob","old_strength":0.0,"new_strength":1.0,"delta_strength":1.0,"old_joint_sentiment":0.0,"new_joint_sentiment":0.7,"delta_joint_sentiment":0.7}],"processing_metrics":{"extraction_ms":10,"graph_update_ms":5}}
JSON
)"

echo "Publishing test graph-change event: ${ARTICLE_ID}"
printf '%s\n' "$CHANGE_PAYLOAD" | docker exec -i "$REDPANDA_CONTAINER" rpk topic produce "$CHANGES_TOPIC" -k "$ARTICLE_ID" >/dev/null

FROM_ISO="$(iso_minus_minutes 15)"
TO_ISO="$(iso_now)"

echo "Waiting for event persistence via Storage API..."
FOUND=0
for i in $(seq 1 30); do
  RESP="$(curl -fsS "${STORAGE_URL}/changes?from=${FROM_ISO}&to=${TO_ISO}&entity=${ARTICLE_ID}")"
  COUNT="$(python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("count",0))' <<<"$RESP")"
  if [ "$COUNT" -ge 1 ]; then
    FOUND=1
    echo "Event persisted (count=${COUNT})."
    echo
    echo "=== /changes ==="
    pretty_json <<<"$RESP"
    break
  fi
  printf '  ... %d/30\n' "$i"
  sleep 1
done

if [ "$FOUND" -ne 1 ]; then
  echo "Storage event was not found in time."
  echo
  echo "=== ${SERVICE_NAME} logs (last 200 lines) ==="
  docker compose logs --tail=200 "$SERVICE_NAME" || true
  exit 1
fi

echo
echo "=== /state (now) ==="
STATE_RESP="$(curl -fsS "${STORAGE_URL}/state?time=$(iso_now)")"
pretty_json <<<"$STATE_RESP"

echo
echo "=== /entity/Alice/history?window=1h ==="
HISTORY_RESP="$(curl -fsS "${STORAGE_URL}/entity/Alice/history?window=1h")"
pretty_json <<<"$HISTORY_RESP"

echo
echo "=== /metrics/sentiment-trend?interval=1h ==="
TREND_RESP="$(curl -fsS "${STORAGE_URL}/metrics/sentiment-trend?interval=1h")"
pretty_json <<<"$TREND_RESP"

echo
echo "Storage service tester completed successfully."
