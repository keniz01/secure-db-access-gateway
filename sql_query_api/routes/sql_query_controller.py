import logging as logger
import os
from typing import Any, Callable, List, Optional, Dict

import strawberry
from strawberry.fastapi import GraphQLRouter

from dependencies.dependency_container import setup_container
from services.abstract_sql_query_service import ISqlQueryService
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker

def read_secret_from_file(file_path: str) -> str:
    """Read secret from file, fallback to empty string if file not found."""
    try:
        with open(file_path, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or read_secret_from_file(os.getenv("DATABASE_URL_FILE", ""))
    or "sqlite+aiosqlite:///:memory:"
)

# Dependency injection setup
_container = setup_container(DATABASE_URL)
_sql_query_service = _container.resolve(ISqlQueryService)
_sql_safety_checker = DefaultSqlSafetyChecker()


# Strawberry input type for the query
@strawberry.input
class SqlStatementRequest:
    sql_statement: str = ""


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


# GraphQL Query type
@strawberry.type
class Query:
    @strawberry.field(description="Health check")
    def ping(self) -> str:
        return "GraphQL SQL Query API is running!"

    @strawberry.field(description="Executes a SQL SELECT statement")
    async def execute_sql_statement(self, request: SqlStatementRequest) -> List[JSON]:
        sql = request.sql_statement.strip()

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
            result: List[Dict[str, Any]] = await _sql_query_service.execute_sql_statement(cleaned_sql)
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
    async def get_table_schema(self, embeddings: List[float]) -> SchemaInfo:
        """
        Retrieves relevant database schema information using vector similarity search.

        Args:
            embeddings: List of float values representing the query embedding vector (384 dimensions)

        Returns:
            SchemaInfo containing formatted schema information
        """
        # Input validation: Check if embeddings list is provided
        if not embeddings:
            raise ValueError("Embeddings list cannot be empty.")

        # Input validation: Check embedding dimensions (should be 384)
        if len(embeddings) != 384:
            raise ValueError(f"Embeddings must be exactly 384 dimensions, got {len(embeddings)}")

        try:
            logger.info("Fetching table schema with embeddings (dimensions=%d)", len(embeddings))
            result: Dict[str, Any] = await _sql_query_service.get_table_schema(embeddings)
            schema_text = result.get("schema", "")
            return SchemaInfo(schema=schema_text)
        except Exception as e:
            logger.exception("Error fetching table schema")
            # Don't expose internal error details to client
            raise Exception("Failed to fetch table schema. Please verify your embeddings.")

    @strawberry.field(description="Dynamically introspect the connected database schema")
    async def introspect_schema(self) -> DatabaseSchemaInfo:
        """
        Reads the live database schema (tables, columns, PKs, FKs) directly from
        information_schema (PostgreSQL) or sqlite_master/PRAGMA (SQLite).
        No hard-coded table names are assumed.
        """
        try:
            logger.info("Introspecting database schema")
            result: Dict[str, Any] = await _sql_query_service.introspect_schema()
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
