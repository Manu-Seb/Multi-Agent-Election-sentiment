from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage_service.db import StorageRepository
from storage_service.state import apply_change_event, empty_graph_state


def current_full_state(repo: StorageRepository) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    snap = repo.fetch_nearest_snapshot_before(now)
    if snap:
        state = snap["graph_state"]
        from_time = snap["snapshot_time"]
    else:
        state = empty_graph_state()
        from_time = datetime.fromtimestamp(0, tz=timezone.utc)

    for row in repo.fetch_changes_between(from_time, now):
        state = apply_change_event(state, row["changes"])
    return state


def replay_from_last_event(repo: StorageRepository, last_event_id: int, limit: int) -> list[dict[str, Any]]:
    rows = repo.fetch_changes_after_id(last_event_id, limit=limit)
    return [row["changes"] for row in rows]
