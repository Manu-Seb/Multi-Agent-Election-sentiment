from __future__ import annotations

try:
    from prometheus_client import Counter, Gauge, Histogram
except ModuleNotFoundError:
    class _NoopMetric:
        def inc(self, *_args, **_kwargs):
            return None

        def set(self, *_args, **_kwargs):
            return None

        def observe(self, *_args, **_kwargs):
            return None

    def Counter(*_args, **_kwargs):  # type: ignore[misc]
        return _NoopMetric()

    def Gauge(*_args, **_kwargs):  # type: ignore[misc]
        return _NoopMetric()

    def Histogram(*_args, **_kwargs):  # type: ignore[misc]
        return _NoopMetric()

STORAGE_LAG_SECONDS = Gauge("graph_storage_lag_seconds", "Kafka event time lag in seconds")
STORED_CHANGE_EVENTS = Counter("graph_storage_change_events_total", "Number of stored change events")
STORED_SNAPSHOTS = Counter("graph_storage_snapshots_total", "Number of stored snapshots")
STORAGE_RETRIES = Counter("graph_storage_retries_total", "Number of storage retries")
QUERY_LATENCY_SECONDS = Histogram(
    "graph_storage_query_latency_seconds",
    "Latency of API queries",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)
