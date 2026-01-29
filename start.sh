#!/bin/bash
# Master startup script for Election Sentiment Analysis System
# Starts: TT-RSS, Redpanda, Ingestion API, and Kafka Producer

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "========================================="
echo "Election Sentiment Analysis - Startup"
echo "========================================="

# Color codes for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Function to print colored messages
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if .env exists
if [ ! -f "ingestion-service/.env" ]; then
    log_error ".env file not found in ingestion-service/"
    log_info "Please copy .env.example to .env and configure it"
    exit 1
fi

# Step 1: Start TT-RSS
log_info "Starting TT-RSS services..."
cd ttrss
docker-compose up -d
cd ..
log_info "TT-RSS started on http://localhost:181"

# Step 2: Start Redpanda
log_info "Starting Redpanda..."
cd redpanda
docker-compose up -d
cd ..
log_info "Redpanda started on localhost:9092"

# Wait for services to be healthy
log_info "Waiting for services to be ready (30s)..."
sleep 30

# Step 3: Setup Kafka topics
log_info "Setting up Kafka topics..."
cd redpanda
bash setup_topics.sh || log_warn "Topic setup may have failed (topics might already exist)"
cd ..

# Step 4: Start Ingestion API (FastAPI)
log_info "Starting Ingestion API..."
source venv/bin/activate
cd ingestion-service
nohup uvicorn main:app --host 0.0.0.0 --port 8000 > ../logs/api.log 2>&1 &
API_PID=$!
echo $API_PID > ../pids/api.pid
cd ..
log_info "Ingestion API started on http://localhost:8000 (PID: $API_PID)"

# Wait for API to start
sleep 5

# Step 5: Start Kafka Producer
log_info "Starting Kafka Producer..."
nohup venv/bin/python ingestion-service/producer.py > logs/producer.log 2>&1 &
PRODUCER_PID=$!
echo $PRODUCER_PID > pids/producer.pid
log_info "Kafka Producer started (PID: $PRODUCER_PID)"

echo ""
echo "========================================="
echo -e "${GREEN}All services started successfully!${NC}"
echo "========================================="
echo ""
echo "Service URLs:"
echo "  - TT-RSS UI:       http://localhost:181"
echo "  - Ingestion API:   http://localhost:8000"
echo "  - API Docs:        http://localhost:8000/docs"
echo "  - Redpanda:        localhost:9092"
echo ""
echo "Logs:"
echo "  - API:             tail -f logs/api.log"
echo "  - Producer:        tail -f logs/producer.log"
echo ""
echo "To stop all services, run: ./stop.sh"
echo ""
