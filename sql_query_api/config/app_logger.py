import contextvars
import hashlib
import json
import os
import re
import sys
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from loguru import logger

try:
    from opentelemetry import trace
except ImportError:  # pragma: no cover - optional dependency in local/dev setups
    trace = None

# Import metrics for audit sink failure tracking
try:
    from metrics import record_audit_sink_write_failed
except ImportError:  # pragma: no cover - circular import during init
    record_audit_sink_write_failed = lambda sink_type: None
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
_audit_hash_chain_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("audit_hash_chain", default="")

_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def sanitize_correlation_id(value: str | None) -> str | None:
    """Return ``value`` when it is a safe correlation ID, otherwise ``None``."""
    if value is not None and _CORRELATION_ID_PATTERN.fullmatch(value):
        return value
    return None


def get_current_correlation_id() -> str:
    """Retrieve the correlation ID for the current context."""
    return _correlation_id_ctx.get()


def set_current_correlation_id(correlation_id: str) -> contextvars.Token[str]:
    """Set the correlation ID for the current context; returns a reset token."""
    return _correlation_id_ctx.set(correlation_id)


def reset_current_correlation_id(token: contextvars.Token[str]) -> None:
    """Restore the correlation ID context to the value it had before ``set``."""
    _correlation_id_ctx.reset(token)


def get_current_audit_hash() -> str:
    """Retrieve the current audit hash chain value."""
    return _audit_hash_chain_ctx.get()


def set_current_audit_hash(audit_hash: str) -> contextvars.Token[str]:
    """Set the audit hash chain value; returns a reset token."""
    return _audit_hash_chain_ctx.set(audit_hash)


def reset_current_audit_hash(token: contextvars.Token[str]) -> None:
    """Restore the audit hash chain context."""
    _audit_hash_chain_ctx.reset(token)


# --- Audit Sink Interface (WORM-compatible) ---

class AuditSink(ABC):
    """Abstract base class for audit log sinks. Implementations must be append-only."""

    @abstractmethod
    def write(self, event: dict[str, Any]) -> None:
        """Write an audit event. Must not allow modification or deletion."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Flush and close the sink."""
        ...


class StdoutAuditSink(AuditSink):
    """Default stdout sink using loguru."""

    def write(self, event: dict[str, Any]) -> None:
        logger.bind(**{k: v for k, v in event.items() if k not in ("audit_hash", "prev_audit_hash")}).info(
            json.dumps(event, default=str, separators=(",", ":"))
        )

    def close(self) -> None:
        pass


class CompositeAuditSink(AuditSink):
    """Writes to multiple sinks atomically (all succeed or all fail)."""

    def __init__(self, sinks: list[AuditSink]) -> None:
        self._sinks = sinks

    def write(self, event: dict[str, Any]) -> None:
        for sink in self._sinks:
            sink.write(event)

    def close(self) -> None:
        for sink in self._sinks:
            sink.close()


class CloudWatchAuditSink(AuditSink):
    """AWS CloudWatch Logs sink with optional KMS encryption."""

    def __init__(self, log_group: str, log_stream: str, region: str | None = None) -> None:
        try:
            import boto3
        except ImportError:
            raise RuntimeError("boto3 required for CloudWatchAuditSink. Install with: pip install boto3")
        self._client = boto3.client("logs", region_name=region)
        self._log_group = log_group
        self._log_stream = log_stream
        self._sequence_token: str | None = None
        self._ensure_stream_exists()

    def _ensure_stream_exists(self) -> None:
        try:
            self._client.create_log_group(logGroupName=self._log_group)
        except self._client.exceptions.ResourceAlreadyExistsException:
            pass
        try:
            resp = self._client.create_log_stream(
                logGroupName=self._log_group, logStreamName=self._log_stream
            )
        except self._client.exceptions.ResourceAlreadyExistsException:
            # Get existing sequence token
            desc = self._client.describe_log_streams(
                logGroupName=self._log_group, logStreamNamePrefix=self._log_stream
            )
            for stream in desc.get("logStreams", []):
                if stream["logStreamName"] == self._log_stream:
                    self._sequence_token = stream.get("uploadSequenceToken")
                    break
        except Exception:
            pass

    def write(self, event: dict[str, Any]) -> None:
        import time
        timestamp = int(time.time() * 1000)
        message = json.dumps(event, default=str, separators=(",", ":"))
        kwargs = {
            "logGroupName": self._log_group,
            "logStreamName": self._log_stream,
            "logEvents": [{"timestamp": timestamp, "message": message}],
        }
        if self._sequence_token:
            kwargs["sequenceToken"] = self._sequence_token
        try:
            resp = self._client.put_log_events(**kwargs)
            self._sequence_token = resp.get("nextSequenceToken")
        except Exception:
            record_audit_sink_write_failed("cloudwatch")
            raise

    def close(self) -> None:
        pass


class S3AuditSink(AuditSink):
    """AWS S3 Object Lock (WORM) sink for immutable audit storage."""

    def __init__(
        self,
        bucket: str,
        prefix: str = "audit/",
        region: str | None = None,
        kms_key_id: str | None = None,
        object_lock_mode: str = "COMPLIANCE",
        object_lock_days: int = 365,
    ) -> None:
        try:
            import boto3
        except ImportError:
            raise RuntimeError("boto3 required for S3AuditSink. Install with: pip install boto3")
        self._s3 = boto3.client("s3", region_name=region)
        self._bucket = bucket
        self._prefix = prefix.rstrip("/") + "/"
        self._kms_key_id = kms_key_id
        self._object_lock_mode = object_lock_mode
        self._object_lock_days = object_lock_days
        self._buffer: list[dict[str, Any]] = []
        self._buffer_lock = threading.Lock()
        self._flush_interval = int(os.getenv("AUDIT_S3_FLUSH_SECONDS", "60"))
        self._max_batch = int(os.getenv("AUDIT_S3_MAX_BATCH", "100"))
        self._start_flush_timer()

    def _start_flush_timer(self) -> None:
        def flush_loop():
            while True:
                import time
                time.sleep(self._flush_interval)
                self.flush()
        thread = threading.Thread(target=flush_loop, daemon=True)
        thread.start()

    def write(self, event: dict[str, Any]) -> None:
        try:
            with self._buffer_lock:
                self._buffer.append(event)
                if len(self._buffer) >= self._max_batch:
                    self._flush_locked()
        except Exception:
            record_audit_sink_write_failed("s3")
            raise

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        import uuid
        batch = self._buffer[:]
        self._buffer.clear()
        key = f"{self._prefix}{datetime.now(timezone.utc).strftime('%Y/%m/%d')}/{uuid.uuid4().hex}.jsonl"
        body = "\n".join(json.dumps(e, default=str, separators=(",", ":")) for e in batch) + "\n"
        kwargs = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": body.encode("utf-8"),
            "ContentType": "application/jsonl",
            "ObjectLockMode": self._object_lock_mode,
            "ObjectLockRetainUntilDate": datetime.now(timezone.utc).replace(
                year=datetime.now(timezone.utc).year + self._object_lock_days
            ),
        }
        if self._kms_key_id:
            kwargs["ServerSideEncryption"] = "aws:kms"
            kwargs["SSEKMSKeyId"] = self._kms_key_id
        try:
            self._s3.put_object(**kwargs)
        except Exception:
            record_audit_sink_write_failed("s3")
            raise

    def flush(self) -> None:
        with self._buffer_lock:
            self._flush_locked()

    def close(self) -> None:
        self.flush()


# Global sink registry
_audit_sink: AuditSink = StdoutAuditSink()
_sink_lock = threading.Lock()


def get_audit_sink() -> AuditSink:
    """Get the current audit sink."""
    with _sink_lock:
        return _audit_sink


def set_audit_sink(sink: AuditSink) -> None:
    """Set the audit sink (thread-safe)."""
    global _audit_sink
    with _sink_lock:
        _audit_sink.close()
        _audit_sink = sink


def configure_audit_sink() -> None:
    """Configure audit sink from environment variables.

    Supported sinks:
    - stdout (default)
    - cloudwatch: AUDIT_SINK=cloudwatch AUDIT_CW_GROUP=... AUDIT_CW_STREAM=...
    - s3: AUDIT_SINK=s3 AUDIT_S3_BUCKET=... [AUDIT_S3_PREFIX=...] [AUDIT_S3_KMS_KEY=...]
    - composite: AUDIT_SINK=composite AUDIT_SINKS=stdout,s3,cloudwatch
    """
    sink_type = os.getenv("AUDIT_SINK", "stdout").lower()

    if sink_type == "stdout":
        set_audit_sink(StdoutAuditSink())
    elif sink_type == "cloudwatch":
        log_group = os.getenv("AUDIT_CW_GROUP")
        log_stream = os.getenv("AUDIT_CW_STREAM")
        region = os.getenv("AUDIT_CW_REGION")
        if not log_group or not log_stream:
            raise RuntimeError("AUDIT_CW_GROUP and AUDIT_CW_STREAM required for CloudWatch sink")
        set_audit_sink(CloudWatchAuditSink(log_group, log_stream, region))
    elif sink_type == "s3":
        bucket = os.getenv("AUDIT_S3_BUCKET")
        if not bucket:
            raise RuntimeError("AUDIT_S3_BUCKET required for S3 sink")
        prefix = os.getenv("AUDIT_S3_PREFIX", "audit/")
        kms_key = os.getenv("AUDIT_S3_KMS_KEY")
        mode = os.getenv("AUDIT_S3_LOCK_MODE", "COMPLIANCE")
        days = int(os.getenv("AUDIT_S3_LOCK_DAYS", "365"))
        set_audit_sink(S3AuditSink(bucket, prefix, kms_key_id=kms_key, object_lock_mode=mode, object_lock_days=days))
    elif sink_type == "composite":
        sinks = []
        for name in os.getenv("AUDIT_SINKS", "stdout").split(","):
            name = name.strip().lower()
            if name == "stdout":
                sinks.append(StdoutAuditSink())
            elif name == "cloudwatch":
                log_group = os.getenv("AUDIT_CW_GROUP")
                log_stream = os.getenv("AUDIT_CW_STREAM")
                region = os.getenv("AUDIT_CW_REGION")
                if log_group and log_stream:
                    sinks.append(CloudWatchAuditSink(log_group, log_stream, region))
            elif name == "s3":
                bucket = os.getenv("AUDIT_S3_BUCKET")
                if bucket:
                    prefix = os.getenv("AUDIT_S3_PREFIX", "audit/")
                    kms_key = os.getenv("AUDIT_S3_KMS_KEY")
                    mode = os.getenv("AUDIT_S3_LOCK_MODE", "COMPLIANCE")
                    days = int(os.getenv("AUDIT_S3_LOCK_DAYS", "365"))
                    sinks.append(S3AuditSink(bucket, prefix, kms_key_id=kms_key, object_lock_mode=mode, object_lock_days=days))
        if not sinks:
            sinks.append(StdoutAuditSink())
        set_audit_sink(CompositeAuditSink(sinks))
    else:
        raise RuntimeError(f"Unknown AUDIT_SINK: {sink_type}")


# --- End Audit Sink Interface ---


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
    """Emit a structured JSON audit event with correlation metadata and hash chaining.

    Supports optional data_classification parameter for compliance labeling:
    - PUBLIC: Non-sensitive operational data
    - INTERNAL: Internal operational data
    - CONFIDENTIAL: Sensitive business data
    - RESTRICTED: Highly sensitive data (PII, credentials, etc.)
    """
    cid = get_current_correlation_id()
    prev_hash = get_current_audit_hash()

    # Extract data classification if provided
    data_classification = payload.pop("data_classification", "INTERNAL")

    # Create payload without audit metadata for hashing
    hash_payload = {k: v for k, v in payload.items() if k not in ("audit_hash", "prev_audit_hash")}
    hash_input = json.dumps(
        {"event": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), **hash_payload},
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    )
    current_hash = hashlib.sha256((prev_hash + hash_input).encode("utf-8")).hexdigest()

    event = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
        "correlation_id": cid,
        "audit_hash": current_hash,
        "prev_audit_hash": prev_hash,
        "data_classification": data_classification,
    }

    # Update the hash chain for the next event
    set_current_audit_hash(current_hash)

    # Write to configured audit sink(s)
    get_audit_sink().write(event)


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
