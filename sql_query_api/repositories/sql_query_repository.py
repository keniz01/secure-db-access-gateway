import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncGenerator, Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import TextClause, text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.ext.asyncio.result import AsyncResult

from exceptions.exception_handlers import raise_sql_execution_exception
from exceptions.forbidden_sql_statement_exception import ForbiddenSqlStatementError
from exceptions.sql_statement_execution_exception import SqlStatementExecutionError
from repositories.abstract_sql_query_repository import ISqlQueryRepository
from repositories.sql_validators.sql_safety_checker import SqlSafetyChecker

_READONLY_ROLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def validate_readonly_role_name(role_name: str | None) -> str | None:
    """Validate a configured PostgreSQL role identifier so ``SET ROLE`` is safe.

    Only a bare, unqualified role name is acceptable; anything else (qualified
    identifiers, string literals, expressions) is rejected before it can reach
    ``SET ROLE``.
    """
    if role_name is None:
        return None
    role_name = role_name.strip()
    if not role_name or not _READONLY_ROLE_PATTERN.fullmatch(role_name):
        raise ValueError(
            "SQL_READONLY_ROLE must be a bare PostgreSQL role name (not a "
            "qualified identifier or a literal)."
        )
    return role_name


def enforce_readonly_role_guardrail(connection_string: str, *, production: bool) -> None:
    """Refuse to serve PostgreSQL tenants without a dedicated read-only role.

    ``production`` should already reflect the runtime environment (and should be
    ``False`` under CI) so hermetic tests and normal local runs are unaffected.
    This closes the "application compromise obtains write access" blocker: the
    login session must switch to a SELECT-only role the engine itself enforces.
    """
    if not connection_string.startswith("postgresql"):
        return
    if not production:
        return
    role_name = os.getenv("SQL_READONLY_ROLE", "").strip()
    if not role_name:
        raise RuntimeError(
            "Production PostgreSQL tenants require SQL_READONLY_ROLE: the login "
            "session must SET ROLE to the dedicated SELECT-only role provisioned "
            "by scripts/setup_least_privilege_gateway_role.sql."
        )
    validate_readonly_role_name(role_name)


class SqlQueryRepository(ISqlQueryRepository):
    """Repository that executes governed, read-only SQL against a database."""

    def __init__(
        self,
        engine: AsyncEngine,
        sql_safety_checker: SqlSafetyChecker,
        query_timeout_seconds: float | None = None,
        sensitive_columns: set[str] | None = None,
        row_filter: Callable[[dict[str, Any]], bool] | None = None,
        data_schema: str = "public",
        metadata_schema: str = "meta",
        tenant_org_id: str | None = None,
        tenant_database_id: str | None = None,
        database_target: str = "primary",
    ) -> None:
        self._engine: AsyncEngine = engine
        self._sql_safety_checker: SqlSafetyChecker = sql_safety_checker
        self._sensitive_columns = {column.lower() for column in (sensitive_columns or set())}
        self._row_filter = row_filter
        self._data_schema = data_schema
        self._metadata_schema = metadata_schema
        self._tenant_org_id = tenant_org_id
        self._tenant_database_id = tenant_database_id
        self._database_target = database_target or "primary"
        configured_timeout = query_timeout_seconds
        if configured_timeout is None:
            configured_timeout = float(os.getenv("SQL_QUERY_TIMEOUT_SECONDS", "30"))
        self._query_timeout_seconds = float(configured_timeout)
        if self._query_timeout_seconds <= 0:
            raise ValueError("SQL_QUERY_TIMEOUT_SECONDS must be greater than zero.")
        self._max_row_limit = int(os.getenv("SQL_QUERY_MAX_ROW_LIMIT", "5000"))
        self._max_result_bytes = int(os.getenv("SQL_QUERY_MAX_RESULT_BYTES", str(5 * 1024 * 1024)))  # 5MB
        self._query_cost_threshold = float(os.getenv("SQL_QUERY_COST_THRESHOLD", "16"))
        self._query_cost_action = os.getenv("SQL_QUERY_COST_ACTION", "deny").strip().lower()
        if self._query_cost_action not in {"allow", "warn", "deny"}:
            self._query_cost_action = "deny"

        # New security‑related configuration
        # Lock timeout in seconds (default 5 seconds), enforced at both the app
        # session AND the database role (DB-side idle_in_transaction/statement
        # budgets are provisioned via setup_least_privilege_gateway_role.sql).
        self._lock_timeout_ms: int = int(float(os.getenv("SQL_LOCK_TIMEOUT_SECONDS", "5")) * 1000)
        if self._lock_timeout_ms <= 0:
            raise ValueError("SQL_LOCK_TIMEOUT_SECONDS must be greater than zero.")
        # Optional dedicated read‑only role name; if set, connections will SET ROLE to it
        self._readonly_role: str | None = validate_readonly_role_name(
            os.getenv("SQL_READONLY_ROLE") or None
        )

    @property
    def database_target(self) -> str:
        """Return the database target used for requests."""

    def estimate_query_cost(self, sql: str) -> dict[str, Any]:
        """Estimate a query's relative execution cost from a few static heuristics."""
        normalized = sql.strip()
        if not normalized:
            return {"score": 0, "level": "low", "reason": "Empty query"}

        upper_sql = normalized.upper()
        score = 1
        reasons: list[str] = []

        if "SELECT" in upper_sql:
            score += 1
        if "JOIN" in upper_sql:
            score += 4
            reasons.append("joins")
        if "GROUP BY" in upper_sql or "HAVING" in upper_sql:
            score += 3
            reasons.append("aggregations")
        if "ORDER BY" in upper_sql:
            score += 2
            reasons.append("sorting")
        if "LIMIT" in upper_sql:
            score += 1
        if "*" in normalized:
            score += 2
            reasons.append("wide scan")
        if "UNION" in upper_sql or "INTERSECT" in upper_sql or "EXCEPT" in upper_sql:
            score += 4
            reasons.append("set operations")
        if "WITH " in upper_sql:
            score += 3
            reasons.append("CTE")

        # Count FROM/JOIN at the outermost level only: strip string literals
        # and parenthesized blocks (subqueries) so correlated subqueries do not
        # inflate the perceived number of table scans.
        outer_sql = re.sub(r"(?:'[^']*'|\"[^\"]*\")", " ", upper_sql)
        outer_sql = re.sub(r"\([^)]*\)", " ", outer_sql)
        number_of_tables = max(1, len(re.findall(r"\bFROM\b|\bJOIN\b", outer_sql)))
        score += number_of_tables - 1

        if "SELECT COUNT" in upper_sql:
            score += 2
            reasons.append("count aggregation")

        if score <= 4:
            level = "low"
        elif score <= 8:
            level = "medium"
        elif score <= 12:
            level = "high"
        else:
            level = "critical"

        return {
            "score": score,
            "level": level,
            "reason": ", ".join(reasons) if reasons else "basic select",
        }

    def evaluate_query_cost(self, sql: str) -> dict[str, Any]:
        """Return the estimated cost and the effective guardrail decision."""
        estimate = self.estimate_query_cost(sql)
        score = int(estimate.get("score", 0))
        threshold = self._query_cost_threshold
        decision = "allow"
        if score >= threshold:
            decision = self._query_cost_action if self._query_cost_action in {"warn", "deny"} else "allow"
        if decision == "deny" and score >= threshold:
            return {
                **estimate,
                "threshold": threshold,
                "decision": "deny",
                "message": "Query exceeds the configured cost threshold and is rejected.",
            }
        if decision == "warn" and score >= threshold:
            return {
                **estimate,
                "threshold": threshold,
                "decision": "warn",
                "message": "Query exceeds the configured cost threshold and will be allowed with a warning.",
            }
        return {
            **estimate,
            "threshold": threshold,
            "decision": "allow",
            "message": "Query is within the configured cost budget.",
        }

    async def explain_query_cost(self, sql: str) -> dict[str, Any]:
        """Run an EXPLAIN plan when the backend supports it and summarize the plan cost."""
        normalized_sql = self._ensure_limit(sql).strip()
        try:
            async with self.get_conn(self._data_schema) as conn:
                dialect_name = conn.dialect.name
                if dialect_name != "postgresql":
                    return {
                        "dialect": dialect_name,
                        "supported": False,
                        "total_cost": None,
                        "plan": {},
                    }
                result = await conn.execute(text(f"EXPLAIN (FORMAT JSON) {normalized_sql}"))
                rows = result.fetchall()
                payload = rows[0][0] if rows and rows[0] and len(rows[0]) else []
                if not payload:
                    return {"dialect": dialect_name, "supported": True, "total_cost": None, "plan": {}}
                plan = (
                    payload[0].get("Plan", {})
                    if isinstance(payload, list) and payload and isinstance(payload[0], dict)
                    else {}
                )
                total_cost = plan.get("Total Cost")
                return {
                    "dialect": dialect_name,
                    "supported": True,
                    "total_cost": total_cost,
                    "plan": plan,
                }
        except Exception as exc:  # pragma: no cover - database-specific execution failure
            logging.warning("EXPLAIN cost estimation failed for query: %s", exc)
            return {
                "dialect": "postgresql",
                "supported": False,
                "total_cost": None,
                "plan": {},
                "error": str(exc),
            }

    @asynccontextmanager
    async def get_conn(self, schema_name: str | None = None) -> AsyncGenerator[AsyncConnection, None]:
        """Yield a read-only database connection, enforcing read-only driver flags."""
        try:
            conn: AsyncConnection = await self._engine.connect()
            try:
                if conn.dialect.name == "postgresql":
                    # Enforce read‑only at the session level so every transaction on
                    # this connection (including EXPLAIN/introspection) is read-only,
                    # not just the explicitly opened one.
                    await conn.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;"))
                    # Optionally switch to a dedicated read‑only role if configured
                    if self._readonly_role:
                        await conn.execute(text(f"SET ROLE {self._readonly_role};"))
                    # Apply timeouts at the SESSION level (not SET LOCAL) so they
                    # hold across every transaction on this pooled connection,
                    # even when a caller opens/commits within the get_conn block.
                    # DB-side (role-level) budgets are the backstop; these app
                    # budgets only ever tighten the session.
                    await conn.execute(
                        text(f"SET SESSION statement_timeout = '{int(self._query_timeout_seconds * 1000)}';")
                    )
                    await conn.execute(
                        text(f"SET SESSION lock_timeout = '{int(self._lock_timeout_ms)}';")
                    )
                    if schema_name:
                        await conn.execute(text(f"SET search_path TO {schema_name}"))
                else:
                    # SQLite read‑only enforcement is handled via the connection URI (mode=ro),
                    # so no per‑connection setup is required here.
                    pass
                yield conn
            finally:
                await conn.close()
        except Exception as e:
            if isinstance(e, SqlStatementExecutionError):
                raise
            logging.error(f"Error connecting to database: {e}")
            raise_sql_execution_exception(
                "Error connecting to database", e, include_traceback=True
            )

    def _apply_sensitive_column_masking(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._sensitive_columns:
            return rows

        masked_rows: list[dict[str, Any]] = []
        for row in rows:
            masked_row: dict[str, Any] = {}
            for key, value in row.items():
                normalized_key = str(key).lower()
                if (
                    normalized_key in self._sensitive_columns
                    or normalized_key.split(".")[-1] in self._sensitive_columns
                ):
                    masked_row[key] = "[MASKED]"
                else:
                    masked_row[key] = value
            masked_rows.append(masked_row)
        return masked_rows

    def _apply_row_filter(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._row_filter:
            return rows
        return [row for row in rows if self._row_filter(row)]

    async def execute_sql_statement(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Validate, guard, and execute a read-only SELECT statement."""
        if not self._sql_safety_checker.is_safe_select_query(sql):
            logging.warning(f"Forbidden SQL statement attempted: {sql}")
            raise ForbiddenSqlStatementError(
                "Only simple SELECT statements are allowed."
            )

        # Add default LIMIT if not present before scoring the query.
        sql = self._ensure_limit(sql)

        cost_guard = self.evaluate_query_cost(sql)
        if cost_guard["decision"] == "deny":
            logging.warning(
                "Query rejected by cost guardrail for %s target: score=%s threshold=%s sql=%s",
                self._database_target,
                cost_guard["score"],
                cost_guard["threshold"],
                sql,
            )
            raise ForbiddenSqlStatementError(
                "Query exceeds the configured cost threshold and is rejected."
            )
        if cost_guard["decision"] == "warn":
            logging.warning(
                "Query cost warning for %s target: score=%s threshold=%s sql=%s",
                self._database_target,
                cost_guard["score"],
                cost_guard["threshold"],
                sql,
            )

        if not self._data_schema.replace("_", "").isalnum():
            raise ValueError("SQL_DATA_SCHEMA must be a valid PostgreSQL schema identifier.")

        async with self.get_conn(self._data_schema) as conn:
            try:
                async def _execute_query() -> list[dict[str, Any]]:
                    result: AsyncResult = await conn.execute(
                        text(sql),
                        parameters=params or {},
                    )

                    if result.returns_rows:
                        rows: Iterable[Row[Any]] = result.fetchall()
                        if len(rows) > self._max_row_limit:
                            raise SqlStatementExecutionError(
                                f"Query result exceeds the maximum allowed row limit ({self._max_row_limit} rows)."
                            )

                        result_dicts: list[dict[str, Any]] = [
                            dict(row._mapping) for row in rows
                        ]

                        # Check byte size
                        serialized_size = len(json.dumps(result_dicts, default=str).encode("utf-8"))
                        if serialized_size > self._max_result_bytes:
                            raise SqlStatementExecutionError(
                                f"Query result exceeds maximum allowed size ({self._max_result_bytes} bytes)."
                            )

                        logging.info(
                            f"SQL executed successfully, returned {len(result_dicts)} rows ({serialized_size} bytes)."
                        )
                        filtered_rows = self._apply_row_filter(result_dicts)
                        return self._apply_sensitive_column_masking(filtered_rows)

                    return []

                try:
                    return await asyncio.wait_for(
                        _execute_query(),
                        timeout=self._query_timeout_seconds,
                    )
                except asyncio.TimeoutError as e:
                    logging.warning(
                        "SQL query timed out after %.2f seconds: %s",
                        self._query_timeout_seconds,
                        sql,
                    )
                    raise SqlStatementExecutionError(
                        f"SQL query timed out after {self._query_timeout_seconds} seconds."
                    ) from e

            except Exception as e:
                if isinstance(e, SqlStatementExecutionError):
                    raise
                logging.error(f"Error executing SQL statement: {e}")
                raise_sql_execution_exception(
                    "Error executing SQL statement", e, include_traceback=True
                )

    async def introspect_schema(self) -> dict[str, Any]:
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
            raise SqlStatementExecutionError(
                f"Error introspecting database schema: {type(e).__name__}: {e}"
            ) from e

    async def _introspect_sqlite(self, conn: AsyncConnection) -> dict[str, Any]:
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
                    "nullable": (
                        bool(col_dict.get("notnull", 0) == 0)
                        if "notnull" in col_dict
                        else bool(col_dict.get("nullable", True))
                    ),
                    "is_primary": (
                        bool(col_dict.get("pk", 0) > 0)
                        if "pk" in col_dict
                        else bool(col_dict.get("is_primary", False))
                    ),
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

    async def _introspect_postgresql(self, conn: AsyncConnection) -> dict[str, Any]:
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

    async def get_table_schema(self, query_embeddings: list[float]) -> dict[str, Any]:
        """Fetch the top 4 most similar database schema entries."""
        query = self._build_similarity_query()

        try:
            async with self.get_conn(self._metadata_schema) as conn:
                embedding_str = f"[{','.join(map(str, query_embeddings))}]"

                result: AsyncResult = await conn.execute(
                    query, {"query_embeddings": embedding_str}
                )
                rows: list[Row[Any]] = result.fetchall()

                formatted = self._format_schema_rows(rows)
                return {"schema": formatted}

        except Exception as e:
            logging.error("Schema embedding lookup failed; vectors must be regenerated: %s", e)
            raise SqlStatementExecutionError(
                "Schema embeddings are unavailable or incompatible with the configured embedding dimensions. "
                "Regenerate schema embeddings before using text-to-SQL."
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
        """Add a default LIMIT 100 if the SQL query doesn't already have a LIMIT clause."""
        # Case-insensitive check for LIMIT clause
        if re.search(r'\bLIMIT\s+\d+\b', sql, re.IGNORECASE):
            return sql
        return f"{sql.rstrip(';')} LIMIT 100"

    def _format_schema_rows(self, rows: Iterable[Row[Any]]) -> str:
        """Format fetched rows into a readable schema string."""
        schema_lines: list[str] = []

        for row in rows:
            raw_json: dict[str, Any] = row[0]
            schema_lines.extend(self._format_single_schema(raw_json))
            schema_lines.append("")  # spacing

        logging.info(
            "Fetched schema embeddings for tenant database %s",
            self._tenant_database_id or "unscoped",
        )
        return "\n".join(schema_lines)

    def _format_single_schema(self, raw_json: dict[str, Any]) -> list[str]:
        """Format a single raw_json schema entry into readable lines."""
        lines: list[str] = []

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
