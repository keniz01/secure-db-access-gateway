from __future__ import annotations

from typing import Protocol

import sqlparse

from repositories.sql_validators.rules.sql_rules import (
    MustBeSelectRule,
    NoCommentRule,
    NoForbiddenKeywordsRule,
    NoSubqueryRule,
    NoUnionOrSetOpsRule,
    NoWithCTERule,
    SingleStatementRule,
    SqlSafetyRule,
)
from repositories.sql_validators.sql_cleaner import clean_sql


# ----------------------
# Checker
# ----------------------
class SqlSafetyChecker(Protocol):
    """Protocol for a SQL safety checker."""

    def is_safe_select_query(self, query: str) -> bool:
        """
        Check if a given SQL query is a "safe" SELECT statement.

        Args:
            query: The SQL query string to validate.

        Returns:
            True if the query is a safe SELECT statement, False otherwise.

        """
        ...


class DefaultSqlSafetyChecker:
    """Validator for "safe" SELECT SQL queries using a pluggable rule system."""

    def __init__(self, max_query_length: int = 10000):
        """Initialize the DefaultSqlSafetyChecker with a set of validation rules."""
        self.max_query_length = max_query_length
        self.rules: list[SqlSafetyRule] = [
            # Fundamental
            SingleStatementRule(),
            MustBeSelectRule(),
            NoCommentRule(),
            NoWithCTERule(),
            NoForbiddenKeywordsRule(
                [
                    "delete",
                    "insert",
                    "update",
                    "drop",
                    "create",
                    "alter",
                    "commit",
                    "rollback",
                ],
            ),
            # Advanced Structural Guardrails
            NoSubqueryRule(),
            NoUnionOrSetOpsRule(),
        ]

    def is_safe_select_query(self, query: str) -> bool:
        """
        Check if a given SQL query is a "safe" SELECT statement.

        Args:
            query: The SQL query string to validate.

        Returns:
            True if the query is a safe SELECT statement, False otherwise.

        """
        if not query or not query.strip():
            return False

        if len(query) > self.max_query_length:
            return False

        parsed = sqlparse.parse(query)
        if not parsed:
            return False

        stmt = parsed[0]
        return all(rule.check(stmt, query) for rule in self.rules)

    def clean_and_validate_sql(self, sql: str) -> str:
        """
        Clean and validate SQL query from LLM output.

        This method combines SQL cleaning (for LLM artifacts) with safety validation.
        It first cleans the SQL to remove markdown, prefixes, and fix common issues,
        then validates it using the configured safety rules.

        Args:
            sql: Raw SQL query string (potentially from LLM)

        Returns:
            Cleaned and validated SQL query string

        Raises:
            ValueError: If SQL cannot be cleaned or fails validation
        """
        # Step 1: Clean the SQL (remove LLM artifacts)
        cleaned_sql = clean_sql(sql)

        # Step 2: Validate the cleaned SQL
        if not self.is_safe_select_query(cleaned_sql):
            raise ValueError(
                "SQL query failed safety validation. Only simple SELECT statements are allowed."
            )

        return cleaned_sql
