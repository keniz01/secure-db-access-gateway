import os
import sys
from typing import Final
from punq import Container
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from loguru import logger


def _parse_table_allowlist(raw_value: str) -> set[str]:
    return {value.strip().lower() for value in raw_value.split(",") if value.strip()}


def _parse_column_allowlist(raw_value: str) -> dict[str, set[str]]:
    allowlist: dict[str, set[str]] = {}
    for entry in raw_value.split(";"):
        if not entry.strip():
            continue
        table_name, columns = entry.split(":", 1)
        allowlist[table_name.strip().lower()] = {
            column.strip().lower() for column in columns.split(",") if column.strip()
        }
    return allowlist


def _parse_sensitive_columns(raw_value: str) -> set[str]:
    return {value.strip().lower() for value in raw_value.split(",") if value.strip()}


def _parse_row_filter(raw_value: str):
    if not raw_value.strip():
        return None

    key, value = raw_value.split("=", 1)
    column_name = key.strip().lower()
    normalized_value = value.strip()

    if normalized_value.startswith("'") and normalized_value.endswith("'"):
        normalized_value = normalized_value[1:-1]

    def row_filter(row):
        return row.get(column_name) == normalized_value

    return row_filter

from repositories.sql_query_repository import SqlQueryRepository
from repositories.abstract_sql_query_repository import ISqlQueryRepository
from services.sql_query_service import SqlQueryService
from services.abstract_sql_query_service import ISqlQueryService


# -----------------------------------------------------------------------------
# Logging Configuration
# -----------------------------------------------------------------------------
logger.remove()  # Remove default sink
logger.add(
    sys.stderr,
    level="INFO",
    colorize=True,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
        "| <level>{level: <8}</level> "
        "| <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> "
        "- <level>{message}</level>"
    ),
)


# -----------------------------------------------------------------------------
# Container Setup
# -----------------------------------------------------------------------------
def setup_container(
    connection_string: str,
    *,
    data_schema: str | None = None,
    metadata_schema: str | None = None,
    tenant_org_id: str | None = None,
    tenant_database_id: str | None = None,
) -> Container:
    """
    Set up the dependency injection container for the SQL Query system.

    Args:
        connection_string (str): The database connection string.

    Returns:
        Container: A configured punq dependency injection container.
    """
    logger.info("Starting setup of dependency injection container [MCP Server]...")
    container = Container()

    try:
        # Create database engine
        logger.debug("Creating async SQLAlchemy engine...")
        engine_kwargs = {
            "echo": False,
            "future": True,
            "pool_pre_ping": True,
        }
        if connection_string.startswith("postgresql") or connection_string.startswith("postgresql+asyncpg"):
            engine_kwargs.update(
                {
                    "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
                    "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "10")),
                    "pool_timeout": float(os.getenv("DB_POOL_TIMEOUT_SECONDS", "30")),
                    "pool_recycle": int(os.getenv("DB_POOL_RECYCLE_SECONDS", "1800")),
                }
            )

        engine: Final[AsyncEngine] = create_async_engine(connection_string, **engine_kwargs)

        logger.success("Async SQLAlchemy engine created successfully.")

        # Register repository
        logger.debug("Initializing SqlQueryRepository...")
        sql_safety_checker = DefaultSqlSafetyChecker(
            table_allowlist=_parse_table_allowlist(os.getenv("SQL_ALLOWED_TABLES", "")),
            column_allowlist=_parse_column_allowlist(os.getenv("SQL_ALLOWED_COLUMNS", "")),
        )
        query_timeout_seconds = float(os.getenv("SQL_QUERY_TIMEOUT_SECONDS", "30"))
        repo = SqlQueryRepository(
            engine=engine,
            sql_safety_checker=sql_safety_checker,
            query_timeout_seconds=query_timeout_seconds,
            sensitive_columns=_parse_sensitive_columns(os.getenv("SQL_SENSITIVE_COLUMNS", "")),
            row_filter=_parse_row_filter(os.getenv("SQL_ROW_FILTER", "")),
            data_schema=data_schema or os.getenv("SQL_DATA_SCHEMA", "public"),
            metadata_schema=metadata_schema or os.getenv("SQL_METADATA_SCHEMA", "meta"),
            tenant_org_id=tenant_org_id,
            tenant_database_id=tenant_database_id,
        )
        container.register(ISqlQueryRepository, instance=repo)
        logger.success("Registered ISqlQueryRepository -> SqlQueryRepository")

        # Register service
        logger.debug("Initializing SqlQueryService...")
        service = SqlQueryService(repository=repo)
        container.register(ISqlQueryService, instance=service)
        logger.success("Registered ISqlQueryService -> SqlQueryService")

        logger.info("Dependency Container setup completed successfully.")
        return container

    except Exception as e:
        logger.exception(f"Dependency Container setup failed due to an error: {e}")
        raise
