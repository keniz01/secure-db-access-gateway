import logging as logger
import os
from typing import Any, Callable, List, Optional, Dict

import strawberry
from strawberry.fastapi import GraphQLRouter

from dependencies.dependency_container import setup_container
from services.music_query_service import IMusicQueryService
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker

def read_secret_from_file(file_path: str) -> str:
    """Read secret from file, fallback to empty string if file not found."""
    try:
        with open(file_path, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

# Get the database URL from environment or file, raise error if not set
DATABASE_URL = os.getenv("DATABASE_URL") or read_secret_from_file(os.getenv("DATABASE_URL_FILE", ""))
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable or DATABASE_URL_FILE is not set")

# Dependency injection setup
_container = setup_container(DATABASE_URL)
_music_query_service = _container.resolve(IMusicQueryService)
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


# Schema Info type for getTableSchema response
@strawberry.type
class SchemaInfo:
    schema: str


# GraphQL Query type
@strawberry.type
class Query:
    @strawberry.field(description="Health check")
    def ping(self) -> str:
        return "GraphQL Music Query API is running!"

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
            result: List[Dict[str, Any]] = await _music_query_service.execute_sql_statement(cleaned_sql)            
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
            result: Dict[str, Any] = await _music_query_service.get_table_schema(embeddings)
            schema_text = result.get("schema", "")
            return SchemaInfo(schema=schema_text)
        except Exception as e:
            logger.exception("Error fetching table schema")
            # Don't expose internal error details to client
            raise Exception("Failed to fetch table schema. Please verify your embeddings.")


# Create schema and router
schema = strawberry.Schema(query=Query)
graphql_app = GraphQLRouter(schema)

router = graphql_app  # Export router for FastAPI
