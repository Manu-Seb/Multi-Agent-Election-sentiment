from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

try:
    from fastapi import WebSocket
except ModuleNotFoundError:
    WebSocket = Any  # type: ignore[misc,assignment]


@dataclass(slots=True)
class ClientState:
    client_id: str
    websocket: WebSocket
    connected_at: float
    subscriptions: set[str] = field(default_factory=set)
    topic_filter: str = ""
    topic_terms: tuple[str, ...] = ()
    rooms: set[str] = field(default_factory=set)
    last_heartbeat: float = field(default_factory=time.time)
    sent_count: int = 0
    dropped_count: int = 0
    window_started: float = field(default_factory=time.time)
    sent_in_window: int = 0


class ConnectionManager:
    def __init__(self, rate_limit_per_sec: int) -> None:
        self._clients: dict[str, ClientState] = {}
        self._rooms: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()
        self.rate_limit_per_sec = max(1, rate_limit_per_sec)

    @staticmethod
    def _topic_terms(topic_filter: str) -> tuple[str, ...]:
        terms = []
        for raw in topic_filter.lower().split():
            token = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
            if len(token) >= 3:
                terms.append(token)
        return tuple(terms)

    async def connect(
        self, client_id: str, websocket: WebSocket, subscriptions: set[str], topic_filter: str
    ) -> ClientState:
        await websocket.accept()
        state = ClientState(client_id=client_id, websocket=websocket, connected_at=time.time())
        state.subscriptions = set(subscriptions)
        state.topic_filter = topic_filter.strip()
        state.topic_terms = self._topic_terms(topic_filter)

        async with self._lock:
            self._clients[client_id] = state
            for entity in subscriptions:
                room = f"entity:{entity}"
                state.rooms.add(room)
                self._rooms.setdefault(room, set()).add(client_id)

        return state

    async def disconnect(self, client_id: str) -> None:
        async with self._lock:
            state = self._clients.pop(client_id, None)
            if state is None:
                return
            for room in state.rooms:
                members = self._rooms.get(room)
                if not members:
                    continue
                members.discard(client_id)
                if not members:
                    self._rooms.pop(room, None)

    async def set_subscriptions(self, client_id: str, subscriptions: set[str]) -> None:
        async with self._lock:
            state = self._clients.get(client_id)
            if state is None:
                return
            for room in list(state.rooms):
                members = self._rooms.get(room)
                if members:
                    members.discard(client_id)
                    if not members:
                        self._rooms.pop(room, None)
            state.rooms.clear()
            state.subscriptions = set(subscriptions)
            for entity in subscriptions:
                room = f"entity:{entity}"
                state.rooms.add(room)
                self._rooms.setdefault(room, set()).add(client_id)

    @staticmethod
    def _passes_rate_limit(state: ClientState, per_sec: int) -> bool:
        now = time.time()
        if now - state.window_started >= 1.0:
            state.window_started = now
            state.sent_in_window = 0
        if state.sent_in_window >= per_sec:
            state.dropped_count += 1
            return False
        state.sent_in_window += 1
        return True

    @staticmethod
    def _event_matches_topic(state: ClientState, events: list[dict[str, Any]]) -> bool:
        if not state.topic_terms:
            return True
        for event in events:
            article_id = str(event.get("article_id", "")).lower()
            entity_text = " ".join(str(ch.get("id", "")) for ch in event.get("entity_changes", []))
            haystack = f"{article_id} {entity_text}".lower()
            if any(term in haystack for term in state.topic_terms):
                return True
        return False

    @staticmethod
    def _event_matches_subscriptions(state: ClientState, events: list[dict[str, Any]]) -> bool:
        if not state.subscriptions:
            return True
        for event in events:
            for change in event.get("entity_changes", []):
                if change.get("id") in state.subscriptions:
                    return True
        return False

    async def broadcast(self, payload_json: str, events: list[dict[str, Any]]) -> tuple[int, int]:
        async with self._lock:
            clients = list(self._clients.values())

        sent = 0
        dropped = 0
        for state in clients:
            if not self._event_matches_topic(state, events):
                continue
            if not self._event_matches_subscriptions(state, events):
                continue
            if not self._passes_rate_limit(state, self.rate_limit_per_sec):
                dropped += 1
                continue
            try:
                await state.websocket.send_text(payload_json)
                state.sent_count += 1
                sent += 1
            except Exception:  # noqa: BLE001
                dropped += 1
                await self.disconnect(state.client_id)

        return sent, dropped

    async def heartbeat(self) -> None:
        async with self._lock:
            clients = list(self._clients.values())
        for state in clients:
            try:
                await state.websocket.send_json({"type": "heartbeat", "ts": int(time.time() * 1000)})
            except Exception:  # noqa: BLE001
                await self.disconnect(state.client_id)

    async def metrics(self) -> dict[str, Any]:
        async with self._lock:
            clients = list(self._clients.values())
        total_sent = sum(c.sent_count for c in clients)
        total_dropped = sum(c.dropped_count for c in clients)
        return {
            "connected_clients": len(clients),
            "messages_sent": total_sent,
            "messages_dropped": total_dropped,
            "drop_rate": (total_dropped / max(1, total_sent + total_dropped)),
        }
