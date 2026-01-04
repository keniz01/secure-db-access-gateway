"""
Logging configuration for the Auth0 API application.
"""

import logging
from .settings import settings


def configure_logging():
    """Configure logging for the application."""
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the specified name.

    Args:
        name: Logger name (usually __name__ or a module name)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)
