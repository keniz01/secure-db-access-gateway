import sys

from loguru import logger

# Ensure clean logger setup
logger.remove()


# Add a default 'correlation_id' if not provided
def ensure_correlation_id(record):
    record["extra"].setdefault("correlation_id", "N/A")
    return True


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
