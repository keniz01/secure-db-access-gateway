from prometheus_client import CollectorRegistry, Counter, Histogram, Gauge, generate_latest

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

# --- Security & Operational Metrics for Alerting ---

AUTH_FAILED_TOTAL = Counter(
    "auth_failed_total",
    "Total number of failed authentication attempts.",
    labelnames=("reason",),  # missing_or_invalid_bearer_token, principal_from_claims_failed
    registry=METRICS_REGISTRY,
)

POLICY_DENIED_TOTAL = Counter(
    "policy_denied_total",
    "Total number of requests denied by policy evaluation.",
    labelnames=("org_id", "reason"),
    registry=METRICS_REGISTRY,
)

QUERY_REJECTED_OVERLOAD_TOTAL = Counter(
    "query_rejected_overload_total",
    "Total number of queries rejected due to tenant overload quotas.",
    labelnames=("org_id", "database_id"),
    registry=METRICS_REGISTRY,
)

QUERY_VALIDATION_FAILED_TOTAL = Counter(
    "query_validation_failed_total",
    "Total number of SQL queries that failed validation.",
    labelnames=("org_id", "reason"),
    registry=METRICS_REGISTRY,
)

RATE_LIMIT_EXCEEDED_TOTAL = Counter(
    "rate_limit_exceeded_total",
    "Total number of requests rejected by rate limiting.",
    labelnames=("dimension",),  # ip, principal, tenant, database
    registry=METRICS_REGISTRY,
)

TENANT_QUOTA_UTILIZATION = Gauge(
    "tenant_quota_utilization",
    "Current tenant quota utilization (0-1).",
    labelnames=("org_id", "database_id", "type"),  # type: concurrent, queued
    registry=METRICS_REGISTRY,
)

ACTIVE_TENANT_CONNECTIONS = Gauge(
    "active_tenant_connections",
    "Number of active connections per tenant database.",
    labelnames=("org_id", "database_id"),
    registry=METRICS_REGISTRY,
)

OPA_EVALUATION_DURATION_SECONDS = Histogram(
    "opa_evaluation_duration_seconds",
    "OPA policy evaluation latency in seconds.",
    labelnames=("org_id", "database_id"),
    registry=METRICS_REGISTRY,
)

OPA_EVALUATION_FAILED_TOTAL = Counter(
    "opa_evaluation_failed_total",
    "Total number of OPA evaluation failures (fail-closed).",
    labelnames=("org_id", "database_id"),
    registry=METRICS_REGISTRY,
)

AUDIT_SINK_WRITE_FAILED_TOTAL = Counter(
    "audit_sink_write_failed_total",
    "Total number of audit sink write failures.",
    labelnames=("sink_type",),
    registry=METRICS_REGISTRY,
)


def observe_query(org_id: str, row_count: int, duration_seconds: float) -> None:
    """Record query throughput and row metrics for a tenant-specific org."""
    normalized_org = org_id or "unknown"
    SQL_QUERY_TOTAL.labels(normalized_org).inc()
    SQL_QUERY_ROWS_RETURNED.labels(normalized_org).observe(float(row_count))
    SQL_QUERY_DURATION_SECONDS.labels(normalized_org).observe(float(duration_seconds))


def record_auth_failed(reason: str) -> None:
    """Record an authentication failure."""
    AUTH_FAILED_TOTAL.labels(reason).inc()


def record_policy_denied(org_id: str, reason: str) -> None:
    """Record a policy denial."""
    POLICY_DENIED_TOTAL.labels(org_id or "unknown", reason).inc()


def record_query_rejected_overload(org_id: str, database_id: str) -> None:
    """Record a query rejected due to tenant overload."""
    QUERY_REJECTED_OVERLOAD_TOTAL.labels(org_id or "unknown", database_id or "unknown").inc()


def record_query_validation_failed(org_id: str, reason: str) -> None:
    """Record a SQL validation failure."""
    QUERY_VALIDATION_FAILED_TOTAL.labels(org_id or "unknown", reason).inc()


def record_rate_limit_exceeded(dimension: str) -> None:
    """Record a rate limit exceeded event."""
    RATE_LIMIT_EXCEEDED_TOTAL.labels(dimension).inc()


def set_tenant_quota_utilization(org_id: str, database_id: str, quota_type: str, value: float) -> None:
    """Set tenant quota utilization (0.0 to 1.0)."""
    TENANT_QUOTA_UTILIZATION.labels(org_id or "unknown", database_id or "unknown", quota_type).set(value)


def set_active_tenant_connections(org_id: str, database_id: str, count: int) -> None:
    """Set active tenant connection count."""
    ACTIVE_TENANT_CONNECTIONS.labels(org_id or "unknown", database_id or "unknown").set(count)


def observe_opa_evaluation(org_id: str, database_id: str, duration_seconds: float) -> None:
    """Record OPA evaluation latency."""
    OPA_EVALUATION_DURATION_SECONDS.labels(org_id or "unknown", database_id or "unknown").observe(duration_seconds)


def record_opa_evaluation_failed(org_id: str, database_id: str) -> None:
    """Record an OPA evaluation failure (fail-closed)."""
    OPA_EVALUATION_FAILED_TOTAL.labels(org_id or "unknown", database_id or "unknown").inc()


def record_audit_sink_write_failed(sink_type: str) -> None:
    """Record an audit sink write failure."""
    AUDIT_SINK_WRITE_FAILED_TOTAL.labels(sink_type).inc()


def get_metrics_payload() -> bytes:
    """Render the current Prometheus metrics registry as a text payload."""
    return generate_latest(METRICS_REGISTRY)
