#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

CONTAINER_NAME="${REDPANDA_CONTAINER:-redpanda}"
BOOTSTRAP="${KAFKA_BOOTSTRAP_SERVERS:-localhost:9092}"
INPUT_TOPIC="${RAW_ARTICLES_TOPIC:-raw_ingestion}"
OUTPUT_TOPIC="${ENTITY_SENTIMENT_TOPIC:-entity-sentiment}"
DLQ_TOPIC="${ENTITY_SENTIMENT_DLQ_TOPIC:-entity-sentiment-dlq}"

LOG_FILE="${PROJECT_ROOT}/logs/ai-analysis.log"
PID_FILE="${PROJECT_ROOT}/pids/ai-analysis.pid"
PYTHON_BIN=$PWD/ai-analysis-service/venv/bin/python
if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required."
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python executable not found at $PYTHON_BIN"
  echo "Set PYTHON_BIN=/path/to/python and rerun."
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "Redpanda container '${CONTAINER_NAME}' is not running."
  echo "Start it first with ./start.sh or docker compose in redpanda/."
  exit 1
fi

mkdir -p "${PROJECT_ROOT}/logs" "${PROJECT_ROOT}/pids"

print_section() {
  echo ""
  echo "============================================================"
  echo "$1"
  echo "============================================================"
}

pretty_print_records() {
  local file_path="$1"
  local key_one="$2"
  local key_two="$3"
  local printed=0

  if [ ! -s "$file_path" ]; then
    echo "(no records captured)"
    return 0
  fi

  while IFS=$'\t' read -r key value; do
    if [ "$key" != "$key_one" ] && [ "$key" != "$key_two" ]; then
      continue
    fi

    printed=1
    echo "Key: $key"
    echo "$value" | "$PYTHON_BIN" -c 'import json,sys; print(json.dumps(json.load(sys.stdin), indent=2, ensure_ascii=False))' 2>/dev/null || echo "$value"
    echo "------------------------------------------------------------"
  done < "$file_path"

  if [ "$printed" -eq 0 ]; then
    echo "(no records for this smoke-test run)"
  fi
}

print_section "Topic Setup"
echo "Ensuring output topics exist..."
docker exec "$CONTAINER_NAME" rpk topic create "$OUTPUT_TOPIC" -p 3 -r 1 --if-not-exists >/dev/null
docker exec "$CONTAINER_NAME" rpk topic create "$DLQ_TOPIC" -p 3 -r 1 --if-not-exists >/dev/null

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" >/dev/null 2>&1; then
  print_section "Processor Status"
  echo "AI analysis processor already running with PID $(cat "$PID_FILE")."
else
  print_section "Processor Status"
  echo "Starting AI analysis processor..."
  RAW_ARTICLES_TOPIC="$INPUT_TOPIC" \
  ENTITY_SENTIMENT_TOPIC="$OUTPUT_TOPIC" \
  ENTITY_SENTIMENT_DLQ_TOPIC="$DLQ_TOPIC" \
  KAFKA_BOOTSTRAP_SERVERS="$BOOTSTRAP" \
  KAFKA_GROUP_ID="entity-sentiment-processor" \
  HF_HUB_DISABLE_PROGRESS_BARS=1 \
  TRANSFORMERS_VERBOSITY=error \
  PYTHONPATH="${PROJECT_ROOT}/ai-analysis-service/src" \
  nohup "$PYTHON_BIN" "${PROJECT_ROOT}/ai-analysis-service/src/main.py" >"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
  sleep 5
fi

if ! kill -0 "$(cat "$PID_FILE")" >/dev/null 2>&1; then
  echo "Processor failed to start. Check logs: $LOG_FILE"
  exit 1
fi

print_section "Publish Test Inputs"
NOW_MS="$(( $(date +%s) * 1000 ))"
RUN_ID="$(date +%s)"
TEST_KEY_1="test-article-1-${RUN_ID}"
TEST_KEY_2="test-article-2-${RUN_ID}"
PAYLOAD_1="{\"id\":\"${TEST_KEY_1}\",\"source_url\":\"https://example.com/1\",\"content\":\"Alice from New York praised Bob during the election debate. Bob answered from Washington and voters sounded optimistic.\",\"published_at\":${NOW_MS},\"raw_metadata\":{\"source\":\"smoke-test\",\"run_id\":\"${RUN_ID}\"}}"
PAYLOAD_2="{\"id\":\"${TEST_KEY_2}\",\"source_url\":\"https://example.com/2\",\"content\":\"Carol criticized Dan in yesterday's campaign rally. The crowd in Texas felt disappointed and angry.\",\"published_at\":${NOW_MS},\"raw_metadata\":{\"source\":\"smoke-test\",\"run_id\":\"${RUN_ID}\"}}"

echo "Input topic: $INPUT_TOPIC"
echo "Run ID: $RUN_ID"
echo "Input keys:"
echo "  - $TEST_KEY_1"
echo "  - $TEST_KEY_2"

printf '%s\n' "$PAYLOAD_1" | docker exec -i "$CONTAINER_NAME" rpk topic produce "$INPUT_TOPIC" -k "$TEST_KEY_1" >/dev/null
printf '%s\n' "$PAYLOAD_2" | docker exec -i "$CONTAINER_NAME" rpk topic produce "$INPUT_TOPIC" -k "$TEST_KEY_2" >/dev/null

print_section "Processing Wait"
echo "Waiting for processing..."
for i in 1 2 3 4 5 6; do
  echo "  ... ${i}/6 (5s)"
  sleep 5
done

print_section "Recent Processor Logs"
tail -n 30 "$LOG_FILE" || true

print_section "Output Records (${OUTPUT_TOPIC})"
OUTPUT_CAPTURE="$(mktemp)"
if timeout 12s docker exec "$CONTAINER_NAME" rpk topic consume "$OUTPUT_TOPIC" -o @-10m:end -n 50 -f '%k\t%v\n' > "$OUTPUT_CAPTURE"; then
  pretty_print_records "$OUTPUT_CAPTURE" "$TEST_KEY_1" "$TEST_KEY_2"
else
  echo "(output consume timed out; no recent records captured)"
fi

print_section "DLQ Records (${DLQ_TOPIC})"
DLQ_CAPTURE="$(mktemp)"
if timeout 8s docker exec "$CONTAINER_NAME" rpk topic consume "$DLQ_TOPIC" -o @-10m:end -n 50 -f '%k\t%v\n' > "$DLQ_CAPTURE"; then
  pretty_print_records "$DLQ_CAPTURE" "$TEST_KEY_1" "$TEST_KEY_2"
else
  echo "(dlq consume timed out; no recent records captured)"
fi

rm -f "$OUTPUT_CAPTURE" "$DLQ_CAPTURE"

print_section "Summary"
echo "Done."
echo "Stop processor: kill \$(cat $PID_FILE) && rm -f $PID_FILE"
