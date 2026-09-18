import contextvars
import json
import os
import sys
from datetime import datetime, timezone

from loguru import logger

try:
    from opentelemetry import trace
except ImportError:  # pragma: no cover - optional dependency in local/dev setups
    trace = None


# Ensure clean logger setup
logger.remove()


def configure_telemetry() -> None:
    """Enable optional OTLP export when a collector endpoint is configured."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint or trace is None:
        return

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "sql-query-api")})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
    except Exception:  # pragma: no cover - telemetry is best-effort only
        logger.warning("Telemetry export could not be initialized; continuing without OTel export.")


_correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="N/A")


def get_current_correlation_id() -> str:
    """Retrieve the correlation ID for the current context."""
    return _correlation_id_ctx.get()


def set_current_correlation_id(correlation_id: str) -> None:
    """Set the correlation ID for the current context."""
    _correlation_id_ctx.set(correlation_id)


# Add a default 'correlation_id' if not provided
def ensure_correlation_id(record: dict[str, object]) -> bool:
    """Attach a default correlation ID to every log record."""
    cid = _correlation_id_ctx.get()
    if cid != "N/A":
        record["extra"].setdefault("correlation_id", cid)
    else:
        record["extra"].setdefault("correlation_id", "N/A")
    return True


def log_audit_event(event_type: str, **payload: object) -> None:
    """Emit a structured JSON audit event to stdout with correlation metadata."""
    cid = payload.get("correlation_id") or get_current_correlation_id()
    event = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "correlation_id": cid,
        **payload,
    }
    bind_kwargs = {"correlation_id": cid, **payload}
    logger.bind(**bind_kwargs).info(json.dumps(event, default=str, separators=(",", ":")))


# Console logger
logger.add(
    sys.stdout,
    level="INFO",
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | "
        "CID={extra[correlation_id]: <36} | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    ),
    filter=ensure_correlation_id,
)
