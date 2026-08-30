from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

METRICS_REGISTRY = CollectorRegistry(auto_describe=True)

SQL_QUERY_TOTAL = Counter(
    "sql_query_total",
    "Total number of SQL queries processed per org.",
    labelnames=("org_id",),
    registry=METRICS_REGISTRY,
)
SQL_QUERY_ROWS_RETURNED = Histogram(
    "sql_query_rows_returned",
    "Rows returned by SQL queries per org.",
    labelnames=("org_id",),
    registry=METRICS_REGISTRY,
)
SQL_QUERY_DURATION_SECONDS = Histogram(
    "sql_query_duration_seconds",
    "SQL query execution latency in seconds per org.",
    labelnames=("org_id",),
    registry=METRICS_REGISTRY,
)


def observe_query(org_id: str, row_count: int, duration_seconds: float) -> None:
    """Record query throughput and row metrics for a tenant-specific org."""
    normalized_org = org_id or "unknown"
    SQL_QUERY_TOTAL.labels(normalized_org).inc()
    SQL_QUERY_ROWS_RETURNED.labels(normalized_org).observe(float(row_count))
    SQL_QUERY_DURATION_SECONDS.labels(normalized_org).observe(float(duration_seconds))


def get_metrics_payload() -> bytes:
    return generate_latest(METRICS_REGISTRY)
