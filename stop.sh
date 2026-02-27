#!/bin/bash
# Master shutdown script for Election Sentiment Analysis System

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

PID_DIR="$PROJECT_ROOT/pids"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

compose_down() {
  local dir="$1"
  log_info "Stopping docker stack: $dir"
  (
    cd "$dir"
    if command -v docker >/dev/null 2>&1; then
      docker compose down || true
    else
      docker-compose down || true
    fi
  )
}

stop_pid_service() {
  local name="$1"
  local pidfile="$2"

  if [ ! -f "$pidfile" ]; then
    log_warn "$name PID file not found"
    return
  fi

  local pid
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" >/dev/null 2>&1; then
    log_info "Stopping $name (PID: $pid)"
    kill "$pid" >/dev/null 2>&1 || true
  else
    log_warn "$name is not running (stale PID: $pid)"
  fi
  rm -f "$pidfile"
}

echo "========================================="
echo "Election Sentiment Analysis - Shutdown"
echo "========================================="

# 1) Host python services
stop_pid_service "WebSocket Broadcast Service" "$PID_DIR/websocket-service.pid"
stop_pid_service "Storage Service" "$PID_DIR/storage-service.pid"
stop_pid_service "Graph Algorithms Service" "$PID_DIR/graph-algorithms.pid"
stop_pid_service "Graph State Service" "$PID_DIR/graph-state.pid"
stop_pid_service "Entity Sentiment Processor" "$PID_DIR/entity-sentiment-processor.pid"
stop_pid_service "Ingestion Producer" "$PID_DIR/ingestion-producer.pid"
stop_pid_service "Ingestion API" "$PID_DIR/ingestion-api.pid"

# 2) Infra stacks
compose_down "$PROJECT_ROOT/monitoring"
compose_down "$PROJECT_ROOT/postgres"
compose_down "$PROJECT_ROOT/ttrss"
compose_down "$PROJECT_ROOT/redpanda"

echo ""
echo "========================================="
echo -e "${GREEN}All services stopped.${NC}"
echo "========================================="
