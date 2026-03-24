# Election Sentiment Analysis System

This repository is an event-driven pipeline for ingesting election-related content, extracting entities and sentiment, building a live relationship graph, persisting graph history, and streaming updates to frontend clients.

For a full architecture walkthrough, see [architecture.md](/home/manuseb/codeWork/election-sentiment/architecture.md).

## What Runs Today

The current stack has these active components:

1. `ttrss/`
   TT-RSS source aggregator
2. `ingestion-service/`
   FastAPI API plus polling producer for TT-RSS and optional Bluesky search
3. `redpanda/`
   Kafka-compatible event bus
4. `ai-analysis-service/src/main.py`
   entity extraction, sentence-level sentiment, co-occurrence detection
5. `ai-analysis-service/src/graph_state/`
   in-memory graph state and graph delta generation
6. `ai-analysis-service/src/graph_algorithms/`
   secondary graph metrics like PageRank and communities
7. `ai-analysis-service/src/storage_service/`
   PostgreSQL persistence, snapshots, reconstruction APIs
8. `ai-analysis-service/src/websocket_service/`
   realtime WebSocket/SSE broadcast service
9. `frontend/`
   React UI
10. `frontend/stream_server.py`
   demo-oriented SSE bridge that visualizes the pipeline step by step

## Actual Data Flow

```text
TT-RSS -------------------\
                           \
                            -> ingestion-service/producer.py -> raw_ingestion
                           /
Bluesky search -----------/

raw_ingestion
  -> ai-analysis-service/src/main.py
  -> entity-sentiment

entity-sentiment
  -> graph_state/stream_worker.py -> graph-changes
  -> graph_algorithms/service.py  -> graph-metrics

graph-changes
  -> storage_service/consumer.py -> PostgreSQL + graph-snapshots
  -> websocket_service/kafka_consumer.py -> WebSocket/SSE clients

frontend/stream_server.py
  -> watches raw_ingestion + entity-sentiment + graph-changes
  -> fetches /graph/current
  -> emits staged SSE updates to the React app
```

## Required Configuration

### `ingestion-service/.env` is required

`./start.sh` will fail if [ingestion-service/.env.example](/home/manuseb/codeWork/election-sentiment/ingestion-service/.env.example)’s real `.env` file does not exist.

Create it with:

```bash
cp ingestion-service/.env.example ingestion-service/.env
```

You should then edit it for your environment, especially:

- `TTRSS_URL`
- `TTRSS_USER`
- `TTRSS_PASSWORD`
- `KAFKA_BROKER`

If you want Bluesky ingestion, also set:

- `BLUESKY_ENABLED=true`
- `BLUESKY_USERNAME`
- `BLUESKY_PASSWORD`
- `BLUESKY_SEARCH_TERMS`

### `ai-analysis-service/.env` is optional

The AI services can run without a real `.env` because they have code defaults and `start.sh` supplies several runtime values explicitly.

If you want to override defaults, create it with:

```bash
cp ai-analysis-service/.env.example ai-analysis-service/.env
```

This is optional. If the file is missing, `./start.sh` logs that built-in defaults will be used.

## Environment Notes

A few configuration details matter in practice:

- The ingestion producer defaults to `raw_ingestion`.
- The AI consumer code itself defaults `RAW_ARTICLES_TOPIC` to `raw-articles`.
- `./start.sh` explicitly overrides that and runs the processor with `RAW_ARTICLES_TOPIC=raw_ingestion`.

So the effective runtime topic wiring is correct even without `ai-analysis-service/.env`.

## Quick Start

### Prerequisites

- Docker with Compose support
- Python 3.11+
- ability to install local Python packages and spaCy model assets

### Start the full backend stack

1. Create the required ingestion env file:

```bash
cp ingestion-service/.env.example ingestion-service/.env
```

2. Optionally create the AI env file:

```bash
cp ai-analysis-service/.env.example ai-analysis-service/.env
```

3. Start everything:

```bash
./start.sh
```

What `./start.sh` does:

- starts Docker stacks for Redpanda, TT-RSS, PostgreSQL, and monitoring
- creates Redpanda topics
- creates or updates per-service virtual environments
- installs AI runtime and ML dependencies
- attempts to install `en_core_web_sm` if missing
- starts host-side Python services for ingestion, NLP, graph-state, graph algorithms, storage, and websocket broadcast

### Stop the backend stack

```bash
./stop.sh
```

`./stop.sh` stops the host Python services first, then tears down the Docker stacks.

## Service URLs

After `./start.sh`, the main backend endpoints are:

- TT-RSS UI: `http://localhost:181`
- Ingestion API: `http://localhost:8000`
- Ingestion API docs: `http://localhost:8000/docs`
- Graph State API: `http://localhost:8010`
- Storage API: `http://localhost:8020`
- WebSocket Broadcast API: `http://localhost:8030`
- Prometheus: `http://localhost:9090`
- Redpanda Kafka broker: `localhost:9092`
- PostgreSQL: `localhost:5433`

## Topics Used

The active pipeline uses these Redpanda topics:

- `raw_ingestion`
- `entity-sentiment`
- `entity-sentiment-dlq`
- `graph-changes`
- `graph-snapshots`
- `graph-metrics`

The bootstrap script in [redpanda/setup_topics.sh](/home/manuseb/codeWork/election-sentiment/redpanda/setup_topics.sh) creates:

- `raw_ingestion`
- `agent_insights`
- `system_commands`

`./start.sh` then ensures the rest of the analysis topics exist.

## Running the Frontend

The React app is not started by `./start.sh`.

From [frontend/](/home/manuseb/codeWork/election-sentiment/frontend):

```bash
npm install
npm run dev
```

The React app expects a stream endpoint at:

```text
http://localhost:8040/stream
```

To run that bridge:

```bash
cd frontend
uvicorn stream_server:app --host 0.0.0.0 --port 8040
```

The current frontend uses this bridge, not the general websocket service directly.

## Ingestion API Usage

Fetch articles from TT-RSS directly:

```bash
curl "http://localhost:8000/api/v1/articles"
```

Search TT-RSS articles:

```bash
curl "http://localhost:8000/api/v1/articles?q=Kerala"
```

Filter by time:

```bash
curl "http://localhost:8000/api/v1/articles?q=elections&since=2026-01-01T00:00:00Z"
```

Register a temporary Bluesky priority topic:

```bash
curl -X POST "http://localhost:8000/api/v1/bluesky/priority-topic" \
  -H "Content-Type: application/json" \
  -d '{"topic":"israel"}'
```

## Development Notes

### Source-specific defaults are not identical

The example env files are not perfect mirrors of every hardcoded default:

- `ingestion-service/.env.example` is oriented toward the compose/networked deployment shape
- many AI service defaults live only in code
- `RAW_ARTICLES_TOPIC` is a notable mismatch between AI code defaults and runtime startup wiring

If you need the exact effective runtime behavior, prefer:

1. [start.sh](/home/manuseb/codeWork/election-sentiment/start.sh)
2. the relevant `os.getenv(..., default)` calls in code
3. the `.env.example` files as override templates, not as the only source of truth

### Running pieces individually

Infra only:

```bash
cd redpanda && docker compose up -d
cd ttrss && docker compose up -d
cd postgres && docker compose up -d
```

Ingestion API only:

```bash
cd ingestion-service
uvicorn main:app --reload
```

Ingestion producer only:

```bash
cd ingestion-service
python producer.py
```

AI processor only:

```bash
cd /home/manuseb/codeWork/election-sentiment
PYTHONPATH=ai-analysis-service/src python ai-analysis-service/src/main.py
```

Graph state API only:

```bash
cd /home/manuseb/codeWork/election-sentiment
PYTHONPATH=ai-analysis-service/src uvicorn graph_state.api:app --host 0.0.0.0 --port 8010
```

Storage API only:

```bash
cd /home/manuseb/codeWork/election-sentiment
PYTHONPATH=ai-analysis-service/src uvicorn storage_service.api:app --host 0.0.0.0 --port 8020
```

WebSocket service only:

```bash
cd /home/manuseb/codeWork/election-sentiment
PYTHONPATH=ai-analysis-service/src uvicorn websocket_service.api:app --host 0.0.0.0 --port 8030
```

## Logs

`./start.sh` writes service logs under [logs/](/home/manuseb/codeWork/election-sentiment/logs).

Useful files include:

- `logs/ingestion-api.log`
- `logs/ingestion-producer.log`
- `logs/entity-sentiment-processor.log`
- `logs/graph-state.log`
- `logs/graph-algorithms.log`
- `logs/storage-service.log`
- `logs/websocket-service.log`

## Project Layout

```text
.
├── ingestion-service/
├── ai-analysis-service/
├── frontend/
├── redpanda/
├── ttrss/
├── postgres/
├── monitoring/
├── architecture.md
├── start.sh
└── stop.sh
```
