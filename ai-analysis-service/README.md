# AI Analysis Service

AI analysis pipeline for entity extraction, sentiment scoring, graph state maintenance, graph algorithm metrics, and schema management.

## What Is Implemented

### 1) Entity Sentiment Processor (`src/main.py`)
- Consumes article events from `raw_ingestion` / `raw-articles`.
- Supports ingestion payload normalization (legacy TT-RSS shape and normalized shape).
- Runs:
  - sentence-aware chunking and spaCy NER (`en_core_web_sm`)
  - DistilBERT sentiment scoring per relevant sentence
  - co-occurrence detection
- Produces `entity-sentiment` keyed by `article_id`.
- Supports DLQ (`entity-sentiment-dlq`) with stack traces.
- Uses Kafka transactions (`begin_transaction`, `send_offsets_to_transaction`, `commit_transaction`) for exactly-once flow assumptions.

### 2) Graph State Service (`src/graph_state/`)
- In-memory graph with O(1) dict lookups and `asyncio.Lock` safety.
- Node model: `id, type, mention_count, sentiment, centrality, first_seen, last_seen, hourly_rate`.
- Edge model: `source, target, strength, joint_sentiment, first_seen, last_strengthened`.
- FastAPI endpoints:
  - `GET /graph/current`
  - `GET /graph/entity/{id}`
  - `GET /graph/edges?entity={id}`
  - `POST /graph/update`
  - `GET /graph/metrics`
- Stream worker consumes `entity-sentiment` and produces `graph-changes`.

### 3) Graph Change Event Generation
- For each processed article:
  - snapshots affected nodes/edges before update
  - applies update
  - snapshots after update
  - computes deltas
- Change event includes:
  - `schema_version`, `timestamp`, `article_id`
  - `entity_changes` (mentions/sentiment/centrality old-new-delta)
  - `relationship_changes` (strength/joint sentiment old-new-delta)
  - `processing_metrics` (`extraction_ms`, `graph_update_ms`)
  - `validation` (replay old+delta ~= new)
- Threshold filtering supported by env vars.

### 4) Graph Algorithms Service (`src/graph_algorithms/`)
- Separate consumer service.
- Consumes `entity-sentiment`, computes metrics, emits `graph-metrics`.
- Includes:
  - incremental approximate PageRank
  - optional sampled betweenness approximation
  - Leiden-like local moving community detection (configurable interval)
  - relationship strength formula with recency and alignment
  - debug graph outputs (`.json` + `.dot`)

### 5) Avro + Wire Models + Schema Registry
- Avro files in `src/entity_graph/avro`.
- Pydantic wire models in `src/entity_graph/wire_models.py`.
- Schema registry helper in `src/entity_graph/schema_registry.py`.

### 6) Smoke Testing
- Script: `deploy/smoke_test_pipeline.sh`
- Starts processor (if needed), injects test records, waits, prints logs, and shows run-scoped output/DLQ records in readable format.

## Directory Layout

```text
ai-analysis-service/
├── src/
│   ├── entity_graph/
│   ├── processors/
│   ├── kafka/
│   ├── graph_state/
│   ├── graph_algorithms/
│   ├── main.py
│   └── graph_state_service.py
├── tests/
│   ├── test_graph_state/
│   └── test_graph_algorithms/
├── benchmarks/
│   ├── benchmark_graph_state.py
│   └── benchmark_graph_algorithms.py
├── deploy/
│   ├── register_schemas.py
│   └── smoke_test_pipeline.sh
├── graph_change_examples.json
├── Dockerfile
├── requirements.txt
└── setup.py
```

## Setup

```bash
cd /home/manuseb/codeWork/election-sentiment/ai-analysis-service
pip install --no-cache-dir -r requirements.txt
```

Optional editable install:

```bash
pip install -e .
```

## Run Commands

### A) Run Entity Sentiment Processor

```bash
cd /home/manuseb/codeWork/election-sentiment
PYTHONPATH=ai-analysis-service/src python ai-analysis-service/src/main.py
```

### B) Run Graph State API

```bash
cd /home/manuseb/codeWork/election-sentiment
PYTHONPATH=ai-analysis-service/src uvicorn graph_state.api:app --host 0.0.0.0 --port 8010
```

### C) Run Graph Algorithms Service

```bash
cd /home/manuseb/codeWork/election-sentiment
PYTHONPATH=ai-analysis-service/src python -m graph_algorithms.main
```

## Smoke Test (Recommended)

```bash
cd /home/manuseb/codeWork/election-sentiment
./ai-analysis-service/deploy/smoke_test_pipeline.sh
```

If an old processor is running and code changed, restart first:

```bash
kill $(cat pids/ai-analysis.pid) && rm -f pids/ai-analysis.pid
```

## Test Commands

### Graph State tests (direct, no pytest required)

```bash
cd /home/manuseb/codeWork/election-sentiment
PYTHONPATH=ai-analysis-service/src:ai-analysis-service python - <<'PY'
from tests.test_graph_state.test_store import (
    test_graph_update_and_lookup,
    test_metrics_include_memory_usage,
    test_change_event_contains_deltas_and_validation,
    test_change_event_threshold_filters_tiny_sentiment_updates,
)
from tests.test_graph_state.test_api import test_graph_update_and_endpoints

test_graph_update_and_lookup()
test_metrics_include_memory_usage()
test_change_event_contains_deltas_and_validation()
test_change_event_threshold_filters_tiny_sentiment_updates()
test_graph_update_and_endpoints()
print("graph-state tests passed")
PY
```

### Graph Algorithms tests

```bash
cd /home/manuseb/codeWork/election-sentiment
PYTHONPATH=ai-analysis-service/src:ai-analysis-service python - <<'PY'
from tests.test_graph_algorithms.test_engine import (
    test_relationship_strength_and_pagerank,
    test_community_changes,
    test_metrics_payload_shape,
)

test_relationship_strength_and_pagerank()
test_community_changes()
test_metrics_payload_shape()
print("graph algorithm tests passed")
PY
```

### Benchmarks

```bash
cd /home/manuseb/codeWork/election-sentiment
PYTHONPATH=ai-analysis-service/src python ai-analysis-service/benchmarks/benchmark_graph_state.py
PYTHONPATH=ai-analysis-service/src python ai-analysis-service/benchmarks/benchmark_graph_algorithms.py
```

## Topic Inspection Commands (Redpanda)

Create required topics (if missing):

```bash
docker exec redpanda rpk topic create entity-sentiment -p 3 -r 1 --if-not-exists
docker exec redpanda rpk topic create entity-sentiment-dlq -p 3 -r 1 --if-not-exists
docker exec redpanda rpk topic create graph-changes -p 3 -r 1 --if-not-exists
docker exec redpanda rpk topic create graph-metrics -p 3 -r 1 --if-not-exists
```

Consume outputs:

```bash
docker exec redpanda rpk topic consume entity-sentiment -n 5 -f '%k\t%v\n'
docker exec redpanda rpk topic consume entity-sentiment-dlq -n 5 -f '%k\t%v\n'
docker exec redpanda rpk topic consume graph-changes -n 5 -f '%k\t%v\n'
docker exec redpanda rpk topic consume graph-metrics -n 5 -f '%k\t%v\n'
```

## Key Environment Variables

### Processor
- `RAW_ARTICLES_TOPIC` (commonly `raw_ingestion` in this repo; alternative deployments may use `raw-articles`)
- `ENTITY_SENTIMENT_TOPIC` (default `entity-sentiment`)
- `ENTITY_SENTIMENT_DLQ_TOPIC` (default `entity-sentiment-dlq`)
- `KAFKA_BOOTSTRAP_SERVERS` (default `localhost:9092`)

### Graph State
- `GRAPH_STREAM_ENABLED` (default `true`)
- `GRAPH_STATE_GROUP_ID` (default `graph-state-service`)
- `GRAPH_CHANGES_TOPIC` (default `graph-changes`)
- `GRAPH_CHANGES_SCHEMA_VERSION` (default `1`)
- `GRAPH_STATE_BATCH_SIZE` (default `16`)
- `GRAPH_CHANGE_*_THRESHOLD` (delta filtering)

### Graph Algorithms
- `GRAPH_ALGO_GROUP_ID`
- `GRAPH_METRICS_TOPIC` (default `graph-metrics`)
- `ALGO_PAGERANK_DAMPING` (default `0.85`)
- `ALGO_PAGERANK_ITERATIONS` (default `20`)
- `ALGO_COMMUNITY_INTERVAL` (default `100`)
- `ALGO_BETWEENNESS_ENABLED` (default `true`)
- `ALGO_BETWEENNESS_SAMPLES` (default `16`)
- `ALGO_VISUALIZATION_ENABLED` (default `true`)
- `ALGO_VISUALIZATION_DIR` (default `./logs/graph-debug`)

## Troubleshooting

- If smoke test shows old behavior, restart processor PID file target before rerun.
- If output appears empty but service is running, check topic name alignment (`raw_ingestion` vs `raw-articles`).
- Old DLQ entries can appear in recent windows; smoke script now uses run-scoped keys for clarity.
- First model load can be slow due to Hugging Face model download/cache warmup.
