from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone

from entity_graph.models import CoOccurrencePair, EntityMention, EntitySentimentResult
from graph_state.store import InMemoryGraphStore


def make_result(article_id: int, entity_pool: list[str]) -> EntitySentimentResult:
    chosen = random.sample(entity_pool, 4)
    sentiments = [random.uniform(-1.0, 1.0) for _ in chosen]

    entities = [
        EntityMention(
            text=name,
            type="PERSON",
            sentence=f"{name} appeared in article {article_id}",
            sentiment_score=score,
            confidence=0.9,
            char_start=0,
            char_end=len(name),
        )
        for name, score in zip(chosen, sentiments)
    ]

    pairs = [
        CoOccurrencePair(entity_1=chosen[i], entity_2=chosen[j], count=1)
        for i in range(len(chosen))
        for j in range(i + 1, len(chosen))
    ]

    return EntitySentimentResult(article_id=f"bench-{article_id}", entities=entities, co_occurrence_pairs=pairs)


async def run_benchmark(iterations: int = 5000) -> None:
    store = InMemoryGraphStore()
    entity_pool = [f"Entity-{i}" for i in range(500)]

    started = time.perf_counter()
    now = datetime.now(tz=timezone.utc)

    for i in range(iterations):
        result = make_result(i, entity_pool)
        await store.apply_result(result=result, observed_at=now)

    elapsed = time.perf_counter() - started
    updates_per_second = iterations / elapsed if elapsed > 0 else 0.0
    metrics = await store.get_metrics()

    print(f"iterations={iterations}")
    print(f"elapsed_sec={elapsed:.4f}")
    print(f"updates_per_second={updates_per_second:.2f}")
    print(f"node_count={metrics['node_count']}")
    print(f"edge_count={metrics['edge_count']}")
    print(f"memory_current_bytes={metrics['memory_current_bytes']}")
    print(f"memory_peak_bytes={metrics['memory_peak_bytes']}")
    print(f"memory_rss_kb={metrics['memory_rss_kb']}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
