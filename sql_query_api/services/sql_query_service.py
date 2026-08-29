import logging
from typing import Any, Dict, List, Optional

from repositories.abstract_sql_query_repository import ISqlQueryRepository
from services.abstract_sql_query_service import ISqlQueryService


class SqlQueryService(ISqlQueryService):
    """
    Service class for SQL queries.
    This class implements the methods to interact with the SQL query repository.
    """

    def __init__(self, repository: ISqlQueryRepository) -> None:
        """
        Initialize the SqlQueryService with a repository (dependency injection).
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

    async def introspect_schema(self) -> Dict[str, Any]:
        try:
            schema = await self.repository.introspect_schema()
            logging.info("Service: Introspected database schema successfully.")
            return schema
        except Exception as e:
            logging.error(f"Service: Error introspecting schema: {e}")
            raise

