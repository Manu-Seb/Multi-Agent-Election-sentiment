from __future__ import annotations

import argparse
import asyncio
import json
import time

import websockets


async def run_client(url: str, duration_sec: int, idx: int) -> dict:
    received = 0
    errors = 0
    started = time.time()

    try:
        async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=10_000_000) as ws:
            while time.time() - started < duration_sec:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    _ = json.loads(msg)
                    received += 1
                except TimeoutError:
                    await ws.send(json.dumps({"action": "pong"}))
                except Exception:
                    errors += 1
    except Exception:
        errors += 1

    return {"client": idx, "received": received, "errors": errors}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://localhost:8030/ws")
    parser.add_argument("--clients", type=int, default=1000)
    parser.add_argument("--duration", type=int, default=30)
    args = parser.parse_args()

    tasks = [run_client(args.url, args.duration, i) for i in range(args.clients)]
    started = time.perf_counter()
    results = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - started

    total_received = sum(x["received"] for x in results)
    total_errors = sum(x["errors"] for x in results)

    print(f"clients={args.clients}")
    print(f"duration_sec={args.duration}")
    print(f"elapsed_sec={elapsed:.2f}")
    print(f"total_messages_received={total_received}")
    print(f"total_errors={total_errors}")
    print(f"messages_per_sec={total_received / max(0.001, elapsed):.2f}")


if __name__ == "__main__":
    asyncio.run(main())
