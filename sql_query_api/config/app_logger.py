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
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "sql-query-api")})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
    except Exception:  # pragma: no cover - telemetry is best-effort only
        logger.warning("Telemetry export could not be initialized; continuing without OTel export.")


# Add a default 'correlation_id' if not provided
def ensure_correlation_id(record):
    record["extra"].setdefault("correlation_id", "N/A")
    return True


def log_audit_event(event_type: str, **payload):
    """Emit a structured JSON audit event to stdout with correlation metadata."""
    event = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    logger.bind(**payload).info(json.dumps(event, default=str, separators=(",", ":")))


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
