from __future__ import annotations

import asyncio

from websocket_service.manager import ConnectionManager


class DummyWebSocket:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def accept(self) -> None:
        return None

    async def send_text(self, payload: str) -> None:
        self.messages.append(payload)

    async def send_json(self, payload) -> None:
        self.messages.append(str(payload))


def test_connection_manager_broadcast_and_filtering() -> None:
    async def run() -> None:
        mgr = ConnectionManager(rate_limit_per_sec=100)
        ws1 = DummyWebSocket()
        ws2 = DummyWebSocket()

        await mgr.connect("c1", ws1, {"Alice"})
        await mgr.connect("c2", ws2, set())

        payload = '{"type":"change_batch"}'
        events = [{"entity_changes": [{"id": "Bob"}]}]
        sent, dropped = await mgr.broadcast(payload, events)
        assert sent == 1  # only c2 receives because c1 subscribed to Alice
        assert dropped == 0

        events2 = [{"entity_changes": [{"id": "Alice"}]}]
        sent2, _ = await mgr.broadcast(payload, events2)
        assert sent2 == 2

        metrics = await mgr.metrics()
        assert metrics["connected_clients"] == 2

    asyncio.run(run())
