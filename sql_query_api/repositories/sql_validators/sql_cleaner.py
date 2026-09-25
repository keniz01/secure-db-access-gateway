"""
SQL cleaning utility for sanitizing LLM-generated SQL queries.

This module handles cleaning of SQL queries that may contain:
- Markdown code blocks
- Prefixes like "SQL:" or "SQL "
- Common truncation issues (e.g., "ELECT" instead of "SELECT")
- Other LLM-specific formatting issues
"""

import logging
import re

logger = logging.getLogger(__name__)

# A read-only analytical query may legitimately begin with WITH (CTE). The
# gateway validates contents with the SQL safety checker, so a leading WITH is
# not a signal that the query needs a "SELECT *" prefix.
_WITH_PREFIX_RE = re.compile(r"^WITH\b", re.IGNORECASE)


def clean_sql(sql: str) -> str:
    """
    Clean LLM-generated SQL query by removing formatting artifacts and fixing common issues.

    Args:
        sql: Raw SQL query string from LLM

    Returns:
        Cleaned SQL query string

    Raises:
        ValueError: If SQL cannot be cleaned or is invalid after cleaning

    """
    if not sql:
        raise ValueError("SQL query cannot be empty")

    # Log raw response for debugging
    logger.debug("Cleaning raw SQL response: %s", sql[:200])

    # Step 1: Remove markdown code blocks if present
    sql = sql.strip()
    if sql.startswith("```"):
        # Remove markdown code blocks
        lines = sql.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        sql = "\n".join(lines).strip()
        logger.debug("Removed markdown code blocks")

    # Step 2: Remove SQL keyword prefix if present (some models add "SQL:" or similar)
    # Use startswith instead of lstrip to avoid removing characters from SELECT
    sql = sql.strip()
    if sql.upper().startswith("SQL:"):
        sql = sql[4:].strip()  # Remove "SQL:" prefix
        logger.debug("Removed 'SQL:' prefix")
    elif sql.upper().startswith("SQL "):
        sql = sql[4:].strip()  # Remove "SQL " prefix
        logger.debug("Removed 'SQL ' prefix")

    # Step 3: Fix common truncation issues: ELECT -> SELECT
    sql_upper = sql.upper().strip()
    if sql_upper.startswith("ELECT"):
        logger.warning("Detected 'ELECT' instead of 'SELECT', fixing...")
        sql = "S" + sql
        sql_upper = sql.upper().strip()

    # Step 4: Ensure SQL starts with SELECT or a WITH ... SELECT analytic query.
    if not sql_upper.startswith("SELECT"):
        # Try to find SELECT in the first few words
        words = sql.split()
        if len(words) > 0 and words[0].upper() == "ELECT":
            sql = "SELECT " + " ".join(words[1:])
            logger.warning("Fixed 'ELECT' to 'SELECT'")
            sql_upper = sql.upper().strip()
        elif _WITH_PREFIX_RE.match(sql_upper):
            # A read-only CTE (WITH ... SELECT) is accepted; the safety checker
            # still validates the full statement afterwards.
            logger.debug("SQL begins with WITH; leaving it untouched")
        elif not sql_upper.startswith("SELECT"):
            # If it doesn't start with SELECT at all, attempt to fix
            logger.warning("SQL does not start with SELECT, attempting to fix...")
            if sql_upper.startswith("ELECT"):
                sql = "S" + sql
                sql_upper = sql.upper().strip()
            else:
                # Do not synthesize SELECT * - fail-closed per data minimization
                if any(keyword in sql_upper for keyword in ["FROM", "WHERE", "JOIN"]):
                    raise ValueError(
                        f"SQL query failed safety validation: must start with SELECT after cleaning: {sql[:100]}"
                    )

    # Step 5: Final validation - ensure it starts with SELECT or WITH after cleaning
    if not (sql_upper.startswith("SELECT") or _WITH_PREFIX_RE.match(sql_upper)):
        raise ValueError(
            f"SQL query failed safety validation: does not start with SELECT after cleaning: {sql[:100]}"
        )

    logger.info("Cleaned SQL query: %s", sql[:200])
    return sql

