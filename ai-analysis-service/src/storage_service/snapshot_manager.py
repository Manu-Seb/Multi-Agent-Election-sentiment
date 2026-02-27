from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .db import StorageRepository
from .metrics import STORED_SNAPSHOTS
from .state import apply_change_event, empty_graph_state


class SnapshotManager:
    def __init__(
        self,
        repo: StorageRepository,
        *,
        every_n_changes: int,
        every_minutes: int,
        retention_days: int,
    ) -> None:
        self.repo = repo
        self.every_n_changes = max(1, every_n_changes)
        self.every_minutes = max(1, every_minutes)
        self.retention_days = retention_days

        self.current_state = empty_graph_state()
        self.changes_since_snapshot = 0
        self.last_snapshot_time: datetime | None = None
        self.last_event_id: int | None = None

    def on_change_event(self, event_id: int, event_time: datetime, changes: dict[str, Any]) -> None:
        self.current_state = apply_change_event(self.current_state, changes)
        self.changes_since_snapshot += 1
        self.last_event_id = event_id

        if self.last_snapshot_time is None:
            self._create_snapshot(event_time)
            return

        due_by_count = self.changes_since_snapshot >= self.every_n_changes
        due_by_time = event_time - self.last_snapshot_time >= timedelta(minutes=self.every_minutes)
        if due_by_count or due_by_time:
            self._create_snapshot(event_time)

    def on_external_snapshot(self, snapshot_time: datetime, last_event_id: int | None, graph_state: dict[str, Any]) -> None:
        self.repo.store_snapshot(snapshot_time=snapshot_time, last_event_id=last_event_id, graph_state=graph_state)
        self.current_state = graph_state
        self.last_snapshot_time = snapshot_time
        self.last_event_id = last_event_id
        self.changes_since_snapshot = 0
        self.repo.prune_snapshots(self.retention_days)
        STORED_SNAPSHOTS.inc()

    def _create_snapshot(self, snapshot_time: datetime) -> None:
        self.repo.store_snapshot(
            snapshot_time=snapshot_time,
            last_event_id=self.last_event_id,
            graph_state=self.current_state,
        )
        self.last_snapshot_time = snapshot_time
        self.changes_since_snapshot = 0
        self.repo.prune_snapshots(self.retention_days)
        STORED_SNAPSHOTS.inc()
