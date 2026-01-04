from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class IMusicQueryService(ABC):
    @abstractmethod
    async def execute_sql_statement(
        self, sql: str, params: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Execute a SQL statement and return the results"""
        raise NotImplementedError("This method should be overridden by subclasses.")

    @abstractmethod
    async def get_table_schema(self, query_embeddings: List[float]) -> Dict[str, Any]:
        """Get table schema information using vector embeddings"""
        raise NotImplementedError("This method should be overridden by subclasses.")
