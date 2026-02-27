from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg2
from psycopg2.extras import Json, RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from .config import StorageConfig
from .utils import compress_json_payload, decompress_json_payload, retry


class StorageRepository:
    def __init__(self, cfg: StorageConfig) -> None:
        self._pool = ThreadedConnectionPool(cfg.pg_min_conn, cfg.pg_max_conn, dsn=cfg.postgres_dsn)

    def _run(self, query: str, params: tuple | None = None, *, fetch: str | None = None):
        conn = self._pool.getconn()
        try:
            with conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(query, params)
                    if fetch == "one":
                        return cur.fetchone()
                    if fetch == "all":
                        return cur.fetchall()
                    return None
        finally:
            self._pool.putconn(conn)

    def store_change_event(self, event_time: datetime, article_id: str, changes: dict[str, Any]) -> int:
        def op():
            row = self._run(
                """
                INSERT INTO graph_change_events (event_time, article_id, changes)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (event_time, article_id, Json(changes)),
                fetch="one",
            )
            return int(row["id"])

        return retry(op)

    def store_snapshot(self, snapshot_time: datetime, last_event_id: int | None, graph_state: dict[str, Any]) -> int:
        compressed = compress_json_payload(graph_state)

        def op():
            row = self._run(
                """
                INSERT INTO graph_snapshots (snapshot_time, last_event_id, graph_state)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (snapshot_time, last_event_id, Json(compressed)),
                fetch="one",
            )
            return int(row["id"])

        return retry(op)

    def prune_snapshots(self, retention_days: int) -> int:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=retention_days)

        def op():
            conn = self._pool.getconn()
            try:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM graph_snapshots WHERE snapshot_time < %s", (cutoff,))
                        return int(cur.rowcount)
            finally:
                self._pool.putconn(conn)

        return retry(op)

    def fetch_nearest_snapshot_before(self, at_time: datetime) -> dict[str, Any] | None:
        row = retry(
            lambda: self._run(
                """
                SELECT id, snapshot_time, last_event_id, graph_state
                FROM graph_snapshots
                WHERE snapshot_time <= %s
                ORDER BY snapshot_time DESC
                LIMIT 1
                """,
                (at_time,),
                fetch="one",
            )
        )
        if not row:
            return None
        row["graph_state"] = decompress_json_payload(row["graph_state"])
        return dict(row)

    def fetch_changes_between(self, from_time: datetime, to_time: datetime) -> list[dict[str, Any]]:
        rows = retry(
            lambda: self._run(
                """
                SELECT id, event_time, article_id, changes
                FROM graph_change_events
                WHERE event_time > %s AND event_time <= %s
                ORDER BY id ASC
                """,
                (from_time, to_time),
                fetch="all",
            )
        )
        return [dict(row) for row in rows or []]

    def fetch_changes(self, from_time: datetime, to_time: datetime, entity: str | None = None) -> list[dict[str, Any]]:
        if entity:
            rows = retry(
                lambda: self._run(
                    """
                    SELECT id, event_time, article_id, changes
                    FROM graph_change_events
                    WHERE event_time >= %s AND event_time <= %s
                      AND changes::text ILIKE %s
                    ORDER BY id ASC
                    """,
                    (from_time, to_time, f"%{entity}%"),
                    fetch="all",
                )
            )
        else:
            rows = retry(
                lambda: self._run(
                    """
                    SELECT id, event_time, article_id, changes
                    FROM graph_change_events
                    WHERE event_time >= %s AND event_time <= %s
                    ORDER BY id ASC
                    """,
                    (from_time, to_time),
                    fetch="all",
                )
            )
        return [dict(row) for row in rows or []]

    def fetch_changes_after_id(self, last_event_id: int, limit: int = 1000) -> list[dict[str, Any]]:
        rows = retry(
            lambda: self._run(
                """
                SELECT id, event_time, article_id, changes
                FROM graph_change_events
                WHERE id > %s
                ORDER BY id ASC
                LIMIT %s
                """,
                (last_event_id, limit),
                fetch="all",
            )
        )
        return [dict(row) for row in rows or []]

    def close(self) -> None:
        self._pool.closeall()
