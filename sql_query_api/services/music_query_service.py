import logging
from typing import Any, Dict, List, Optional

from repositories.abstract_music_query_repository import IMusicQueryRepository
from services.abstract_music_query_service import IMusicQueryService


class MusicQueryService(IMusicQueryService):
    """
    Service class for music queries.
    This class implements the methods to interact with the music query repository.
    """

    def __init__(self, repository: IMusicQueryRepository) -> None:
        """
        Initialize the MusicQueryService with a repository (dependency injection).
        """
        self.repository = repository

    async def execute_sql_statement(
        self, sql: str, params: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        try:
            result = await self.repository.execute_sql_statement(sql, params)
            logging.info("Service: SQL executed successfully.")
            return result
        except Exception as e:
            logging.error(f"Service: Error executing SQL: {e}")
            raise

    async def get_table_schema(self, query_embeddings: List[float]) -> Dict[str, Any]:
        try:
            schema = await self.repository.get_table_schema(query_embeddings)
            logging.info("Service: Fetched database schema.")
            return schema
        except Exception as e:
            logging.error(f"Service: Error fetching schema: {e}")
            raise
