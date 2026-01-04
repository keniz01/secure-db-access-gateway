import logging
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional, Iterable
from sqlalchemy import TextClause, text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.ext.asyncio.result import AsyncResult

from repositories.abstract_music_query_repository import IMusicQueryRepository
from exceptions.forbidden_sql_statement_exception import ForbiddenSqlStatementException
from exceptions.sql_statement_execution_exception import SqlStatementExecutionException

from exceptions.exception_handlers import raise_sql_execution_exception
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker, SqlSafetyChecker


class MusicQueryRepository(IMusicQueryRepository):
    """
    Repository class for music queries.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        sql_safety_checker: SqlSafetyChecker,
    ) -> None:
        self._engine: AsyncEngine = engine
        self._sql_safety_checker: SqlSafetyChecker = sql_safety_checker

    @asynccontextmanager
    async def get_conn(self, schema_name: str) -> AsyncGenerator[AsyncConnection, None]:
        try:
            conn: AsyncConnection = await self._engine.connect()
            try:
                await conn.execute(text(f"SET search_path TO {schema_name}"))
                yield conn
            finally:
                await conn.close()
        except Exception as e:
            logging.error(f"Error connecting to database: {e}")
            raise_sql_execution_exception(
                "Error connecting to database", e, include_traceback=True
            )

    async def execute_sql_statement(
        self,
        sql: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if not self._sql_safety_checker.is_safe_select_query(sql):
            logging.warning(f"Forbidden SQL statement attempted: {sql}")
            raise ForbiddenSqlStatementException(
                "Only simple SELECT statements are allowed."
            )

        # Add default LIMIT if not present
        sql = self._ensure_limit(sql)

        async with self.get_conn("music") as conn:
            try:
                result: AsyncResult = await conn.execute(
                    text(sql),
                    parameters=params or {},
                )

                if result.returns_rows:
                    rows: Iterable[Row[Any]] = result.fetchall()
                    result_dicts: List[Dict[str, Any]] = [
                        dict(row._mapping) for row in rows
                    ]

                    logging.info(
                        f"SQL executed successfully, returned {len(result_dicts)} rows."
                    )
                    return result_dicts

                return []

            except Exception as e:
                logging.error(f"Error executing SQL statement: {e}")
                raise_sql_execution_exception(
                    "Error executing SQL statement", e, include_traceback=True
                )

    async def get_table_schema(self, query_embeddings: List[float]) -> Dict[str, Any]:
        """
        Fetches the top 4 most similar database schema entries.
        """
        query = self._build_similarity_query()

        try:
            async with self.get_conn("meta") as conn:
                embedding_str = f"[{','.join(map(str, query_embeddings))}]"

                result: AsyncResult = await conn.execute(
                    query, {"query_embeddings": embedding_str}
                )
                rows: List[Row[Any]] = result.fetchall()

                formatted = self._format_schema_rows(rows)
                return {"schema": formatted}

        except Exception as e:
            logging.error(f"Error fetching database schema: {e}", exc_info=True)
            raise SqlStatementExecutionException(
                f"Error fetching database schema: {type(e).__name__}: {e}"
            ) from e

    def _build_similarity_query(self) -> TextClause:
        return text(
            """
            SELECT raw_json
            FROM schema_embeddings
            ORDER BY (embeddings <#> CAST(:query_embeddings AS vector)) ASC
            LIMIT 4
            """
        )

    def _ensure_limit(self, sql: str) -> str:
        """
        Adds a default LIMIT 100 if the SQL query doesn't already have a LIMIT clause.
        """
        # Case-insensitive check for LIMIT clause
        if re.search(r'\bLIMIT\s+\d+\b', sql, re.IGNORECASE):
            return sql
        return f"{sql.rstrip(';')} LIMIT 100"

    def _format_schema_rows(self, rows: Iterable[Row[Any]]) -> str:
        """
        Formats fetched rows into a readable schema string.
        """
        schema_lines: List[str] = []

        for row in rows:
            raw_json: Dict[str, Any] = row[0]
            schema_lines.extend(self._format_single_schema(raw_json))
            schema_lines.append("")  # spacing

        logging.info("Fetched database schema for meta")
        return "\n".join(schema_lines)

    def _format_single_schema(self, raw_json: Dict[str, Any]) -> List[str]:
        """
        Formats a single raw_json schema entry.
        """
        lines: List[str] = []

        for table_name, table_info in raw_json.items():
            lines.append(f"{table_name}:")
            if isinstance(table_info, dict):
                columns = table_info.get("columns", {})
                for column_name, column_info in columns.items():
                    description = (
                        column_info.get("column_description", "")
                        if isinstance(column_info, dict)
                        else str(column_info)
                    )
                    lines.append(f"  {column_name}: {description}")

        return lines
