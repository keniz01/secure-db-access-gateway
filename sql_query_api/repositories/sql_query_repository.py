import logging
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional, Iterable
from sqlalchemy import TextClause, text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.ext.asyncio.result import AsyncResult

from repositories.abstract_sql_query_repository import ISqlQueryRepository
from exceptions.forbidden_sql_statement_exception import ForbiddenSqlStatementException
from exceptions.sql_statement_execution_exception import SqlStatementExecutionException

from exceptions.exception_handlers import raise_sql_execution_exception
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker, SqlSafetyChecker


class SqlQueryRepository(ISqlQueryRepository):
    """
    Repository class for SQL queries.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        sql_safety_checker: SqlSafetyChecker,
    ) -> None:
        self._engine: AsyncEngine = engine
        self._sql_safety_checker: SqlSafetyChecker = sql_safety_checker

    @asynccontextmanager
    async def get_conn(self, schema_name: Optional[str] = None) -> AsyncGenerator[AsyncConnection, None]:
        try:
            conn: AsyncConnection = await self._engine.connect()
            try:
                if conn.dialect.name == "postgresql":
                    await conn.execute(text("SET TRANSACTION READ ONLY;"))
                    if schema_name:
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

        async with self.get_conn() as conn:
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

    async def introspect_schema(self) -> Dict[str, Any]:
        """
        Dynamically introspects the connected database schema.
        Reads information_schema (PostgreSQL) or sqlite_master / PRAGMA (SQLite).
        """
        try:
            async with self.get_conn() as conn:
                dialect_name = conn.dialect.name
                if dialect_name == "sqlite":
                    return await self._introspect_sqlite(conn)
                else:
                    return await self._introspect_postgresql(conn)
        except Exception as e:
            logging.error(f"Error introspecting database schema: {e}", exc_info=True)
            raise SqlStatementExecutionException(
                f"Error introspecting database schema: {type(e).__name__}: {e}"
            ) from e

    async def _introspect_sqlite(self, conn: AsyncConnection) -> Dict[str, Any]:
        tables_res = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        )
        tables = [row[0] for row in tables_res.fetchall()]

        tables_list = []
        for table in tables:
            safe_table = table.replace("'", "''")
            cols_res = await conn.execute(text(f"PRAGMA table_info('{safe_table}')"))
            cols = cols_res.fetchall()
            columns_list = []
            for col in cols:
                col_dict = dict(col._mapping) if hasattr(col, "_mapping") else {
                    "name": col[1],
                    "type": col[2],
                    "nullable": col[3] == 0,
                    "is_primary": col[5] > 0,
                }
                columns_list.append({
                    "name": str(col_dict.get("name", "")),
                    "type": str(col_dict.get("type", "TEXT")),
                    "nullable": bool(col_dict.get("notnull", 0) == 0) if "notnull" in col_dict else bool(col_dict.get("nullable", True)),
                    "is_primary": bool(col_dict.get("pk", 0) > 0) if "pk" in col_dict else bool(col_dict.get("is_primary", False)),
                })

            fks_res = await conn.execute(text(f"PRAGMA foreign_key_list('{safe_table}')"))
            fks = fks_res.fetchall()
            fk_list = []
            for fk in fks:
                fk_dict = dict(fk._mapping) if hasattr(fk, "_mapping") else {
                    "table": fk[2],
                    "from": fk[3],
                    "to": fk[4],
                }
                fk_list.append({
                    "column": str(fk_dict.get("from", "")),
                    "foreign_schema": "main",
                    "foreign_table": str(fk_dict.get("table", "")),
                    "foreign_column": str(fk_dict.get("to", "")),
                })

            tables_list.append({
                "name": table,
                "schema_name": "main",
                "columns": columns_list,
                "foreign_keys": fk_list,
            })

        return {"tables": tables_list}

    async def _introspect_postgresql(self, conn: AsyncConnection) -> Dict[str, Any]:
        tables_res = await conn.execute(text("""
            SELECT table_schema, table_name 
            FROM information_schema.tables 
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema', 'meta')
              AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
        """))
        tables = tables_res.fetchall()

        tables_list = []
        for row in tables:
            schema_name = row[0]
            table_name = row[1]

            cols_res = await conn.execute(text("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = :schema AND table_name = :table
                ORDER BY ordinal_position
            """), {"schema": schema_name, "table": table_name})
            cols = cols_res.fetchall()

            pks_res = await conn.execute(text("""
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'PRIMARY KEY'
                  AND tc.table_schema = :schema
                  AND tc.table_name = :table
            """), {"schema": schema_name, "table": table_name})
            pks = {r[0] for r in pks_res.fetchall()}

            fks_res = await conn.execute(text("""
                SELECT
                    kcu.column_name AS column_name,
                    ccu.table_schema AS foreign_schema,
                    ccu.table_name AS foreign_table,
                    ccu.column_name AS foreign_column
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = :schema
                  AND tc.table_name = :table
            """), {"schema": schema_name, "table": table_name})
            fks = fks_res.fetchall()

            columns_list = []
            for col in cols:
                col_name = col[0]
                columns_list.append({
                    "name": col_name,
                    "type": col[1],
                    "nullable": col[2] == "YES",
                    "is_primary": col_name in pks,
                })

            fk_list = []
            for fk in fks:
                fk_list.append({
                    "column": fk[0],
                    "foreign_schema": fk[1],
                    "foreign_table": fk[2],
                    "foreign_column": fk[3],
                })

            tables_list.append({
                "name": table_name,
                "schema_name": schema_name,
                "columns": columns_list,
                "foreign_keys": fk_list,
            })

        return {"tables": tables_list}

    async def get_table_schema(self, query_embeddings: List[float]) -> Dict[str, Any]:
        """
        Fetches the top 4 most similar database schema entries if available,
        or dynamically introspects database schema.
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
            logging.info(f"Vector schema lookup unavailable ({e}), falling back to dynamic introspection.")
            try:
                schema_info = await self.introspect_schema()
                lines = []
                for table in schema_info.get("tables", []):
                    lines.append(f"{table['name']}:")
                    for col in table.get("columns", []):
                        lines.append(f"  {col['name']}: {col.get('type', '')}")
                    lines.append("")
                return {"schema": "\n".join(lines)}
            except Exception as inner_e:
                logging.error(f"Error fetching database schema: {inner_e}", exc_info=True)
                raise SqlStatementExecutionException(
                    f"Error fetching database schema: {type(inner_e).__name__}: {inner_e}"
                ) from inner_e

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
