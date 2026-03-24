# Election Sentiment System Architecture

This repository is a multi-stage streaming system that ingests election-related content from TT-RSS and Bluesky, pushes normalized records into Redpanda, runs NLP and graph-building processors, stores graph history in PostgreSQL, and exposes the results to frontend clients through streaming APIs.

The architecture is split into six major layers:

1. Source ingestion
2. Event transport through Redpanda
3. NLP enrichment
4. Graph construction and graph analytics
5. Persistence and historical reconstruction
6. Frontend delivery

## 1. High-Level Data Flow

```text
TT-RSS -------------------\
                           \
                            -> ingestion-service/producer.py -> Redpanda topic: raw_ingestion
                           /
Bluesky search -----------/

raw_ingestion
  -> ai-analysis-service/src/main.py
  -> topic: entity-sentiment

entity-sentiment
  -> graph_state/stream_worker.py -> topic: graph-changes
  -> graph_algorithms/service.py  -> topic: graph-metrics

graph-changes
  -> storage_service/consumer.py  -> PostgreSQL graph_change_events
  -> websocket_service/kafka_consumer.py -> WebSocket/SSE broadcast

storage_service/snapshot_manager.py
  -> topic: graph-snapshots
  -> PostgreSQL graph_snapshots

Frontend
  -> current demo path: frontend/stream_server.py SSE endpoint
  -> production-style realtime path: websocket_service/api.py
```

## 2. Infrastructure Components

### Redpanda

Redpanda is the central event bus. The repo uses it as a Kafka-compatible streaming backbone. It runs from [redpanda/docker-compose.yml](/home/manuseb/codeWork/election-sentiment/redpanda/docker-compose.yml) and exposes Kafka on `localhost:9092`.

The repo creates and uses these important topics:

- `raw_ingestion`: normalized raw articles/posts from TT-RSS and Bluesky
- `entity-sentiment`: NLP-enriched article results with entities and sentiment
- `entity-sentiment-dlq`: failed NLP records
- `graph-changes`: per-article graph delta events
- `graph-snapshots`: periodic materialized graph snapshots
- `graph-metrics`: algorithmic graph metrics such as PageRank and communities

### PostgreSQL

PostgreSQL is used only for graph history persistence, not for the primary online graph state. It runs from [postgres/docker-compose.yml](/home/manuseb/codeWork/election-sentiment/postgres/docker-compose.yml) on `localhost:5433`.

The schema is initialized by [001_schema.sql](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/deploy/storage-postgres/initdb/001_schema.sql):

- `graph_change_events`
  - one row per graph delta event
  - stores `event_time`, `article_id`, and full change payload in `JSONB`
- `graph_snapshots`
  - stores periodic full graph snapshots in `JSONB`
  - links to the last applied change event

## 3. Source Ingestion Layer

The ingestion layer lives in `ingestion-service/`.

### 3.1 TT-RSS ingestion

The TT-RSS client in [ttrss.py](/home/manuseb/codeWork/election-sentiment/ingestion-service/services/ttrss.py) logs in with the configured TT-RSS API credentials and calls `getHeadlines` against feed `-4`, which means all articles. The service supports two access patterns:

- `main.py`: ad hoc read/search API for querying TT-RSS content directly
- `producer.py`: continuous polling worker that publishes new articles into Redpanda

In the polling worker:

- `get_session_id()` authenticates against TT-RSS
- `fetch_ttrss_articles()` fetches the latest headlines with article body excerpts
- `normalize_ttrss_article()` converts TT-RSS records into the shared article model
- `fetch_full_content()` in [content_fetcher.py](/home/manuseb/codeWork/election-sentiment/ingestion-service/services/content_fetcher.py) fetches the article URL itself and extracts cleaner plain text using `trafilatura`

This matters architecturally because TT-RSS provides the discovery layer, but the project tries to enrich the article body with full text from the original article URL before shipping it downstream.

### 3.2 Bluesky ingestion

Bluesky enters through the same polling worker in [producer.py](/home/manuseb/codeWork/election-sentiment/ingestion-service/producer.py). It is optional and controlled by environment variables in [ingestion-service/.env.example](/home/manuseb/codeWork/election-sentiment/ingestion-service/.env.example).

The Bluesky path works like this:

- `active_bluesky_queries()` merges:
  - default search terms from `BLUESKY_SEARCH_TERMS`
  - temporary priority topics registered at runtime
- `create_bluesky_fetcher()` creates a search client if Bluesky is enabled
- `fetch_bluesky_posts()` performs the search
- `normalize_bluesky_post()` maps each Bluesky post into the same article schema used by TT-RSS

The system also persists Bluesky deduplication state in `producer.bluesky.state.json`, so repeated searches do not republish the same post IDs.

### 3.3 Priority topics from the frontend

The ingestion API in [ingestion-service/main.py](/home/manuseb/codeWork/election-sentiment/ingestion-service/main.py) exposes `POST /api/v1/bluesky/priority-topic`. When the frontend starts tracking a topic, it can register that topic so the Bluesky worker searches it before or alongside the static environment-configured queries.

This creates a feedback loop:

1. User enters a topic in the frontend
2. Frontend helper calls the ingestion API
3. Ingestion worker updates the active Bluesky search set
4. New Bluesky posts for that topic begin flowing into `raw_ingestion`

### 3.4 Shared ingestion payload

Both TT-RSS and Bluesky are normalized into a common payload shape before being published. The ingestion-service schema in [schemas.py](/home/manuseb/codeWork/election-sentiment/ingestion-service/schemas.py) includes:

- `id`
- `title`
- `content`
- `link`
- `feed_id`
- `feed_title`
- `published_at`
- `author`
- `tags`

The producer also adds `source` values such as `ttrss` or `bluesky`.

This normalization step is critical because every downstream service assumes it is reading one stream of article-like documents, regardless of whether the original source was RSS or social search.

## 4. Redpanda Transport Layer

Redpanda is the decoupling point between services. Each stage consumes from one topic and emits to another topic, so ingestion, NLP, graph updates, storage, and broadcast can scale or restart independently.

Important properties of the transport design:

- ingestion publishes raw documents into `raw_ingestion`
- NLP reads `raw_ingestion` and writes `entity-sentiment`
- graph-state reads `entity-sentiment` and writes `graph-changes`
- storage reads `graph-changes` and writes snapshots to `graph-snapshots`
- websocket broadcast reads `graph-changes` directly for low-latency push
- graph algorithms reads `entity-sentiment` independently, so it does not interfere with the main graph-state path

This is a fan-out architecture, not a single linear chain. Once `entity-sentiment` exists, multiple services can consume it for different purposes.

## 5. NLP Enrichment Layer

The NLP processor is [ai-analysis-service/src/main.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/main.py).

### 5.1 Input contract

The processor consumes from `RAW_ARTICLES_TOPIC`, which defaults to `raw-articles` in the code, but the startup script explicitly sets it to `raw_ingestion`. This is the actual runtime alignment in [start.sh](/home/manuseb/codeWork/election-sentiment/start.sh).

The processor accepts:

- the newer normalized `ArticleValue` wire format
- the current legacy ingestion payload from `ingestion-service/producer.py`

That backward-compatibility logic is implemented in `_parse_article_value()`.

### 5.2 Entity extraction

Entity extraction is handled by [entity_extractor.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/processors/entity_extractor.py).

The extractor:

- uses a lightweight `sentencizer` to split the article into sentences
- builds overlapping sentence chunks so long articles do not exceed model comfort
- runs spaCy NER (`en_core_web_sm`) on each chunk
- deduplicates entities by text, type, and character offsets
- maps every entity mention back to:
  - exact sentence index
  - original sentence text
  - global start/end character offsets

This sentence-aware mapping is important because sentiment is not computed for the entire article first. It is computed for the sentences that contain entity mentions.

### 5.3 Sentiment analysis

Sentiment scoring is handled by [sentiment_analyzer.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/processors/sentiment_analyzer.py).

The processor:

- loads the Hugging Face sentiment pipeline
- defaults to `distilbert-base-uncased-finetuned-sst-2-english`
- truncates each input sentence to 512 tokens/characters worth of model input
- scores the unique sentences that contain entities
- converts model output to signed scores:
  - positive label -> positive score
  - negative label -> negative score
  - confidence is preserved separately

Each entity mention then inherits the sentiment score and confidence of the sentence in which it appeared.

### 5.4 Co-occurrence extraction

Co-occurrence is computed in [co_occurrence.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/processors/co_occurrence.py).

The rules are:

- entities in the same sentence form pairs
- entities in adjacent sentences can also form pairs
- each pair accumulates a count
- self-pairs are excluded
- pairs are normalized so `(A, B)` and `(B, A)` become one edge candidate

This produces the relational signal used later in graph construction.

### 5.5 NLP output event

After extraction and scoring, the processor emits an `EntitySentimentResultValue` into `entity-sentiment`. The event contains:

- `article_id`
- `processed_at`
- `entities`
  - entity text
  - entity type
  - sentence
  - sentiment score
  - confidence
  - character offsets
- `co_occurrence_pairs`
- `processing_metadata`
  - processing time
  - model versions

Failures are sent to `entity-sentiment-dlq`.

## 6. Graph Construction Layer

The main online graph is maintained by the graph-state service in `ai-analysis-service/src/graph_state/`.

### 6.1 Graph state model

The graph model is defined in [graph_state/models.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/graph_state/models.py).

Each node stores:

- `id`
- `type`
- `mention_count`
- `sentiment`
- `centrality`
- `first_seen`
- `last_seen`
- `hourly_rate`

Each edge stores:

- `source`
- `target`
- `strength`
- `joint_sentiment`
- `first_seen`
- `last_strengthened`

The online graph is kept entirely in memory inside `InMemoryGraphStore`.

### 6.2 How graph updates are applied

The core logic lives in [graph_state/store.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/graph_state/store.py).

For each `entity-sentiment` event:

1. The store computes which nodes and edges will be affected.
2. It snapshots the affected nodes and edges before mutation.
3. It upserts nodes from entity mentions.
4. It upserts edges from co-occurrence pairs.
5. It recomputes centrality for impacted nodes.
6. It snapshots the affected nodes and edges again.
7. It derives change deltas.
8. It validates that `old + delta ~= new`.
9. It returns a graph-change event.

### 6.3 Node update semantics

For nodes:

- a new entity creates a new node
- repeated mentions increment `mention_count`
- sentiment is smoothed using an exponential weighted update:
  - `new_sentiment = old * 0.95 + incoming * 0.05`
- `last_seen` is updated
- `hourly_rate` is computed from a rolling one-hour mention window

This means node sentiment is not a raw average of all mentions. It is a smoothed moving value that reacts gradually to new evidence.

### 6.4 Edge update semantics

For edges:

- a co-occurrence pair creates or updates an undirected edge
- edge keys are normalized alphabetically to prevent duplicate directional edges
- `strength` is also smoothed with an exponential weighted update
- `joint_sentiment` is based on the mean sentiment of the two endpoint nodes
- neighbor sets are maintained to support centrality calculations

### 6.5 Centrality

Centrality in the online graph-state service is simple degree centrality:

- `centrality = degree / (total_nodes - 1)`

Only impacted nodes are recalculated on each update.

### 6.6 Graph-change events

The graph-state worker in [stream_worker.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/graph_state/stream_worker.py) consumes `entity-sentiment`, calls `apply_result()`, and publishes the resulting change event to `graph-changes`.

Each change event includes:

- `schema_version`
- `timestamp`
- `article_id`
- `entity_changes`
  - old/new mention counts
  - old/new sentiment
  - old/new centrality
  - deltas for each
- `relationship_changes`
  - old/new strength
  - old/new joint sentiment
  - deltas for each
- `processing_metrics`
  - extraction time
  - graph update time
- `validation`
  - whether replaying the delta matches the final post-update state
- `node_count`
- `edge_count`

This is the core event that both persistence and realtime delivery consume.

### 6.7 Graph state API

The graph-state FastAPI app in [graph_state/api.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/graph_state/api.py) exposes the in-memory live graph:

- `GET /graph/current`
- `GET /graph/entity/{entity_id}`
- `GET /graph/edges?entity={id}`
- `POST /graph/update`
- `GET /graph/metrics`

This API is the live in-memory view, not the historical persisted view.

## 7. Parallel Graph Analytics Layer

The graph algorithms service in `ai-analysis-service/src/graph_algorithms/` is separate from the online graph-state service.

It consumes `entity-sentiment` independently and computes richer graph metrics without affecting the core graph update path.

### 7.1 Why it is separate

This separation prevents expensive analytics from slowing the main graph-change stream. The graph-state service is optimized for fast mutation and delta generation. The graph-algorithms service is optimized for secondary analytics.

### 7.2 Metrics computed

The engine in [graph_algorithms/engine.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/graph_algorithms/engine.py) computes:

- incremental PageRank on an affected subgraph
- approximate betweenness centrality
- Leiden-like community detection on intervals
- relationship strength using:
  - proximity factor
  - recency decay
  - sentiment alignment
- optional debug graph exports as `.json` and `.dot`

It publishes summarized metric payloads to `graph-metrics`.

This topic is currently an analytics branch. The current frontend in this repo does not directly render it.

## 8. Persistence Layer in PostgreSQL

Persistence is implemented in `ai-analysis-service/src/storage_service/`.

### 8.1 What gets stored

The storage consumer in [storage_service/consumer.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/storage_service/consumer.py) subscribes to:

- `graph-changes`
- `graph-snapshots`

For each `graph-changes` event:

- it converts the event timestamp into a database timestamp
- inserts a row into `graph_change_events`
- hands the change event to the snapshot manager

For each `graph-snapshots` event:

- it ignores snapshots published by the same storage instance
- it stores externally published snapshots
- it updates the current reconstruction baseline

### 8.2 Repository behavior

Database access is implemented in [storage_service/db.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/storage_service/db.py).

The repository uses:

- a threaded PostgreSQL connection pool
- retry logic around writes and reads
- `JSONB` storage for both change events and snapshots
- snapshot compression before persistence

Key methods include:

- `store_change_event()`
- `store_snapshot()`
- `fetch_nearest_snapshot_before()`
- `fetch_changes_between()`
- `fetch_changes()`
- `fetch_recent_articles()`

### 8.3 Snapshot strategy

Snapshots are coordinated by [snapshot_manager.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/storage_service/snapshot_manager.py).

The manager keeps an in-memory reconstruction of the graph and creates a new snapshot when:

- a configured number of change events has passed, or
- a configured amount of time has elapsed

When a snapshot is created:

1. The current reconstructed graph state is stored in PostgreSQL.
2. The same snapshot is published back to `graph-snapshots`.
3. Old snapshots are pruned according to retention settings.

This design allows:

- durable historical reconstruction
- cross-instance snapshot sharing
- faster recovery than replaying the entire history from day one

### 8.4 Historical reconstruction

Historical state reconstruction is implemented in [storage_service/state.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/storage_service/state.py).

The reconstruction flow for `GET /state?time=...` is:

1. Find the latest snapshot at or before the requested time.
2. If no snapshot exists, start from an empty graph.
3. Fetch all change events after that snapshot and before the requested time.
4. Apply each change event in order.
5. Return the reconstructed graph.

This means PostgreSQL is the source of truth for historical state, while the graph-state service is the source of truth for the current in-memory live state.

### 8.5 Storage API

The storage API in [storage_service/api.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/storage_service/api.py) exposes:

- `GET /state`
- `GET /changes`
- `GET /entity/{entity_id}/history`
- `GET /metrics/sentiment-trend`
- `GET /articles/recent`
- `GET /metrics`
- `GET /health`

These endpoints are designed for historical and analytical queries, not just live stream display.

## 9. Realtime Delivery Layer

There are two realtime delivery paths in this repo.

### 9.1 General-purpose websocket broadcast service

The more production-oriented realtime path is `ai-analysis-service/src/websocket_service/`.

The broadcast service:

- consumes `graph-changes`
- batches events for efficient fanout
- serves WebSocket clients at `/ws`
- serves SSE clients at `/sse`
- supports:
  - topic filtering
  - entity subscriptions
  - replay from `last_event_id`
  - heartbeats
  - optional Redis cross-instance fanout

Important implementation files:

- [websocket_service/api.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/websocket_service/api.py)
- [websocket_service/manager.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/websocket_service/manager.py)
- [websocket_service/kafka_consumer.py](/home/manuseb/codeWork/election-sentiment/ai-analysis-service/src/websocket_service/kafka_consumer.py)

This service also uses PostgreSQL to serve:

- initial full state
- replay data for reconnecting clients

So the websocket layer combines:

- low-latency live events from Redpanda
- durable catch-up/recovery from PostgreSQL

### 9.2 Frontend demo stream server

The current React frontend in this repo is wired to [frontend/stream_server.py](/home/manuseb/codeWork/election-sentiment/frontend/stream_server.py), not directly to the websocket service.

This helper server is a specialized SSE bridge that:

- listens to `raw_ingestion`
- waits for matching `entity-sentiment`
- waits for matching `graph-changes`
- fetches current graph state from the graph-state API
- emits a staged event sequence to the browser

The browser sees the pipeline as four explicit steps:

1. `step1`: raw article accepted/rejected by topic filter
2. `step2`: entities and article-level mean sentiment
3. `step3`: graph delta event
4. `step4`: current graph snapshot from the graph-state API

Architecturally, this stream server is a visualization adapter. It is not the core streaming platform. It exists to make the pipeline easy to inspect article by article in the UI.

## 10. Frontend Layer

The frontend is a Vite + React app in `frontend/`.

### 10.1 Entry point

The React app starts in [frontend/src/main.jsx](/home/manuseb/codeWork/election-sentiment/frontend/src/main.jsx) and renders [frontend/src/App.jsx](/home/manuseb/codeWork/election-sentiment/frontend/src/App.jsx).

### 10.2 What the frontend does

The UI asks the user for a topic, then opens an `EventSource` connection to the stream server:

- default URL: `http://localhost:8040/stream`
- query param: `topic=<user topic>`

When the topic is submitted:

1. the frontend opens the SSE stream
2. the stream server attempts to register the same topic as a Bluesky priority topic
3. the frontend starts receiving staged pipeline events

### 10.3 How pipeline data is presented

The UI stores article progress by `article_id` and displays:

- current connection state
- current article processing state
- latest graph state
- historical article cards

It renders:

- source type badges
- filter decision
- raw NER and sentiment payloads
- graph delta payloads
- current top nodes and edges from the graph-state API snapshot

So the frontend is not only a dashboard. It is also a pipeline explainer that visualizes the transformation from raw content to graph updates.

## 11. Runtime Orchestration

The startup script [start.sh](/home/manuseb/codeWork/election-sentiment/start.sh) is the best description of the expected runtime topology.

It starts:

- Redpanda
- TT-RSS
- PostgreSQL
- monitoring stack
- ingestion API
- ingestion producer
- entity sentiment processor
- graph state service
- graph algorithms service
- storage service
- websocket broadcast service

It also ensures topic creation and aligns environment variables so the processor reads from `raw_ingestion`.

One practical detail: the React frontend and `frontend/stream_server.py` are not started by `start.sh`. They are separate developer-facing components for visualization.

## 12. End-to-End Walkthrough

Here is the full path for one article or post.

### 12.1 TT-RSS path

1. TT-RSS returns a headline/article record.
2. The ingestion worker fetches fuller article content from the article URL.
3. The record is normalized and published to `raw_ingestion`.
4. The NLP processor extracts entities and sentiment-bearing sentences.
5. The processor computes co-occurrence pairs and publishes `entity-sentiment`.
6. The graph-state worker updates the in-memory graph and publishes `graph-changes`.
7. The storage worker stores the change in PostgreSQL and may create a new snapshot.
8. The websocket service can broadcast the change immediately.
9. The frontend stream server can also observe the raw input, the NLP output, the graph delta, and the latest graph state, then send that staged sequence to the browser.

### 12.2 Bluesky path

1. A configured or frontend-prioritized topic is searched on Bluesky.
2. Matching posts are normalized into the same article schema as TT-RSS items.
3. Those posts are published to `raw_ingestion`.
4. The rest of the pipeline is identical to the TT-RSS path.

This symmetry is a major design choice: RSS articles and Bluesky posts become the same downstream object class as early as possible.

## 13. Architectural Boundaries and Responsibilities

Each subsystem has a specific responsibility:

- `ingestion-service`
  - source connectivity
  - normalization
  - source-specific deduplication
- `Redpanda`
  - decoupled transport
  - fan-out between processing stages
- `src/main.py` in ai-analysis-service
  - NLP enrichment
  - entity and sentiment extraction
- `graph_state`
  - online mutable graph
  - per-article graph delta generation
- `graph_algorithms`
  - secondary graph metrics and community analysis
- `storage_service`
  - durable graph history
  - snapshots
  - state reconstruction APIs
- `websocket_service`
  - generic realtime client fanout
  - reconnect and replay support
- `frontend/stream_server.py`
  - demo-oriented staged visualization bridge
- `frontend`
  - human-facing live explanation and graph inspection UI

## 14. Most Important Design Decisions

### Normalize early

TT-RSS and Bluesky are normalized into one downstream event type almost immediately. That keeps every later service source-agnostic.

### Stream everything through Redpanda

Each stage is independently restartable and scalable because communication happens through topics, not direct service-to-service calls.

### Separate live state from historical state

The graph-state service is optimized for low-latency in-memory updates. PostgreSQL is optimized for durable history and replay.

### Represent graph evolution as deltas

`graph-changes` is the core integration contract. It lets the system:

- persist history compactly
- replay changes
- broadcast updates in realtime
- derive snapshots later

### Keep advanced analytics off the hot path

PageRank, betweenness, and community detection run in a separate consumer so the main graph update loop stays fast.

## 15. Current Frontend Path vs Intended Platform Path

There are effectively two frontend-facing models in the repo:

- current demo path
  - React app -> `frontend/stream_server.py` -> Redpanda + graph-state API + ingestion API
- broader platform path
  - client -> `websocket_service/api.py` -> Redpanda live events + PostgreSQL recovery

The first path is better for explaining the pipeline step by step.
The second path is better for a generalized realtime application with reconnect/replay behavior.

## 16. Summary

At a system level, this project is an event-driven election intelligence pipeline:

- TT-RSS and Bluesky provide raw election-related content
- ingestion normalizes that content into one raw article stream
- Redpanda distributes the stream to independent processors
- the AI processor performs NER, sentence-level sentiment scoring, and co-occurrence detection
- the graph-state service converts those NLP results into a live entity relationship graph and emits graph deltas
- the storage service persists those deltas and snapshots in PostgreSQL for historical reconstruction
- the websocket service and frontend stream server expose the results to users in realtime
- the frontend visualizes both the current graph and the step-by-step transformation from content to sentiment graph

That is the full architecture implemented by the code in this repository today.
