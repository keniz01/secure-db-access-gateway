"""
Text-to-SQL service for converting natural language queries to SQL.
"""

import re
from typing import Dict, List, Optional, Any
import httpx
import sqlglot
from sqlglot import exp
from app.config.settings import settings
from app.config.logging import get_logger
from app.services.ai_service import AIService
from app.exceptions.handlers import AIServiceError
from prompts.registry import load_prompt, render_prompt

logger = get_logger(__name__)

#: Maximum generation+execution round-trips attempted when a DB execution
#: error indicates the LLM can correct its SQL (e.g. bad alias, bad column).
MAX_EXECUTION_ATTEMPTS = 3

#: Root-cause substrings that indicate a correctable, query-level mistake.
RETRYABLE_EXECUTION_ERROR_MARKERS = (
    "syntax error",
    "missing from-clause",
    "missing from ",
    "does not exist",
    "undefined column",
    "undefined table",
    "no such column",
    "no such table",
    "unknown column",
    "unknown table",
    "unexpected token",
    "ungrouped column",
    "grouping error",
    "must appear in the group by",
)


class SqlExecutionError(Exception):
    """Raised when the generated SQL reaches the database but fails to execute.

    Carries the sanitized root-cause detail from the SQL Query API so the
    generation loop can feed it back to the LLM for correction.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Failed to execute SQL: {detail}")


def _is_retryable_execution_error(detail: str) -> bool:
    """Return True when the DB root-cause looks correctable by the LLM."""
    lowered = (detail or "").lower()
    return any(marker in lowered for marker in RETRYABLE_EXECUTION_ERROR_MARKERS)


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

            if not execute:
                # Step 3: Generate SQL using LLM
                logger.info("Generating SQL from natural language query")
                sql = await self._generate_sql_with_llm(query, schema)
                return {
                    "sql": sql,
                    "schema": schema,
                }

            # Steps 3-4: Generate SQL and execute it. When the database rejects
            # the statement with a correctable error (bad alias, typo'd name),
            # feed the root cause back to the LLM and retry a bounded number of
            # times before giving up.
            feedback = ""
            for attempt in range(1, MAX_EXECUTION_ATTEMPTS + 1):
                logger.info("Generating SQL from natural language query")
                sql = await self._generate_sql_with_llm(query, schema, feedback=feedback)

                logger.info("Executing generated SQL query")
                try:
                    execution_result = await self._execute_sql(sql, access_token, database_id)
                    return {
                        "sql": sql,
                        "schema": schema,
                        "results": execution_result,
                    }
                except SqlExecutionError as e:
                    if not _is_retryable_execution_error(e.detail):
                        logger.warning("SQL execution failed (non-retryable): %s", e.detail)
                        return {"error": e.detail, "sql": sql}
                    if attempt >= MAX_EXECUTION_ATTEMPTS:
                        logger.warning(
                            "SQL execution failed after %d attempts: %s",
                            MAX_EXECUTION_ATTEMPTS,
                            e.detail,
                        )
                        return {"error": e.detail, "sql": sql}
                    logger.warning(
                        "SQL execution failed (attempt %d/%d), scheduling correction: %s",
                        attempt,
                        MAX_EXECUTION_ATTEMPTS,
                        e.detail,
                    )
                    feedback = (
                        "The SQL statement you generated was rejected by the database "
                        f"with the following error:\n{e.detail}\n"
                        "Fix the statement (for example an undeclared table alias or a "
                        "misspelled column/table name) and return a single corrected "
                        "SELECT statement only."
                    )

        except AIServiceError as e:
            logger.error("AI service error in text-to-SQL: %s", e)
            return {"error": f"Failed to generate SQL: {str(e)}", "sql": None}
        except ValueError as e:
            # Handle validation errors from sql_query_api
            logger.warning("SQL validation error: %s", e)
            return {"error": str(e), "sql": sql if "sql" in locals() else None}
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

    async def _generate_sql_with_llm(
        self,
        natural_language: str,
        schema: str,
        feedback: str = "",
    ) -> str:
        """
        Generate SQL query from natural language using LLM.
        Uses a prompt format based on table-augmented generation best practices.
        System and user prompts are loaded from the file-based prompt registry.
        A lightweight local pre-check validates the output and triggers one retry
        with error feedback before the SQL is sent to the SQL Query API.
        """
        system_config = load_prompt("text_to_sql")
        user_config = load_prompt("text_to_sql/user")
        system_prompt = render_prompt(
            system_config, schema=schema, question=natural_language
        )

        last_feedback = feedback
        for attempt in range(1, 3):
            user_prompt = render_prompt(
                user_config,
                schema=schema,
                natural_language=natural_language,
            )
            if last_feedback:
                user_prompt = (
                    f"{user_prompt}\n\nYour previous answer was rejected:\n"
                    f"{last_feedback}\nReturn a single corrected SELECT statement only."
                )

            try:
                sql = await self.ai_service.get_greeting(
                    system=system_prompt,
                    user=user_prompt,
                    max_tokens=500,  # SQL queries can be longer
                )
            except Exception as e:
                logger.error("Error generating SQL with LLM: %s", e)
                raise AIServiceError(f"Failed to generate SQL: {str(e)}")

            if not sql:
                raise AIServiceError("LLM returned empty SQL query")

            logger.debug("Raw SQL response from LLM: %s", sql[:200])

            ok, precheck_feedback = self._precheck_sql(sql)
            if ok:
                logger.info("Generated SQL query (raw): %s", sql[:200])
                return sql

            logger.warning("SQL pre-check failed (attempt %d): %s", attempt, precheck_feedback)
            if last_feedback:
                last_feedback = f"{last_feedback}\nAdditionally: {precheck_feedback}"
            else:
                last_feedback = precheck_feedback

        raise AIServiceError(f"Failed to generate valid SQL: {last_feedback}")

    @staticmethod
    def _precheck_sql(raw_sql: str) -> tuple[bool, str]:
        """
        Lightweight pre-check of LLM-generated SQL before it is sent to the
        SQL Query API for full validation. Catches obvious formatting,
        multi-statement, and alias-scope problems so they can be retried cheaply.

        Returns (ok, feedback): ok is True when the candidate looks plausible,
        otherwise feedback describes the problem for the retry prompt.
        """
        sql = _strip_sql_framing(raw_sql)

        if not sql.upper().startswith("SELECT"):
            return False, "The SQL must start with SELECT; it started with something else."

        # Multi-statement check: strip string literals before counting ';'
        # so a semicolon inside a string does not cause a false positive.
        no_strings = re.sub(r"'(?:[^']|'')*'", "''", sql)
        no_strings = re.sub(r'"(?:[^"]|"")*"', '""', no_strings)
        body = no_strings.rstrip()
        if ";" in body[:-1]:
            return False, (
                "Multiple SQL statements detected. "
                "Return exactly one SELECT statement that answers the whole question."
            )

        alias_error = _find_unresolved_table_aliases(sql)
        if alias_error:
            return False, alias_error

        grouping_error = _find_ungrouped_correlated_subquery(sql)
        if grouping_error:
            return False, grouping_error

        return True, ""

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
                    error = data["errors"][0]
                    error_msg = error.get("message", "Unknown GraphQL error")
                    extensions = (error.get("extensions") or {}).get("sqlQueryApi") or {}
                    # Prefer the sanitized root-cause detail for DB execution
                    # failures so the caller can give corrective feedback to the LLM.
                    if extensions.get("code") == "SQL_EXECUTION_ERROR" and extensions.get("details"):
                        raise SqlExecutionError(extensions["details"])
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
        except SqlExecutionError:
            # Re-raise DB execution errors as-is so the generation loop can act on them
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


def _strip_sql_framing(raw_sql: str) -> str:
    """Remove markdown fences, 'SQL:'/'SQL' prefixes, and surrounding whitespace."""
    sql = raw_sql.strip()
    if sql.startswith("```"):
        lines = [line for line in sql.split("\n") if not line.strip().startswith("```")]
        sql = "\n".join(lines).strip()
    if sql.upper().startswith("SQL:"):
        sql = sql[4:].strip()
    elif sql.upper().startswith("SQL "):
        sql = sql[4:].strip()
    return sql


def _find_unresolved_table_aliases(sql: str) -> str:
    """
    Detect references to table aliases/qualifiers that are never declared.

    Collects every alias (and real table name) declared in FROM/JOIN clauses,
    CTE names, and derived-table subquery aliases across the whole statement,
    then flags any qualified column reference whose qualifier is not declared.
    A whole-statement set is used deliberately: outer aliases referenced inside
    correlated scalar subqueries are legitimate, and the database remains the
    final arbiter for anything this conservative check cannot decide.

    Returns an empty string when no unresolvable reference is found, otherwise a
    human-readable description for the retry prompt.
    """
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except Exception as e:
        return f"The SQL failed to parse: {e}"

    if len(statements) != 1 or statements[0] is None or not isinstance(statements[0], exp.Query):
        return "The SQL must be a single SELECT query."

    root = statements[0]
    declared: set[str] = set()

    for table in root.find_all(exp.Table):
        if table.alias:
            declared.add(table.alias.lower())
        if table.name:
            declared.add(table.name.lower())

    for cte in root.find_all(exp.CTE):
        if cte.alias:
            declared.add(cte.alias.lower())

    for subquery in root.find_all(exp.Subquery):
        if subquery.alias:
            declared.add(subquery.alias.lower())

    referenced: set[str] = set()
    for column in root.find_all(exp.Column):
        qualifier = column.table
        if not qualifier:
            continue
        # A multi-part qualifier (schema.column) keeps only the final segment,
        # matching how table aliases are attached by the parser.
        referenced.add(qualifier.split(".")[-1].lower())

    unresolved = sorted(referenced - declared)
    if unresolved:
        names = ", ".join(f'"{name}"' for name in unresolved)
        return (
            f"The statement references {names} which is/are not declared as a "
            "table name or alias in any FROM or JOIN clause. Every alias used in "
            "the SELECT list, WHERE clause, or subquery must be declared in a "
            "FROM or JOIN clause."
        )
    return ""


def _find_ungrouped_correlated_subquery(sql: str) -> str:
    """
    Detect a top-level aggregate SELECT that correlates a scalar subquery in the
    SELECT list against an outer-table column without a GROUP BY clause.

    PostgreSQL rejects such statements with `GroupingError: subquery uses
    ungrouped column ... from outer query` (the classic "how many X does Y have
    and which X has the most Z" pattern). The alias pre-check cannot catch this
    because every alias is declared; the flaw is grouping semantics. Returns a
    description for the retry prompt, or an empty string when the pattern is
    absent.
    """
    try:
        select = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return ""
    if not isinstance(select, exp.Select):
        return ""
    # With DISTINCT, the non-aggregate select-list columns are implicitly
    # grouped by Postgres, so a correlated subquery may be valid.
    if select.args.get("distinct"):
        return ""
    if select.args.get("group"):
        return ""

    has_top_level_aggregate = False
    subqueries = []
    for projection in select.expressions:
        inner = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(inner, exp.AggFunc):
            has_top_level_aggregate = True
        if isinstance(inner, exp.Subquery):
            subqueries.append(inner)

    if not has_top_level_aggregate or not subqueries:
        return ""

    outer_names = set()
    from_spec = select.args.get("from_")
    if from_spec is not None:
        _collect_table_names(from_spec.this, outer_names)
    for join in select.args.get("joins") or []:
        _collect_table_names(join.this, outer_names)

    for subquery in subqueries:
        for column in subquery.find_all(exp.Column):
            qualifier = column.table
            if qualifier and qualifier.split(".")[-1].lower() in outer_names:
                return (
                    "The statement uses an aggregate function (e.g. COUNT, SUM, MAX) at the "
                    "top level while a SELECT-list subquery references an outer-query column "
                    f'("{qualifier}".{column.name}) without a GROUP BY clause. PostgreSQL '
                    "rejects this with 'subquery uses ungrouped column ... from outer query'. "
                    "Either add a GROUP BY clause that includes every referenced outer column, "
                    "or restructure the query so the aggregate and the correlated subquery do "
                    "not reference ungrouped outer columns together."
                )
    return ""


def _collect_table_names(node, names: set) -> None:
    """Add a FROM/JOIN table's alias (or real name) to ``names``."""
    if isinstance(node, exp.Table):
        if node.alias:
            names.add(node.alias.lower())
            return
        if node.name:
            names.add(node.name.lower())
