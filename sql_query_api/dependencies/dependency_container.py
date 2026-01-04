import sys
from typing import Final
from punq import Container
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from loguru import logger

from repositories.music_query_repository import MusicQueryRepository
from repositories.abstract_music_query_repository import IMusicQueryRepository
from services.music_query_service import MusicQueryService
from services.abstract_music_query_service import IMusicQueryService


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
    Set up the dependency injection container for the Music Query system.

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
        logger.debug("Initializing MusicQueryRepository...")
        sql_safety_checker = DefaultSqlSafetyChecker()
        repo = MusicQueryRepository(engine=engine, sql_safety_checker=sql_safety_checker)
        container.register(IMusicQueryRepository, instance=repo)
        logger.success("Registered IMusicQueryRepository -> MusicQueryRepository")

        # Register service
        logger.debug("Initializing MusicQueryService...")
        service = MusicQueryService(repository=repo)
        container.register(IMusicQueryService, instance=service)
        logger.success("Registered IMusicQueryService -> MusicQueryService")

        logger.info("Dependency Container setup completed successfully.")
        return container

    except Exception as e:
        logger.exception(f"Dependency Container setup failed due to an error: {e}")
        raise
