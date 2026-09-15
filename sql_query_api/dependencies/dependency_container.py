import os
import sys
from collections.abc import Callable, Mapping
from typing import Final

from loguru import logger
from punq import Container
from shared_secrets import is_environment_production
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from repositories.abstract_sql_query_repository import ISqlQueryRepository
from repositories.sql_query_repository import (
    SqlQueryRepository,
    enforce_readonly_role_guardrail,
)
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from services.abstract_sql_query_service import ISqlQueryService
from services.sql_query_service import SqlQueryService


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


def _parse_row_filter(
    raw_value: str,
) -> Callable[[Mapping[str, object]], bool] | None:
    if not raw_value.strip():
        return None

    key, value = raw_value.split("=", 1)
    column_name = key.strip().lower()
    normalized_value = value.strip()

    if normalized_value.startswith("'") and normalized_value.endswith("'"):
        normalized_value = normalized_value[1:-1]

    def row_filter(row: Mapping[str, object]) -> bool:
        return row.get(column_name) == normalized_value

    return row_filter

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
    database_target: str = "primary",
) -> Container:
    """
    Set up the dependency injection container for the SQL Query system.

    Args:
        connection_string: The database connection string.
        data_schema: Schema containing the data tables to serve.
        metadata_schema: Schema containing table metadata.
        tenant_org_id: Optional org identifier to scope the engine.
        tenant_database_id: Optional logical database identifier.
        database_target: Which configured database target to use.

    Returns:
        Container: A configured punq dependency injection container.

    """
    logger.info("Starting setup of dependency injection container [MCP Server]...")
    container = Container()

    try:
        if connection_string.startswith(("sqlite://", "sqlite+aiosqlite://")) and (
            ":memory:" not in connection_string and "mode=ro" not in connection_string
        ):
            prefix, path = connection_string.split("://", 1)
            if not path.startswith("/file:"):
                connection_string = f"{prefix}:///file:/{path.lstrip('/')}"
            connection_string = (
                f"{connection_string}&mode=ro&uri=true"
                if "?" in connection_string
                else f"{connection_string}?mode=ro&uri=true"
            )
        # Production gate: without a dedicated SELECT-only role the DB engine
        # itself is powerless to stop a compromised application from writing.
        enforce_readonly_role_guardrail(
            connection_string,
            production=is_environment_production() and not os.getenv("CI"),
        )
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
            database_target=database_target,
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
