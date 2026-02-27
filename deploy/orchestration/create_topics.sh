#!/usr/bin/env bash
set -euo pipefail

BROKERS="${KAFKA_BROKERS:-redpanda:9092}"
PARTITIONS="${KAFKA_NUM_PARTITIONS:-3}"
REPLICA="${KAFKA_REPLICATION_FACTOR:-1}"

until rpk cluster info --brokers "$BROKERS" >/dev/null 2>&1; do
  sleep 1
done

create_topic() {
  local topic="$1"
  rpk topic create "$topic" \
    --brokers "$BROKERS" \
    -p "$PARTITIONS" \
    -r "$REPLICA" \
    --if-not-exists >/dev/null
}

create_topic raw_ingestion
create_topic entity-sentiment
create_topic entity-sentiment-dlq
create_topic graph-changes
create_topic graph-snapshots
create_topic graph-metrics

# legacy topics retained for compatibility
create_topic raw_ingestion
create_topic agent_insights
create_topic system_commands

echo "topics ready"
