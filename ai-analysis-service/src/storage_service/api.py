from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import load_config
from .consumer import StorageConsumerWorker
from .db import StorageRepository
from .metrics import QUERY_LATENCY_SECONDS
from .snapshot_manager import SnapshotManager
from .state import ReconstructionCache, apply_change_event, build_entity_history, build_sentiment_trend, empty_graph_state
from .utils import parse_iso_time, parse_window, utc_now

cfg = load_config()
repo = StorageRepository(cfg)
snapshot_mgr = SnapshotManager(
    repo,
    every_n_changes=cfg.snapshot_every_n_changes,
    every_minutes=cfg.snapshot_every_minutes,
    retention_days=cfg.snapshot_retention_days,
)
consumer = StorageConsumerWorker(cfg, repo, snapshot_mgr)
cache = ReconstructionCache(cfg.reconstruction_cache_ttl_seconds, cfg.reconstruction_cache_size)

app = FastAPI(title="Graph Storage Service")


@app.on_event("startup")
def startup() -> None:
    consumer.start()


@app.on_event("shutdown")
def shutdown() -> None:
    consumer.stop()
    repo.close()


@app.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/state")
def get_state(time: str = Query(..., description="ISO-8601 timestamp")) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        at_time = parse_iso_time(time)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cache_key = f"state:{at_time.isoformat()}"
    cached = cache.get(cache_key)
    if cached:
        QUERY_LATENCY_SECONDS.observe(time.perf_counter() - started)
        return {"cached": True, **cached}

    nearest = repo.fetch_nearest_snapshot_before(at_time)
    if nearest:
        graph_state = nearest["graph_state"]
        from_time = nearest["snapshot_time"]
        base_snapshot_id = nearest["id"]
    else:
        graph_state = empty_graph_state()
        from_time = datetime.fromtimestamp(0, tz=timezone.utc)
        base_snapshot_id = None

    changes = repo.fetch_changes_between(from_time, at_time)
    for row in changes:
        graph_state = apply_change_event(graph_state, row["changes"])

    response = {
        "requested_time": at_time.isoformat(),
        "base_snapshot_id": base_snapshot_id,
        "applied_changes": len(changes),
        "state": graph_state,
    }
    cache.set(cache_key, response)
    QUERY_LATENCY_SECONDS.observe(time.perf_counter() - started)
    return {"cached": False, **response}


@app.get("/changes")
def get_changes(
    from_time: str = Query(..., alias="from"),
    to_time: str = Query(..., alias="to"),
    entity: str | None = Query(None),
) -> dict[str, Any]:
    started = time.perf_counter()
    rows = repo.fetch_changes(parse_iso_time(from_time), parse_iso_time(to_time), entity=entity)
    QUERY_LATENCY_SECONDS.observe(time.perf_counter() - started)
    return {"count": len(rows), "items": rows}


@app.get("/entity/{entity_id}/history")
def get_entity_history(entity_id: str, window: str = Query("24h")) -> dict[str, Any]:
    started = time.perf_counter()
    delta = parse_window(window)
    end = utc_now()
    begin = end - delta
    rows = repo.fetch_changes(begin, end, entity=entity_id)
    history = build_entity_history(rows, entity_id)
    QUERY_LATENCY_SECONDS.observe(time.perf_counter() - started)
    return {
        "entity": entity_id,
        "window": window,
        "points": history,
    }


@app.get("/metrics/sentiment-trend")
def sentiment_trend(
    interval: str = Query("1h"),
    from_time: str | None = Query(None, alias="from"),
    to_time: str | None = Query(None, alias="to"),
) -> dict[str, Any]:
    started = time.perf_counter()
    end = parse_iso_time(to_time) if to_time else utc_now()
    begin = parse_iso_time(from_time) if from_time else end - parse_window("24h")
    rows = repo.fetch_changes(begin, end)
    trend = build_sentiment_trend(rows, interval)
    QUERY_LATENCY_SECONDS.observe(time.perf_counter() - started)
    return {
        "from": begin.isoformat(),
        "to": end.isoformat(),
        "interval": interval,
        "points": trend,
    }
