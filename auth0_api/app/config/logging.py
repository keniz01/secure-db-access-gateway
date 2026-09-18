"""
Logging configuration for the Auth0 API application.
"""

import contextvars
import logging
import re
from .settings import settings

_correlation_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="N/A")

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


class CorrelationIdFilter(logging.Filter):
    """Attach the current correlation ID to each log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = get_current_correlation_id()
        return True


def configure_logging():
    """Configure logging for the application."""
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.LOG_LEVEL)

    if not any(isinstance(f, CorrelationIdFilter) for f in root_logger.filters):
        root_logger.addFilter(CorrelationIdFilter())

    handler = logging.StreamHandler()
    handler.addFilter(CorrelationIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [CID=%(correlation_id)s] [%(name)s] %(message)s")
    )

    if not root_logger.handlers:
        root_logger.addHandler(handler)
    else:
        for h in root_logger.handlers:
            h.addFilter(CorrelationIdFilter())
            h.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s [CID=%(correlation_id)s] [%(name)s] %(message)s")
            )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the specified name.

    Args:
        name: Logger name (usually __name__ or a module name)

    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)
    if not any(isinstance(f, CorrelationIdFilter) for f in logger.filters):
        logger.addFilter(CorrelationIdFilter())
    return logger
