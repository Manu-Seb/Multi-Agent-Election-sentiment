#!/bin/bash
# Master shutdown script for Election Sentiment Analysis System
# Stops: Storage Service, Kafka Producer, Ingestion API, Redpanda, Graph Storage PostgreSQL, and TT-RSS

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "========================================="
echo "Election Sentiment Analysis - Shutdown"
echo "========================================="

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

stop_pid_service() {
    local name="$1"
    local pidfile="$2"
    if [ -f "$pidfile" ]; then
        local pid
        pid=$(cat "$pidfile")
        log_info "Stopping ${name} (PID: ${pid})..."
        kill "$pid" 2>/dev/null || log_warn "${name} already stopped"
        rm -f "$pidfile"
    else
        log_warn "${name} PID file not found"
    fi
}

# Step 1: Stop Storage Service
stop_pid_service "Storage Service" "pids/storage-service.pid"

# Step 2: Stop Kafka Producer
if [ -f "pids/producer.pid" ]; then
    PRODUCER_PID=$(cat pids/producer.pid)
    log_info "Stopping Kafka Producer (PID: $PRODUCER_PID)..."
    kill $PRODUCER_PID 2>/dev/null || log_warn "Producer already stopped"
    rm -f pids/producer.pid
else
    log_warn "Producer PID file not found"
fi

# Step 3: Stop Ingestion API
if [ -f "pids/api.pid" ]; then
    API_PID=$(cat pids/api.pid)
    log_info "Stopping Ingestion API (PID: $API_PID)..."
    kill $API_PID 2>/dev/null || log_warn "API already stopped"
    rm -f pids/api.pid
else
    log_warn "API PID file not found"
fi

# Step 4: Stop Redpanda
log_info "Stopping Redpanda..."
cd redpanda
docker-compose down
cd ..

# Step 5: Stop Graph Storage PostgreSQL
log_info "Stopping Graph Storage PostgreSQL..."
cd ai-analysis-service/deploy/storage-postgres
docker compose down
cd ../../..

# Step 6: Stop TT-RSS
log_info "Stopping TT-RSS..."
cd ttrss
docker-compose down
cd ..

echo ""
echo "========================================="
echo -e "${GREEN}All services stopped successfully!${NC}"
echo "========================================="
echo ""
