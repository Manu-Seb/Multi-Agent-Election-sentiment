#!/bin/bash
# Master startup script for Election Sentiment Analysis System
# Starts infra via folder-based docker-compose and app services via local venvs.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

LOG_DIR="$PROJECT_ROOT/logs"
PID_DIR="$PROJECT_ROOT/pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

compose_up() {
  local dir="$1"
  log_info "Starting docker stack: $dir"
  if (
    cd "$dir"
    if command -v docker >/dev/null 2>&1; then
      docker compose up -d
    else
      docker-compose up -d
    fi
  ); then
    return
  fi

  log_warn "Compose up failed for $dir; trying to repair conflicting named containers."
  repair_stack_conflicts "$dir"

  if (
    cd "$dir"
    if command -v docker >/dev/null 2>&1; then
      docker compose up -d
    else
      docker-compose up -d
    fi
  ); then
    return
  fi

  log_error "Compose up failed for $dir even after conflict repair."
  exit 1
}

ensure_venv() {
  local venv_dir="$1"
  local req_file="$2"
  local name="$3"

  if [ ! -x "$venv_dir/bin/python" ]; then
    log_warn "$name venv not found at $venv_dir; creating it."
    python3 -m venv "$venv_dir"
  fi

  log_info "Installing/updating dependencies for $name"
  "$venv_dir/bin/python" -m pip install --upgrade pip >/dev/null
  "$venv_dir/bin/python" -m pip install -r "$req_file" >/dev/null
}

start_python_service() {
  local name="$1"
  local cmd="$2"
  local logfile="$3"
  local pidfile="$4"

  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" >/dev/null 2>&1; then
    log_warn "$name already running (PID: $(cat "$pidfile"))"
    return
  fi

  log_info "Starting $name"
  nohup bash -lc "$cmd" >"$logfile" 2>&1 &
  local pid=$!
  sleep 2
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    log_error "$name failed to stay up. Last log lines:"
    tail -n 40 "$logfile" 2>/dev/null || true
    return 1
  fi
  echo "$pid" >"$pidfile"
  log_info "$name started (PID: $pid)"
}

cleanup_legacy_app_containers() {
  local containers=(
    ingestion-api
    ingestion-producer
    entity-sentiment-processor
    graph-state
    graph-algorithms
    storage-service
    election-sentiment-websocket-service-1
  )
  for c in "${containers[@]}"; do
    if docker ps -a --format '{{.Names}}' | grep -qx "$c"; then
      log_warn "Removing legacy container: $c"
      docker rm -f "$c" >/dev/null 2>&1 || true
    fi
  done
}

repair_stack_conflicts() {
  local dir="$1"
  local base
  base="$(basename "$dir")"
  local containers=()

  case "$base" in
    redpanda)
      containers=(redpanda)
      ;;
    ttrss)
      containers=(ttrss mercury opencc ttrss-database.postgres-1 database.postgres)
      ;;
    postgres)
      containers=(graph-storage-postgres)
      ;;
    monitoring)
      containers=(prometheus loki promtail grafana)
      ;;
  esac

  for c in "${containers[@]}"; do
    if docker ps -a --format '{{.Names}}' | grep -qx "$c"; then
      log_warn "Removing conflicting container for stack $base: $c"
      docker rm -f "$c" >/dev/null 2>&1 || true
    fi
  done
}

echo "========================================="
echo "Election Sentiment Analysis - Startup"
echo "========================================="

if [ ! -f "$PROJECT_ROOT/ingestion-service/.env" ]; then
  log_error ".env file not found in ingestion-service/"
  log_info "Please copy ingestion-service/.env.example to ingestion-service/.env"
  exit 1
fi

# Export ingestion env vars for this script run.
set -a
source "$PROJECT_ROOT/ingestion-service/.env"
set +a

# 1) Infra stacks (folder parity)
compose_up "$PROJECT_ROOT/redpanda"
compose_up "$PROJECT_ROOT/ttrss"
compose_up "$PROJECT_ROOT/postgres"
compose_up "$PROJECT_ROOT/monitoring"

# 2) Kafka topics
log_info "Creating Kafka topics"
sleep 5
(
  cd "$PROJECT_ROOT/redpanda"
  bash setup_topics.sh || true
)
docker exec redpanda rpk topic create entity-sentiment -p 3 -r 1 --if-not-exists >/dev/null 2>&1 || true
docker exec redpanda rpk topic create entity-sentiment-dlq -p 3 -r 1 --if-not-exists >/dev/null 2>&1 || true
docker exec redpanda rpk topic create graph-changes -p 3 -r 1 --if-not-exists >/dev/null 2>&1 || true
docker exec redpanda rpk topic create graph-snapshots -p 3 -r 1 --if-not-exists >/dev/null 2>&1 || true
docker exec redpanda rpk topic create graph-metrics -p 3 -r 1 --if-not-exists >/dev/null 2>&1 || true
log_info "Kafka topics ready"

# 3) Venvs
AI_VENV="$PROJECT_ROOT/ai-analysis-service/venv"
INGEST_VENV="$PROJECT_ROOT/ingestion-service/venv"

ensure_venv "$INGEST_VENV" "$PROJECT_ROOT/ingestion-service/requirements.txt" "ingestion-service"
ensure_venv "$AI_VENV" "$PROJECT_ROOT/ai-analysis-service/requirements.runtime.txt" "ai-analysis-service (runtime)"
ensure_venv "$AI_VENV" "$PROJECT_ROOT/ai-analysis-service/requirements.ml.txt" "ai-analysis-service (ml)"

# Ensure spaCy model needed by entity extractor exists.
if ! "$AI_VENV/bin/python" -c "import spacy; spacy.load('en_core_web_sm')" >/dev/null 2>&1; then
  log_info "Installing spaCy model en_core_web_sm"
  "$AI_VENV/bin/python" -m spacy download en_core_web_sm >/dev/null || \
    log_warn "Could not auto-install en_core_web_sm; entity processor may fail until it is installed."
fi

# 4) Ingestion services (host Python)
cleanup_legacy_app_containers

start_python_service \
  "Ingestion API" \
  "cd '$PROJECT_ROOT/ingestion-service' && '$INGEST_VENV/bin/uvicorn' main:app --host 0.0.0.0 --port 8000" \
  "$LOG_DIR/ingestion-api.log" \
  "$PID_DIR/ingestion-api.pid"

start_python_service \
  "Ingestion Producer" \
  "cd '$PROJECT_ROOT/ingestion-service' && TTRSS_URL='${TTRSS_URL:-http://localhost:181/api/}' TTRSS_USER='${TTRSS_USER:-admin}' TTRSS_PASSWORD='${TTRSS_PASSWORD:-password}' KAFKA_BROKER='${KAFKA_BROKER:-localhost:9092}' POLL_INTERVAL='${POLL_INTERVAL:-15}' '$INGEST_VENV/bin/python' producer.py" \
  "$LOG_DIR/ingestion-producer.log" \
  "$PID_DIR/ingestion-producer.pid"

# 5) AI analysis services (host Python)
AI_PY="$AI_VENV/bin/python"
AI_UVICORN="$AI_VENV/bin/uvicorn"
AI_PYTHONPATH="$PROJECT_ROOT/ai-analysis-service/src"

start_python_service \
  "Entity Sentiment Processor" \
  "cd '$PROJECT_ROOT' && PYTHONPATH='$AI_PYTHONPATH' KAFKA_BOOTSTRAP_SERVERS='localhost:9092' RAW_ARTICLES_TOPIC='raw_ingestion' ENTITY_SENTIMENT_TOPIC='entity-sentiment' ENTITY_SENTIMENT_DLQ_TOPIC='entity-sentiment-dlq' KAFKA_GROUP_ID='entity-sentiment-processor' HF_HUB_DISABLE_PROGRESS_BARS='1' TRANSFORMERS_VERBOSITY='error' '$AI_PY' ai-analysis-service/src/main.py" \
  "$LOG_DIR/entity-sentiment-processor.log" \
  "$PID_DIR/entity-sentiment-processor.pid"

start_python_service \
  "Graph State Service" \
  "cd '$PROJECT_ROOT' && PYTHONPATH='$AI_PYTHONPATH' KAFKA_BOOTSTRAP_SERVERS='localhost:9092' GRAPH_STREAM_ENABLED='true' ENTITY_SENTIMENT_TOPIC='entity-sentiment' GRAPH_CHANGES_TOPIC='graph-changes' '$AI_UVICORN' graph_state.api:app --host 0.0.0.0 --port 8010" \
  "$LOG_DIR/graph-state.log" \
  "$PID_DIR/graph-state.pid"

start_python_service \
  "Graph Algorithms Service" \
  "cd '$PROJECT_ROOT' && PYTHONPATH='$AI_PYTHONPATH' KAFKA_BOOTSTRAP_SERVERS='localhost:9092' ENTITY_SENTIMENT_TOPIC='entity-sentiment' GRAPH_METRICS_TOPIC='graph-metrics' GRAPH_ALGO_GROUP_ID='graph-algorithms-service' '$AI_PY' -m graph_algorithms.main" \
  "$LOG_DIR/graph-algorithms.log" \
  "$PID_DIR/graph-algorithms.pid"

start_python_service \
  "Storage Service" \
  "cd '$PROJECT_ROOT' && PYTHONPATH='$AI_PYTHONPATH' KAFKA_BOOTSTRAP_SERVERS='localhost:9092' GRAPH_STORAGE_POSTGRES_DSN='postgresql://graph_user:graph_pass@localhost:5433/graph_storage' GRAPH_STORAGE_GROUP_ID='graph-storage' GRAPH_CHANGES_TOPIC='graph-changes' GRAPH_SNAPSHOTS_TOPIC='graph-snapshots' GRAPH_STORAGE_INSTANCE_ID='storage-1' '$AI_UVICORN' storage_service.api:app --host 0.0.0.0 --port 8020" \
  "$LOG_DIR/storage-service.log" \
  "$PID_DIR/storage-service.pid"

start_python_service \
  "WebSocket Broadcast Service" \
  "cd '$PROJECT_ROOT' && PYTHONPATH='$AI_PYTHONPATH' KAFKA_BOOTSTRAP_SERVERS='localhost:9092' WEBSOCKET_GROUP_ID='websocket-broadcaster' GRAPH_CHANGES_TOPIC='graph-changes' GRAPH_STORAGE_POSTGRES_DSN='postgresql://graph_user:graph_pass@localhost:5433/graph_storage' WS_REDIS_ENABLED='false' WS_REDIS_URL='redis://localhost:6379/0' WS_INSTANCE_ID='ws-1' '$AI_UVICORN' websocket_service.api:app --host 0.0.0.0 --port 8030" \
  "$LOG_DIR/websocket-service.log" \
  "$PID_DIR/websocket-service.pid"

echo ""
echo "========================================="
echo -e "${GREEN}All services started.${NC}"
echo "========================================="
echo "TT-RSS UI:           http://localhost:181"
echo "Ingestion API:       http://localhost:8000"
echo "Graph State API:     http://localhost:8010"
echo "Storage API:         http://localhost:8020"
echo "WebSocket API:       http://localhost:8030"
echo "Prometheus:          http://localhost:9090"
echo "Grafana:             http://localhost:3000"
echo ""
echo "Logs directory:      $LOG_DIR"
echo "Stop with:           ./stop.sh"
