from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class WebsocketConfig:
    kafka_bootstrap_servers: str
    kafka_group_id: str
    graph_changes_topic: str
    batch_interval_ms: int
    max_batch_size: int
    heartbeat_seconds: int
    auth_required: bool
    rate_limit_per_sec: int
    compression_mode: str
    redis_enabled: bool
    redis_url: str
    redis_channel: str
    instance_id: str
    storage_postgres_dsn: str
    replay_limit: int


def load_config() -> WebsocketConfig:
    return WebsocketConfig(
        kafka_bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        kafka_group_id=os.getenv("WEBSOCKET_GROUP_ID", "websocket-broadcaster"),
        graph_changes_topic=os.getenv("GRAPH_CHANGES_TOPIC", "graph-changes"),
        batch_interval_ms=int(os.getenv("WS_BATCH_INTERVAL_MS", "100")),
        max_batch_size=int(os.getenv("WS_MAX_BATCH_SIZE", "200")),
        heartbeat_seconds=int(os.getenv("WS_HEARTBEAT_SECONDS", "20")),
        auth_required=os.getenv("WS_AUTH_REQUIRED", "false").lower() == "true",
        rate_limit_per_sec=int(os.getenv("WS_RATE_LIMIT_PER_SEC", "30")),
        compression_mode=os.getenv("WS_COMPRESSION_MODE", "none"),  # none|gzip|msgpack
        redis_enabled=os.getenv("WS_REDIS_ENABLED", "false").lower() == "true",
        redis_url=os.getenv("WS_REDIS_URL", "redis://localhost:6379/0"),
        redis_channel=os.getenv("WS_REDIS_CHANNEL", "graph-changes-broadcast"),
        instance_id=os.getenv("WS_INSTANCE_ID", "ws-1"),
        storage_postgres_dsn=os.getenv(
            "GRAPH_STORAGE_POSTGRES_DSN",
            "postgresql://graph_user:graph_pass@localhost:5433/graph_storage",
        ),
        replay_limit=int(os.getenv("WS_REPLAY_LIMIT", "1000")),
    )
