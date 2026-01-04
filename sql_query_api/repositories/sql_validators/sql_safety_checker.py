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

    def __init__(self):
        """Initialize the DefaultSqlSafetyChecker with a set of validation rules."""
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
        parsed = sqlparse.parse(query)
        if not parsed:
            return False

        stmt = parsed[0]
        return all(rule.check(stmt, query) for rule in self.rules)
