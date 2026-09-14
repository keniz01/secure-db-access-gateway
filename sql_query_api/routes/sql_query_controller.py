import logging as logger
import re
from collections.abc import Callable
from typing import Any

import strawberry
from graphql import GraphQLError
from strawberry.extensions import QueryDepthLimiter
from strawberry.fastapi import GraphQLRouter

from auth import Principal
from config.app_logger import log_audit_event
from dependencies.tenant_service_provider import TenantServiceProvider
from exceptions.sql_statement_execution_exception import SqlStatementExecutionError
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from services.abstract_sql_query_service import ISqlQueryService
from services.policy_engine import PolicyEvaluator
from services.query_gateway import GovernedQueryGateway, GovernedQueryRequest
from services.tenant_database_resolver import (
    TenantDatabaseConfig,
    TenantDatabaseResolutionError,
    TenantDatabaseResolver,
)


def _sanitize_db_error(exc: Exception) -> str:
    """
    Extract a single-line, non-sensitive detail from a DB execution error.

    Strips SQL text and traceback context so only the root-cause message is
    returned.  The result is safe to surface in structured error extensions
    to trusted internal callers (e.g. the text-to-sql feedback loop).
    """
    msg = str(exc.message if hasattr(exc, "message") else exc)
    # Extract the "↳ Caused by ..." line if present
    m = re.search(r"↳\s*Caused by\s*(.+?)(?:\n|$|\[SQL:)", msg)
    if m:
        detail = m.group(1).strip()
        # Strip the [SQL: ...] prefix if it leaked in
        detail = re.sub(r"\[SQL:.*", "", detail, flags=re.DOTALL).strip()
        # Collapse "<class 'asyncpg.exceptions.UndefinedTableError'>" noise
        detail = re.sub(r"<class '([^']+)'>", r"\1", detail)
        return detail
    # Plain-message errors (timeout, row limit) — return as-is
    return msg.strip()

_tenant_database_resolver = TenantDatabaseResolver.from_environment()
_tenant_service_provider = TenantServiceProvider(_tenant_database_resolver)
# Kept as an inert test fixture attribute for older callers; it is never used
# to execute requests.
_sql_query_service: ISqlQueryService | None = None
_sql_safety_checker = DefaultSqlSafetyChecker()
_query_gateway = GovernedQueryGateway(
    lambda: _tenant_service_provider,
    _sql_safety_checker,
    audit=lambda event_type, **payload: log_audit_event(event_type, **payload),
    policy_evaluator=PolicyEvaluator.from_environment(),
)


# Strawberry input type for the query
@strawberry.input
class SqlStatementRequest:
    """GraphQL input type for a governed SQL request."""

    sql_statement: str = ""
    database_id: str = "default"


# JSON scalar for dynamic result sets
@strawberry.scalar(description="Arbitrary JSON object")
class JSON:
    """GraphQL scalar that serializes arbitrary JSON values."""

    serialize: Callable[[Any], Any] = staticmethod(lambda value: value)
    parse_value: Callable[[Any], Any] = staticmethod(lambda value: value)


# Schema Info type for getTableSchema (vector-embedding-based) response
@strawberry.type
class SchemaInfo:
    """GraphQL response type wrapping a schema text payload."""

    schema: str


# Types for dynamic schema introspection
@strawberry.type
class ColumnInfo:
    """GraphQL type describing a single table column."""

    name: str
    type: str
    nullable: bool
    is_primary: bool


@strawberry.type
class ForeignKeyInfo:
    """GraphQL type describing a foreign key relationship."""

    column: str
    foreign_schema: str
    foreign_table: str
    foreign_column: str


@strawberry.type
class TableInfo:
    """GraphQL type describing a table and its schema details."""

    name: str
    schema_name: str
    columns: list[ColumnInfo]
    foreign_keys: list[ForeignKeyInfo]


@strawberry.type
class DatabaseSchemaInfo:
    """GraphQL response type containing introspected tables."""

    tables: list[TableInfo]


@strawberry.type
class QueryCostEstimate:
    """GraphQL type describing a relative query cost estimate."""

    score: int
    level: str
    reason: str


@strawberry.type
class PolicySimulation:
    """GraphQL type exposing a policy evaluation result."""

    allowed: bool
    reason: str
    policy_ids: list[str]
    row_restrictions: JSON
    masked_columns: list[str]


# GraphQL Query type
@strawberry.type
class Query:
    """Root GraphQL query type with governed and introspection fields."""

    @staticmethod
    def _request_context(
        info: strawberry.Info, database_id: str | None
    ) -> tuple[Principal, TenantDatabaseConfig, ISqlQueryService]:
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
        """Return a liveness response for health checks."""
        return "GraphQL SQL Query API is running!"

    @strawberry.field(description="Estimate the relative computational cost of a SELECT query")
    async def estimate_query_cost(
        self,
        info: strawberry.Info,
        sql_statement: str,
        database_id: str = "default",
    ) -> QueryCostEstimate:
        """Estimate the relative computational cost of a SELECT query."""
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
    async def execute_sql_statement(self, info: strawberry.Info, request: SqlStatementRequest) -> list[JSON]:
        """Execute a governed SELECT statement and return its rows."""
        sql = request.sql_statement.strip()
        principal, binding, service = Query._request_context(info, request.database_id)

        # Input validation: Check if SQL is empty
        if not sql:
            raise ValueError("SQL statement cannot be empty.")

        # Input validation: Limit query length to prevent DoS
        if len(sql) > 10000:
            raise ValueError("SQL statement is too long (max 10000 characters).")

        try:
            logger.info("Executing SQL query (length=%d)", len(sql))
            return await _query_gateway.execute(
                GovernedQueryRequest(
                    principal=principal,
                    database_id=binding.database_id,
                    sql=sql,
                )
            )
        except ValueError as e:
            # ValueError from cleaning/validation - provide clear error message
            logger.warning("SQL validation failed: %s", str(e))
            raise ValueError(str(e)) from e
        except SqlStatementExecutionError as e:
            logger.exception("Error executing SQL")
            detail = _sanitize_db_error(e)
            raise GraphQLError(
                message="Failed to execute SQL statement. Please verify your query syntax.",
                extensions={
                    "sqlQueryApi": {
                        "code": "SQL_EXECUTION_ERROR",
                        "details": detail,
                    }
                },
            ) from e
        except Exception:
            logger.exception("Error executing SQL")
            # Don't expose internal error details to client
            raise Exception("Failed to execute SQL statement. Please verify your query syntax.") from None

    @strawberry.field(description="Explain policy enforcement without executing SQL")
    def simulate_policy(
        self,
        info: strawberry.Info,
        request: SqlStatementRequest,
    ) -> PolicySimulation:
        """Evaluate policy enforcement without executing SQL."""
        principal, binding, _ = Query._request_context(info, request.database_id)
        if principal.role != "admin":
            raise PermissionError("Policy simulation requires an administrator role.")
        sql = request.sql_statement.strip()
        if not sql:
            raise ValueError("SQL statement cannot be empty.")
        try:
            cleaned_sql = _sql_safety_checker.clean_and_validate_sql(sql)
            decision = _query_gateway.simulate(
                GovernedQueryRequest(principal=principal, database_id=binding.database_id, sql=cleaned_sql)
            )
        except ValueError:
            raise
        return PolicySimulation(
            allowed=decision.allowed,
            reason=decision.reason,
            policy_ids=list(decision.policy_ids),
            row_restrictions=decision.row_restrictions,
            masked_columns=sorted(decision.masked_columns),
        )

    @strawberry.field(description="Get table schema information using vector embeddings")
    async def get_table_schema(
        self,
        info: strawberry.Info,
        embeddings: list[float],
        database_id: str = "default",
    ) -> SchemaInfo:
        """
        Retrieve relevant database schema information using vector similarity search.

        Args:
            info: GraphQL request context.
            embeddings: List of float values representing the query embedding vector.
            database_id: Logical database identifier to introspect.

        Returns:
            SchemaInfo containing formatted schema information.

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
            result: dict[str, Any] = await service.get_table_schema(embeddings)
            schema_text = result.get("schema", "")
            return SchemaInfo(schema=schema_text)
        except Exception:
            logger.exception("Error fetching table schema")
            # Don't expose internal error details to client
            raise Exception(
                "Schema embeddings are unavailable or incompatible with 768 dimensions. "
                "Regenerate schema embeddings before using text-to-SQL."
            ) from None

    @strawberry.field(description="Dynamically introspect the connected database schema")
    async def introspect_schema(
        self,
        info: strawberry.Info,
        database_id: str = "default",
    ) -> DatabaseSchemaInfo:
        """
        Read the live database schema (tables, columns, PKs, FKs) directly from
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
            result: dict[str, Any] = await service.introspect_schema()
            raw_tables: list[dict[str, Any]] = result.get("tables", [])

            tables: list[TableInfo] = []
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
        except Exception:
            logger.exception("Error introspecting database schema")
            raise Exception("Failed to introspect database schema.") from None


# Create schema and router
schema = strawberry.Schema(query=Query, extensions=[QueryDepthLimiter(max_depth=6)])
graphql_app = GraphQLRouter(schema)

router = graphql_app  # Export router for FastAPI
