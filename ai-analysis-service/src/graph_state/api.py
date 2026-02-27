from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query

from entity_graph.wire_models import EntitySentimentResultValue

from .store import InMemoryGraphStore
from .stream_worker import GraphStreamWorker

app = FastAPI(title="Graph State Service")
store = InMemoryGraphStore()
worker = GraphStreamWorker(store=store)


@app.on_event("startup")
async def startup() -> None:
    if os.getenv("GRAPH_STREAM_ENABLED", "true").lower() == "true":
        await worker.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    await worker.stop()


@app.get("/graph/current")
async def get_graph_current() -> dict:
    return await store.get_current_state()


@app.get("/graph/entity/{entity_id}")
async def get_graph_entity(entity_id: str) -> dict:
    item = await store.get_entity(entity_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")
    return item


@app.get("/graph/edges")
async def get_graph_edges(entity: str = Query(..., description="Entity id")) -> list[dict]:
    return await store.get_edges_for_entity(entity)


@app.post("/graph/update")
async def post_graph_update(payload: EntitySentimentResultValue) -> dict:
    result, processed_at, metadata = payload.to_domain()
    return await store.apply_result(
        result=result,
        observed_at=processed_at,
        extraction_ms=metadata.processing_time_ms,
    )


@app.get("/graph/metrics")
async def get_graph_metrics() -> dict:
    return await store.get_metrics()
