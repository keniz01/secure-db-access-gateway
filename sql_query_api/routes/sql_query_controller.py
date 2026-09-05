import logging as logger
from typing import Any, Callable, List, Dict

import strawberry
from strawberry.fastapi import GraphQLRouter

from config.app_logger import log_audit_event
from dependencies.tenant_service_provider import TenantServiceProvider
from services.abstract_sql_query_service import ISqlQueryService
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from services.query_gateway import GovernedQueryGateway, GovernedQueryRequest
from services.tenant_database_resolver import (
    TenantDatabaseResolver,
    TenantDatabaseResolutionError,
)
from services.policy_engine import PolicyEvaluator

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


@strawberry.type
class PolicySimulation:
    allowed: bool
    reason: str
    policy_ids: List[str]
    row_restrictions: JSON
    masked_columns: List[str]


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
            raise ValueError(str(e))
        except Exception as e:
            logger.exception("Error executing SQL")
            # Don't expose internal error details to client
            raise Exception("Failed to execute SQL statement. Please verify your query syntax.")

    @strawberry.field(description="Explain policy enforcement without executing SQL")
    def simulate_policy(
        self,
        info: strawberry.Info,
        request: SqlStatementRequest,
    ) -> PolicySimulation:
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
