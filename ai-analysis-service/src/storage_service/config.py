from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class StorageConfig:
    postgres_dsn: str
    pg_min_conn: int
    pg_max_conn: int
    kafka_bootstrap_servers: str
    kafka_group_id: str
    changes_topic: str
    snapshots_topic: str
    snapshot_every_n_changes: int
    snapshot_every_minutes: int
    snapshot_retention_days: int
    schema_version: int
    reconstruction_cache_ttl_seconds: int
    reconstruction_cache_size: int


DEFAULT_DSN = "postgresql://graph_user:graph_pass@localhost:5433/graph_storage"


def load_config() -> StorageConfig:
    return StorageConfig(
        postgres_dsn=os.getenv("GRAPH_STORAGE_POSTGRES_DSN", DEFAULT_DSN),
        pg_min_conn=int(os.getenv("GRAPH_STORAGE_PG_MIN_CONN", "1")),
        pg_max_conn=int(os.getenv("GRAPH_STORAGE_PG_MAX_CONN", "10")),
        kafka_bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        kafka_group_id=os.getenv("GRAPH_STORAGE_GROUP_ID", "graph-storage"),
        changes_topic=os.getenv("GRAPH_CHANGES_TOPIC", "graph-changes"),
        snapshots_topic=os.getenv("GRAPH_SNAPSHOTS_TOPIC", "graph-snapshots"),
        snapshot_every_n_changes=int(os.getenv("GRAPH_SNAPSHOT_EVERY_N_CHANGES", "100")),
        snapshot_every_minutes=int(os.getenv("GRAPH_SNAPSHOT_EVERY_MINUTES", "10")),
        snapshot_retention_days=int(os.getenv("GRAPH_SNAPSHOT_RETENTION_DAYS", "7")),
        schema_version=int(os.getenv("GRAPH_CHANGES_SCHEMA_VERSION", "1")),
        reconstruction_cache_ttl_seconds=int(os.getenv("GRAPH_RECON_CACHE_TTL_SECONDS", "60")),
        reconstruction_cache_size=int(os.getenv("GRAPH_RECON_CACHE_SIZE", "128")),
    )
