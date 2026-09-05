import logging as logger
import re
import time
from typing import Any, Callable, List, Dict

import strawberry
from strawberry.fastapi import GraphQLRouter

from config.app_logger import log_audit_event
from metrics import observe_query
from dependencies.tenant_service_provider import TenantServiceProvider
from services.abstract_sql_query_service import ISqlQueryService
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from services.tenant_database_resolver import (
    TenantDatabaseResolver,
    TenantDatabaseResolutionError,
)

_tenant_database_resolver = TenantDatabaseResolver.from_environment()
_tenant_service_provider = TenantServiceProvider(_tenant_database_resolver)
# Kept as an inert test fixture attribute for older callers; it is never used
# to execute requests.
_sql_query_service: ISqlQueryService | None = None
_sql_safety_checker = DefaultSqlSafetyChecker()


def _extract_tables_touched(sql: str) -> List[str]:
    targets = re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_\.]*)", sql, flags=re.IGNORECASE)
    unique_tables: List[str] = []
    for table in targets:
        table_name = table.split(".")[-1]
        if table_name and table_name not in unique_tables:
            unique_tables.append(table_name)
    return unique_tables


# Strawberry input type for the query
@strawberry.input
class SqlStatementRequest:
    sql_statement: str = ""
    database_id: str = "default"


# JSON scalar for dynamic result sets
@strawberry.scalar(description="Arbitrary JSON object")
class JSON:
    serialize: Callable[[Any], Any] = staticmethod(lambda value: value)
    parse_value: Callable[[Any], Any] = staticmethod(lambda value: value)


# Schema Info type for getTableSchema (vector-embedding-based) response
@strawberry.type
class SchemaInfo:
    schema: str


# Types for dynamic schema introspection
@strawberry.type
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    is_primary: bool


@strawberry.type
class ForeignKeyInfo:
    column: str
    foreign_schema: str
    foreign_table: str
    foreign_column: str


@strawberry.type
class TableInfo:
    name: str
    schema_name: str
    columns: List[ColumnInfo]
    foreign_keys: List[ForeignKeyInfo]


@strawberry.type
class DatabaseSchemaInfo:
    tables: List[TableInfo]


@strawberry.type
class QueryCostEstimate:
    score: int
    level: str
    reason: str


# GraphQL Query type
@strawberry.type
class Query:
    @staticmethod
    def _request_context(info: strawberry.Info, database_id: str | None):
        request_obj = getattr(info, "context", {}).get("request") if getattr(info, "context", None) else None
        principal = getattr(getattr(request_obj, "state", None), "principal", None) if request_obj else None
        if principal is None:
            raise PermissionError("Authenticated principal is required.")
        try:
            binding, service = _tenant_service_provider.resolve(principal, database_id)
        except TenantDatabaseResolutionError as exc:
            raise PermissionError(str(exc)) from exc
        except Exception:
            logger.error("Tenant database service resolution failed.")
            raise PermissionError("Database is unavailable.") from None

        return principal, binding, service

    @strawberry.field(description="Health check")
    def ping(self) -> str:
        return "GraphQL SQL Query API is running!"

    @strawberry.field(description="Estimate the relative computational cost of a SELECT query")
    async def estimate_query_cost(
        self,
        info: strawberry.Info,
        sql_statement: str,
        database_id: str = "default",
    ) -> QueryCostEstimate:
        sql = sql_statement.strip()
        if not sql:
            raise ValueError("SQL statement cannot be empty.")

        _, _, service = Query._request_context(info, database_id)
        cost = service.repository.estimate_query_cost(sql)
        return QueryCostEstimate(
            score=cost.get("score", 0),
            level=cost.get("level", "low"),
            reason=cost.get("reason", "basic select"),
        )

    @strawberry.field(description="Executes a SQL SELECT statement")
    async def execute_sql_statement(self, info: strawberry.Info, request: SqlStatementRequest) -> List[JSON]:
        sql = request.sql_statement.strip()
        principal, binding, service = Query._request_context(info, request.database_id)

        # Input validation: Check if SQL is empty
        if not sql:
            raise ValueError("SQL statement cannot be empty.")

        # Input validation: Limit query length to prevent DoS
        if len(sql) > 10000:
            raise ValueError("SQL statement is too long (max 10000 characters).")

        try:
            # Clean and validate SQL (handles LLM-generated SQL cleaning and safety validation)
            cleaned_sql = _sql_safety_checker.clean_and_validate_sql(sql)
            logger.info("Executing SQL query (length=%d)", len(cleaned_sql))
            started_at = time.perf_counter()
            log_audit_event(
                "sql_query",
                user=principal.email,
                org_id=principal.org_id,
                database_id=binding.database_id,
                database_target=service.repository.database_target,
                query=cleaned_sql,
                tables_touched=_extract_tables_touched(cleaned_sql),
            )
            result: List[Dict[str, Any]] = await service.execute_sql_statement(cleaned_sql)
            elapsed = time.perf_counter() - started_at
            observe_query(org_id=principal.org_id, row_count=len(result), duration_seconds=elapsed)
            return result
        except ValueError as e:
            # ValueError from cleaning/validation - provide clear error message
            logger.warning("SQL validation failed: %s", str(e))
            raise ValueError(str(e))
        except Exception as e:
            logger.exception("Error executing SQL")
            # Don't expose internal error details to client
            raise Exception("Failed to execute SQL statement. Please verify your query syntax.")

    @strawberry.field(description="Get table schema information using vector embeddings")
    async def get_table_schema(
        self,
        info: strawberry.Info,
        embeddings: List[float],
        database_id: str = "default",
    ) -> SchemaInfo:
        """
        Retrieves relevant database schema information using vector similarity search.

        Args:
            embeddings: List of float values representing the query embedding vector (768 dimensions)

        Returns:
            SchemaInfo containing formatted schema information
        """
        # Input validation: Check if embeddings list is provided
        if not embeddings:
            raise ValueError("Embeddings list cannot be empty.")

        # Stored schema vectors must be regenerated before using the 768-dimension model.
        if len(embeddings) != 768:
            raise ValueError(f"Embeddings must be exactly 768 dimensions, got {len(embeddings)}")

        try:
            principal, binding, service = Query._request_context(info, database_id)
            log_audit_event(
                "schema_embedding_lookup",
                user=principal.email,
                org_id=principal.org_id,
                database_id=binding.database_id,
            )
            logger.info("Fetching table schema with embeddings (dimensions=%d)", len(embeddings))
            result: Dict[str, Any] = await service.get_table_schema(embeddings)
            schema_text = result.get("schema", "")
            return SchemaInfo(schema=schema_text)
        except Exception as e:
            logger.exception("Error fetching table schema")
            # Don't expose internal error details to client
            raise Exception(
                "Schema embeddings are unavailable or incompatible with 768 dimensions. "
                "Regenerate schema embeddings before using text-to-SQL."
            )

    @strawberry.field(description="Dynamically introspect the connected database schema")
    async def introspect_schema(
        self,
        info: strawberry.Info,
        database_id: str = "default",
    ) -> DatabaseSchemaInfo:
        """
        Reads the live database schema (tables, columns, PKs, FKs) directly from
        information_schema (PostgreSQL) or sqlite_master/PRAGMA (SQLite).
        No hard-coded table names are assumed.
        """
        try:
            principal, binding, service = Query._request_context(info, database_id)
            log_audit_event(
                "schema_introspection",
                user=principal.email,
                org_id=principal.org_id,
                database_id=binding.database_id,
            )
            logger.info("Introspecting database schema")
            result: Dict[str, Any] = await service.introspect_schema()
            raw_tables: List[Dict[str, Any]] = result.get("tables", [])

            tables: List[TableInfo] = []
            for t in raw_tables:
                columns = [
                    ColumnInfo(
                        name=c["name"],
                        type=c["type"],
                        nullable=c["nullable"],
                        is_primary=c["is_primary"],
                    )
                    for c in t.get("columns", [])
                ]
                fks = [
                    ForeignKeyInfo(
                        column=fk["column"],
                        foreign_schema=fk["foreign_schema"],
                        foreign_table=fk["foreign_table"],
                        foreign_column=fk["foreign_column"],
                    )
                    for fk in t.get("foreign_keys", [])
                ]
                tables.append(
                    TableInfo(
                        name=t["name"],
                        schema_name=t["schema_name"],
                        columns=columns,
                        foreign_keys=fks,
                    )
                )

            return DatabaseSchemaInfo(tables=tables)
        except Exception as e:
            logger.exception("Error introspecting database schema")
            raise Exception("Failed to introspect database schema.")


# Create schema and router
schema = strawberry.Schema(query=Query)
graphql_app = GraphQLRouter(schema)

router = graphql_app  # Export router for FastAPI
