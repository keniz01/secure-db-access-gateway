from ast import Dict
import logging as logger
import os
from typing import Any, Callable, List, Optional, Dict
import asyncio
from openai import AsyncOpenAI, RateLimitError, APIError, APITimeoutError
import json

import strawberry
from strawberry.fastapi import GraphQLRouter

from dependencies.dependency_container import setup_container
from services.music_query_service import IMusicQueryService

client = AsyncOpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=os.getenv("GITHUB_TOKEN"),
    timeout=15.0,  # hard timeout per request
)

# Get the database URL from environment, raise error if not set
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

# Dependency injection setup
_container = setup_container(DATABASE_URL)
_music_query_service = _container.resolve(IMusicQueryService)


# Strawberry input type for the query
@strawberry.input
class SqlStatementRequest:
    sql_statement: str = ""


# JSON scalar for dynamic result sets
@strawberry.scalar(description="Arbitrary JSON object")
class JSON:
    serialize: Callable[[Any], Any] = staticmethod(lambda value: value)
    parse_value: Callable[[Any], Any] = staticmethod(lambda value: value)


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

        # Only allow SELECT queries
        if not sql.lower().startswith("select"):
            raise ValueError("Only SELECT statements are allowed.")

        try:
            logger.info("Executing SQL query (length=%d)", len(sql))
            result: List[Dict[str, Any]] = await _music_query_service.execute_sql_statement(sql)            
            return result
        except Exception as e:
            logger.exception("Error executing SQL")
            # Don't expose internal error details to client
            raise Exception("Failed to execute SQL statement. Please verify your query syntax.")


# Create schema and router
schema = strawberry.Schema(query=Query)
graphql_app = GraphQLRouter(schema)

router = graphql_app  # Export router for FastAPI
