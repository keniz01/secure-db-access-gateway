import sys
from typing import Final
from punq import Container
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from loguru import logger

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
def setup_container(connection_string: str) -> Container:
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
        engine: Final[AsyncEngine] = create_async_engine(
            connection_string, echo=False, future=True, pool_pre_ping=True
        )

        logger.success("Async SQLAlchemy engine created successfully.")

        # Register repository
        logger.debug("Initializing SqlQueryRepository...")
        sql_safety_checker = DefaultSqlSafetyChecker()
        repo = SqlQueryRepository(engine=engine, sql_safety_checker=sql_safety_checker)
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
