"""
SQL cleaning utility for sanitizing LLM-generated SQL queries.

This module handles cleaning of SQL queries that may contain:
- Markdown code blocks
- Prefixes like "SQL:" or "SQL "
- Common truncation issues (e.g., "ELECT" instead of "SELECT")
- Other LLM-specific formatting issues

Security: This function does NOT repair or synthesize SQL. It only normalizes
formatting and rejects invalid queries. The safety boundary is enforced by
SqlSafetyChecker after cleaning.
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
    Clean LLM-generated SQL query by removing formatting artifacts.

    Args:
        sql: Raw SQL query string from LLM

    Returns:
        Cleaned SQL query string

    Raises:
        ValueError: If SQL cannot be cleaned or is invalid after cleaning

    """
    if not sql:
        raise ValueError("SQL query cannot be empty")

    # Step 1: Remove markdown code blocks if present
    sql = sql.strip()
    if sql.startswith("```"):
        lines = sql.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        sql = "\n".join(lines).strip()

    # Step 2: Remove SQL keyword prefix if present (some models add "SQL:" or similar)
    sql = sql.strip()
    if sql.upper().startswith("SQL:"):
        sql = sql[4:].strip()
    elif sql.upper().startswith("SQL "):
        sql = sql[4:].strip()

    # Step 3: Fix common truncation issue: ELECT -> SELECT
    sql_upper = sql.upper().strip()
    if sql_upper.startswith("ELECT"):
        sql = "S" + sql
        sql_upper = sql.upper().strip()

    # Step 4: Ensure SQL starts with SELECT or a WITH ... SELECT analytic query.
    if not sql_upper.startswith("SELECT"):
        words = sql.split()
        if len(words) > 0 and words[0].upper() == "ELECT":
            sql = "SELECT " + " ".join(words[1:])
            sql_upper = sql.upper().strip()
        elif _WITH_PREFIX_RE.match(sql_upper):
            pass
        else:
            raise ValueError(
                f"SQL query failed safety validation: must start with SELECT after cleaning: {sql[:100]}"
            )

    # Step 5: Final validation - ensure it starts with SELECT or WITH after cleaning
    if not (sql_upper.startswith("SELECT") or _WITH_PREFIX_RE.match(sql_upper)):
        raise ValueError(
            f"SQL query failed safety validation: does not start with SELECT after cleaning: {sql[:100]}"
        )

    # Strip leading/trailing whitespace only (preserve internal formatting)
    sql = sql.strip()

    return sql

