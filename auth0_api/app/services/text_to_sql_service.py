"""
Text-to-SQL service for converting natural language queries to SQL.
"""

import json
from typing import Dict, List, Optional, Any
import httpx
from app.config.settings import settings
from app.config.logging import get_logger
from app.services.ai_service import AIService
from app.exceptions.handlers import AIServiceError
from prompts.registry import load_prompt, render_prompt

logger = get_logger(__name__)


class TextToSqlService:
    """Service for converting natural language queries to SQL."""

    def __init__(self, ai_service: AIService):
        """
        Initialize the TextToSqlService.

        Args:
            ai_service: AIService instance for embeddings and LLM operations
        """
        self.ai_service = ai_service
        self.sql_query_api_url = settings.SQL_QUERY_API_URL

    async def generate_sql_from_text(
        self,
        query: str,
        execute: bool = False,
        access_token: Optional[str] = None,
        database_id: str = "default",
    ) -> Dict[str, Any]:
        """
        Generate SQL from natural language query and optionally execute it.

        Args:
            query: Natural language query string
            execute: Whether to execute the generated SQL and return results

        Returns:
            Dictionary containing:
            - sql: Generated SQL query
            - schema: Retrieved schema information (if available)
            - results: Query results (if execute=True)
            - error: Error message (if any)
        """
        try:
            # Step 1: Generate embeddings from natural language query
            logger.info("Generating embeddings for query: %s", query[:100])
            embeddings = await self.ai_service.generate_embeddings(query)

            # Step 2: Get relevant schema using embeddings
            logger.info("Retrieving relevant schema information")
            schema = await self._get_relevant_schema(embeddings, access_token, database_id)

            # Step 3: Generate SQL using LLM
            logger.info("Generating SQL from natural language query")
            sql = await self._generate_sql_with_llm(query, schema)

            result: Dict[str, Any] = {
                "sql": sql,
                "schema": schema,
            }

            # Step 4: Optionally execute the SQL
            if execute:
                logger.info("Executing generated SQL query")
                execution_result = await self._execute_sql(sql, access_token, database_id)
                result["results"] = execution_result

            return result

        except AIServiceError as e:
            logger.error("AI service error in text-to-SQL: %s", e)
            return {"error": f"Failed to generate SQL: {str(e)}", "sql": None}
        except ValueError as e:
            # Handle validation errors from sql_query_api
            logger.warning("SQL validation error: %s", e)
            return {"error": str(e), "sql": result.get("sql") if "result" in locals() else None}
        except Exception as e:
            logger.exception("Unexpected error in text-to-SQL: %s", e)
            return {"error": f"Unexpected error: {str(e)}", "sql": None}

    async def _get_relevant_schema(
        self,
        embeddings: List[float],
        access_token: Optional[str] = None,
        database_id: str = "default",
    ) -> str:
        """
        Retrieve relevant schema information using vector embeddings.

        Args:
            embeddings: List of float values representing the query embedding

        Returns:
            Formatted schema string
        """
        graphql_query = """
        query GetTableSchema($embeddings: [Float!]!, $databaseId: String!) {
            getTableSchema(embeddings: $embeddings, databaseId: $databaseId) {
                schema
            }
        }
        """

        variables = {"embeddings": embeddings, "databaseId": database_id}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.sql_query_api_url,
                    json={"query": graphql_query, "variables": variables},
                    headers=self._api_headers(access_token),
                )
                response.raise_for_status()

                data = response.json()
                if "errors" in data:
                    error_msg = data["errors"][0].get("message", "Unknown GraphQL error")
                    logger.error("GraphQL error fetching schema: %s", error_msg)
                    raise Exception(f"Failed to fetch schema: {error_msg}")

                schema_info = data.get("data", {}).get("getTableSchema", {})
                schema_text = schema_info.get("schema", "")
                return schema_text

        except httpx.HTTPError as e:
            logger.error("HTTP error fetching schema: %s", e)
            raise Exception(f"Failed to connect to SQL Query API: {str(e)}")
        except Exception as e:
            logger.error("Error fetching schema: %s", e)
            raise

    async def _generate_sql_with_llm(self, natural_language: str, schema: str) -> str:
        """
        Generate SQL query from natural language using LLM.
        Uses a prompt format based on table-augmented generation best practices.
        System and user prompts are loaded from the file-based prompt registry.
        """
        system_config = load_prompt("text_to_sql")
        user_config = load_prompt("text_to_sql/user")
        system_prompt = render_prompt(
            system_config, schema=schema, question=natural_language
        )
        user_prompt = render_prompt(
            user_config,
            schema=schema,
            natural_language=natural_language,
        )

        try:
            sql = await self.ai_service.get_greeting(
                system=system_prompt,
                user=user_prompt,
                max_tokens=500,  # SQL queries can be longer
            )

            if not sql:
                raise AIServiceError("LLM returned empty SQL query")

            # Log raw response for debugging
            logger.debug("Raw SQL response from LLM: %s", sql[:200])

            # Return raw SQL - cleaning and validation will be handled by sql_query_api
            logger.info("Generated SQL query (raw): %s", sql[:200])
            return sql

        except Exception as e:
            logger.error("Error generating SQL with LLM: %s", e)
            raise AIServiceError(f"Failed to generate SQL: {str(e)}")

    async def _execute_sql(
        self,
        sql: str,
        access_token: Optional[str] = None,
        database_id: str = "default",
    ) -> List[Dict[str, Any]]:
        """
        Execute SQL query via SQL Query API.

        Args:
            sql: SQL query string to execute

        Returns:
            List of result dictionaries
        """
        graphql_query = """
        query ExecuteSql($sql: String!, $databaseId: String!) {
            executeSqlStatement(request: { sqlStatement: $sql, databaseId: $databaseId })
        }
        """

        variables = {"sql": sql, "databaseId": database_id}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.sql_query_api_url,
                    json={"query": graphql_query, "variables": variables},
                    headers=self._api_headers(access_token),
                )
                response.raise_for_status()

                data = response.json()
                if "errors" in data:
                    error_msg = data["errors"][0].get("message", "Unknown GraphQL error")
                    logger.error("GraphQL error executing SQL: %s", error_msg)
                    # Preserve validation error messages from sql_query_api
                    # These may include cleaning/validation failures
                    raise ValueError(f"SQL validation or execution failed: {error_msg}")

                results = data.get("data", {}).get("executeSqlStatement", [])
                return results

        except httpx.HTTPError as e:
            logger.error("HTTP error executing SQL: %s", e)
            raise Exception(f"Failed to connect to SQL Query API: {str(e)}")
        except ValueError as e:
            # Re-raise ValueError (validation errors) as-is to preserve error messages
            logger.warning("SQL validation error: %s", str(e))
            raise
        except Exception as e:
            logger.error("Error executing SQL: %s", e)
            raise Exception(f"Failed to execute SQL: {str(e)}")

    @staticmethod
    def _api_headers(access_token: Optional[str] = None) -> Dict[str, str]:
        """Build headers for authenticated SQL Query API requests."""
        headers = {"Content-Type": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        return headers
