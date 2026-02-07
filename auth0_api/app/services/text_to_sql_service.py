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
        self, query: str, execute: bool = False
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
            schema = await self._get_relevant_schema(embeddings)

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
                execution_result = await self._execute_sql(sql)
                result["results"] = execution_result

            return result

        except AIServiceError as e:
            logger.error("AI service error in text-to-SQL: %s", e)
            return {"error": f"Failed to generate SQL: {str(e)}", "sql": None}
        except Exception as e:
            logger.exception("Unexpected error in text-to-SQL: %s", e)
            return {"error": f"Unexpected error: {str(e)}", "sql": None}

    async def _get_relevant_schema(self, embeddings: List[float]) -> str:
        """
        Retrieve relevant schema information using vector embeddings.

        Args:
            embeddings: List of float values representing the query embedding

        Returns:
            Formatted schema string
        """
        graphql_query = """
        query GetTableSchema($embeddings: [Float!]!) {
            getTableSchema(embeddings: $embeddings) {
                schema
            }
        }
        """

        variables = {"embeddings": embeddings}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.sql_query_api_url,
                    json={"query": graphql_query, "variables": variables},
                    headers={"Content-Type": "application/json"},
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

        Args:
            natural_language: Natural language query
            schema: Relevant database schema information

        Returns:
            Generated SQL query string
        """
        system_prompt = """You are a SQL expert. Your task is to convert natural language queries into valid PostgreSQL SELECT statements.

Rules:
1. Generate ONLY SELECT statements - no INSERT, UPDATE, DELETE, CREATE, ALTER, or DROP
2. Use the provided schema information to determine correct table and column names
3. Follow PostgreSQL syntax
4. Include appropriate WHERE clauses based on the query
5. Add LIMIT clauses when appropriate (default to LIMIT 100 if not specified)
6. Return ONLY the SQL query, no explanations or markdown formatting
7. Do not include any text before or after the SQL statement
8. Use proper SQL escaping for string values

Example:
User: "Show me all albums released in 2000"
Schema: albums: album_id, title, release_date, artist_id
SQL: SELECT * FROM albums WHERE EXTRACT(YEAR FROM release_date) = 2000 LIMIT 100
"""

        user_prompt = f"""Schema Information:
{schema}

Natural Language Query:
{natural_language}

Generate a PostgreSQL SELECT query for the above natural language query using the schema information provided."""

        try:
            sql = await self.ai_service.get_greeting(
                system=system_prompt,
                user=user_prompt,
                max_tokens=500,  # SQL queries can be longer
            )

            if not sql:
                raise AIServiceError("LLM returned empty SQL query")

            # Clean up the SQL - remove markdown code blocks if present
            sql = sql.strip()
            if sql.startswith("```"):
                # Remove markdown code blocks
                lines = sql.split("\n")
                lines = [line for line in lines if not line.strip().startswith("```")]
                sql = "\n".join(lines).strip()

            # Remove SQL keyword prefix if present (some models add "SQL:" or similar)
            sql = sql.lstrip("SQL:").strip()

            logger.info("Generated SQL query: %s", sql[:200])
            return sql

        except Exception as e:
            logger.error("Error generating SQL with LLM: %s", e)
            raise AIServiceError(f"Failed to generate SQL: {str(e)}")

    async def _execute_sql(self, sql: str) -> List[Dict[str, Any]]:
        """
        Execute SQL query via SQL Query API.

        Args:
            sql: SQL query string to execute

        Returns:
            List of result dictionaries
        """
        graphql_query = """
        query ExecuteSql($sql: String!) {
            executeSqlStatement(request: { sqlStatement: $sql })
        }
        """

        variables = {"sql": sql}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.sql_query_api_url,
                    json={"query": graphql_query, "variables": variables},
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()

                data = response.json()
                if "errors" in data:
                    error_msg = data["errors"][0].get("message", "Unknown GraphQL error")
                    logger.error("GraphQL error executing SQL: %s", error_msg)
                    raise Exception(f"Failed to execute SQL: {error_msg}")

                results = data.get("data", {}).get("executeSqlStatement", [])
                return results

        except httpx.HTTPError as e:
            logger.error("HTTP error executing SQL: %s", e)
            raise Exception(f"Failed to connect to SQL Query API: {str(e)}")
        except Exception as e:
            logger.error("Error executing SQL: %s", e)
            raise

