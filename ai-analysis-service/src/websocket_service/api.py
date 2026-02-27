from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from storage_service.db import StorageRepository

from .auth import validate_jwt_placeholder
from .codec import encode_payload
from .config import load_config
from .kafka_consumer import KafkaChangeConsumer
from .manager import ConnectionManager
from .recovery import current_full_state, replay_from_last_event
from .redis_sync import RedisBroadcaster

cfg = load_config()
app = FastAPI(title="WebSocket Broadcast Service")
manager = ConnectionManager(rate_limit_per_sec=cfg.rate_limit_per_sec)
repo = StorageRepository(
    type("TempCfg", (), {"pg_min_conn": 1, "pg_max_conn": 8, "postgres_dsn": cfg.storage_postgres_dsn})
)

# Thread -> asyncio bridge queue
loop_ref: asyncio.AbstractEventLoop | None = None
incoming_queue: asyncio.Queue[dict[str, Any]] | None = None
consumer: KafkaChangeConsumer | None = None
redis_sync = RedisBroadcaster(cfg.redis_enabled, cfg.redis_url, cfg.redis_channel)

# SSE listeners
sse_clients: dict[str, asyncio.Queue[str]] = {}

# small buffer for debugging/recovery fallback
recent_events: deque[dict[str, Any]] = deque(maxlen=5000)


async def _enqueue_event(event: dict[str, Any]) -> None:
    if incoming_queue is not None:
        await incoming_queue.put(event)


def _enqueue_from_thread(event: dict[str, Any]) -> None:
    if loop_ref is None:
        return
    loop_ref.call_soon_threadsafe(asyncio.create_task, _enqueue_event(event))


def _on_kafka_event(event: dict[str, Any]) -> None:
    event["_source_instance"] = cfg.instance_id
    _enqueue_from_thread(event)
    redis_sync.publish(event)


def _on_redis_event(event: dict[str, Any]) -> None:
    if event.get("_source_instance") == cfg.instance_id:
        return
    _enqueue_from_thread(event)


@app.on_event("startup")
async def startup() -> None:
    global loop_ref, incoming_queue, consumer
    loop_ref = asyncio.get_running_loop()
    incoming_queue = asyncio.Queue(maxsize=10000)

    consumer = KafkaChangeConsumer(
        bootstrap_servers=cfg.kafka_bootstrap_servers,
        group_id=cfg.kafka_group_id,
        topic=cfg.graph_changes_topic,
        on_event=_on_kafka_event,
    )
    consumer.start()

    redis_sync.start_subscriber(_on_redis_event)

    asyncio.create_task(_broadcast_loop())
    asyncio.create_task(_heartbeat_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    if consumer:
        consumer.stop()
    redis_sync.stop()
    repo.close()


async def _heartbeat_loop() -> None:
    while True:
        await asyncio.sleep(cfg.heartbeat_seconds)
        await manager.heartbeat()


async def _broadcast_loop() -> None:
    assert incoming_queue is not None
    interval = cfg.batch_interval_ms / 1000.0

    while True:
        first = await incoming_queue.get()
        batch = [first]
        deadline = time.monotonic() + interval

        while len(batch) < cfg.max_batch_size and time.monotonic() < deadline:
            timeout = max(0.0, deadline - time.monotonic())
            try:
                nxt = await asyncio.wait_for(incoming_queue.get(), timeout=timeout)
                batch.append(nxt)
            except TimeoutError:
                break

        for event in batch:
            recent_events.append(event)

        payload = {"type": "change_batch", "events": batch, "count": len(batch), "ts": int(time.time() * 1000)}
        encoding, encoded = encode_payload(payload, cfg.compression_mode)

        if isinstance(encoded, bytes):
            # WS text API for browser compatibility: wrap binary as base64-ish list of ints.
            payload_json = json.dumps(
                {
                    "type": "compressed_batch",
                    "encoding": encoding,
                    "bytes": list(encoded),
                    "count": len(batch),
                }
            )
        else:
            payload_json = encoded

        sent, dropped = await manager.broadcast(payload_json, batch)

        # SSE push
        dead_sse = []
        for client_id, q in sse_clients.items():
            try:
                q.put_nowait(payload_json)
            except asyncio.QueueFull:
                dead_sse.append(client_id)
        for client_id in dead_sse:
            sse_clients.pop(client_id, None)


async def _auth_context(authorization: str | None = Header(default=None)) -> dict:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    return validate_jwt_placeholder(token, required=cfg.auth_required)


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    entities: str | None = Query(default=None, description="comma-separated entity ids"),
    last_event_id: int | None = Query(default=None),
):
    _ = validate_jwt_placeholder(token, required=cfg.auth_required)

    subscription_set = set(x.strip() for x in (entities or "").split(",") if x.strip())
    client_id = str(uuid.uuid4())
    state = await manager.connect(client_id, websocket, subscription_set)

    try:
        # Initial full state
        init_state = current_full_state(repo)
        await websocket.send_json({"type": "initial_state", "state": init_state})

        # Recovery by last event id
        if last_event_id is not None:
            replay = replay_from_last_event(repo, last_event_id=last_event_id, limit=cfg.replay_limit)
            await websocket.send_json({"type": "replay", "count": len(replay), "events": replay})

        while True:
            msg = await websocket.receive_json()
            action = msg.get("action")
            if action == "pong":
                state.last_heartbeat = time.time()
                continue
            if action == "subscribe":
                ents = set(str(x).strip() for x in msg.get("entities", []) if str(x).strip())
                await manager.set_subscriptions(client_id, ents)
                await websocket.send_json({"type": "subscribed", "entities": sorted(ents)})
                continue
            if action == "join_room":
                # Placeholder room support: map room name to entity:room style.
                room = str(msg.get("room", "")).strip()
                if room:
                    ents = set(state.subscriptions)
                    ents.add(room)
                    await manager.set_subscriptions(client_id, ents)
                    await websocket.send_json({"type": "joined_room", "room": room})
                continue
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(client_id)


@app.get("/sse")
async def sse_endpoint(
    entities: str | None = Query(default=None),
    auth: dict = Depends(_auth_context),
):
    _ = auth
    client_id = f"sse-{uuid.uuid4()}"
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
    sse_clients[client_id] = q

    async def event_stream():
        try:
            init_state = current_full_state(repo)
            yield f"event: initial_state\\ndata: {json.dumps({'type':'initial_state','state':init_state})}\\n\\n"
            while True:
                payload_json = await q.get()
                yield f"event: change_batch\\ndata: {payload_json}\\n\\n"
        finally:
            sse_clients.pop(client_id, None)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/health")
async def health() -> dict[str, Any]:
    metrics = await manager.metrics()
    return {"ok": True, "instance_id": cfg.instance_id, **metrics}
