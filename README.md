# Election Sentiment Analysis System

A multi-agent system for ingesting, analyzing, and tracking sentiment from news articles related to elections.

## Architecture

```
TT-RSS (News Aggregator) 
    ↓
Ingestion API (FastAPI) ← Query articles via HTTP
    ↓
Kafka Producer → Redpanda (Event Bus)
    ↓
[Future: Sentiment Analysis Agents]
```

## Services

1. **TT-RSS** - RSS feed aggregator and reader (Port 181)
2. **Redpanda** - Kafka-compatible event streaming platform (Port 9092)
3. **Ingestion API** - FastAPI service for querying articles (Port 8000)
4. **Kafka Producer** - Polls TT-RSS and publishes to Redpanda

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.11+
- Virtual environment set up

### Setup

1. **Configure environment**:
   ```bash
   cp ingestion-service/.env.example ingestion-service/.env
   # Edit .env with your TT-RSS credentials
   ```

2. **Install Python dependencies**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r ingestion-service/requirements.txt
   ```

3. **Start all services**:
   ```bash
   ./start.sh
   ```

4. **Stop all services**:
   ```bash
   ./stop.sh
   ```

## Service URLs

- **TT-RSS UI**: http://localhost:181
  - Default credentials: admin/password (configure in TT-RSS)
- **Ingestion API**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs
- **Redpanda**: localhost:9092

## API Usage

### Fetch all articles
```bash
curl "http://localhost:8000/api/v1/articles"
```

### Search for specific articles
```bash
curl "http://localhost:8000/api/v1/articles?q=Kerala"
```

### Filter by date
```bash
curl "http://localhost:8000/api/v1/articles?q=elections&since=2026-01-01T00:00:00Z"
```

## Logs

- **API logs**: `tail -f logs/api.log`
- **Producer logs**: `tail -f logs/producer.log`

## Development

### Running components individually

```bash
# TT-RSS only
cd ttrss && docker-compose up -d

# Redpanda only
cd redpanda && docker-compose up -d

# API only
source venv/bin/activate
cd ingestion-service
uvicorn main:app --reload

# Producer only
source venv/bin/activate
python ingestion-service/producer.py
```

## Project Structure

```
.
├── ingestion-service/     # FastAPI app & Kafka producer
│   ├── main.py           # API endpoints
│   ├── producer.py       # Kafka producer
│   ├── services/         # TT-RSS client
│   ├── schemas.py        # Data models
│   └── transformers.py   # Data normalization
├── ttrss/                # TT-RSS docker-compose
├── redpanda/             # Redpanda docker-compose
├── start.sh              # Master startup script
└── stop.sh               # Master shutdown script
```
