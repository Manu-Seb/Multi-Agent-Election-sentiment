#!/usr/bin/env bash
set -e

CONTAINER=redpanda

echo "Producing test message..."

docker exec -i $CONTAINER rpk topic produce raw_ingestion \
  --key "Tamil Nadu" <<EOF
{"source":"rss","text":"Test election event","region":"Tamil Nadu","timestamp":"$(date -Iseconds)"}
EOF

echo
echo "Consuming message..."

docker exec $CONTAINER rpk topic consume raw_ingestion \
  -o start \
  -n 1
