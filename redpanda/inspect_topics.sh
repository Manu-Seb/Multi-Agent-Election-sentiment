#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${REDPANDA_CONTAINER:-redpanda}"
MESSAGE_COUNT="${1:-5}"

TOPICS=(
  "raw_ingestion"
  "entity-sentiment"
  "entity-sentiment-dlq"
  "graph-changes"
  "graph-snapshots"
  "graph-metrics"
)

echo "Redpanda container: $CONTAINER"
echo "Messages per topic: $MESSAGE_COUNT"
echo

echo "== Topic List =="
docker exec "$CONTAINER" rpk topic list
echo

format_messages() {
  local topic="$1"
  local raw=""

  if ! raw="$(timeout 8s docker exec "$CONTAINER" rpk topic consume "$topic" -o @-30m:end -n "$MESSAGE_COUNT" -f '%k\t%v\n' 2>/dev/null)"; then
    echo "[info] no recent messages found or consume timed out"
    return 0
  fi

  if [[ -z "$raw" ]]; then
    echo "[info] no recent messages found"
    return 0
  fi

  while IFS=$'\t' read -r key value; do
    [[ -z "${key}${value}" ]] && continue
    echo "---"
    echo "key: ${key:-<empty>}"
    echo "value:"
    if [[ -n "${value:-}" ]] && echo "$value" | jq . >/dev/null 2>&1; then
      echo "$value" | jq .
    else
      printf '  %s\n' "${value:-<empty>}"
    fi
    echo
  done <<< "$raw"
}

for topic in "${TOPICS[@]}"; do
  echo "== Topic: $topic =="
  if docker exec "$CONTAINER" rpk topic describe "$topic" >/dev/null 2>&1; then
    docker exec "$CONTAINER" rpk topic describe "$topic"
    echo
    format_messages "$topic"
  else
    echo "[info] topic does not exist"
  fi
  echo
done
