#!/usr/bin/env bash
set -e

CONTAINER=redpanda

echo "Waiting for Redpanda to be ready..."
until docker exec $CONTAINER rpk cluster info >/dev/null 2>&1; do
  sleep 1
done

echo "Creating topics..."

docker exec $CONTAINER rpk topic create raw_ingestion \
  -p 3 \
  -r 1 \
  -c cleanup.policy=delete \
  -c retention.ms=1800000 \
  --if-not-exists

docker exec $CONTAINER rpk topic create agent_insights \
  -p 3 \
  -r 1 \
  -c cleanup.policy=delete \
  -c retention.ms=1800000 \
  --if-not-exists

docker exec $CONTAINER rpk topic create system_commands \
  -p 1 \
  -r 1 \
  -c retention.ms=600000 \
  --if-not-exists

echo "Topics ready."
