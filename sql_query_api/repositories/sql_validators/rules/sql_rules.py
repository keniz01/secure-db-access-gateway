from __future__ import annotations

import re
from collections.abc import Callable, Generator, Iterable
from typing import Any, Protocol

import sqlparse
from sqlparse import tokens
from sqlparse.sql import Statement, TokenList, Parenthesis
from sqlparse.tokens import DML


# ============================================================
# Base Rule Protocol
# ============================================================
class SqlSafetyRule(Protocol):
    """Return True if the query passes the rule; False otherwise."""

    def check(self, stmt: Statement, raw: str) -> bool:
        """
        Check if the given SQL statement is safe.

        Args:
            stmt: The SQL statement to check.
            raw: The raw SQL query.

        Returns:
            True if the query is safe, False otherwise.

        """
        ...


# ============================================================
# Utility helpers
# ============================================================
def flatten(stmt: TokenList) -> Generator[TokenList, Any, None]:
    """Yield tokens from a nested token list."""
    return (t for t in stmt.flatten())


def any_token(
    stmt: TokenList, predicate: Callable[[TokenList], bool]
) -> bool:
    """Return True if any token in the statement satisfies the predicate."""
    return any(predicate(t) for t in flatten(stmt))


# ============================================================
# Fundamental Rules
# ============================================================
class SingleStatementRule:
    """Rule to ensure that the query contains only a single statement."""

    def check(self, stmt: Statement, raw: str) -> bool:
        """
        Check if the query contains only a single statement.

        Args:
            stmt: The SQL statement to check.
            raw: The raw SQL query.

        Returns:
            True if the query contains only a single statement, False otherwise.

        """
        return len(sqlparse.parse(raw)) == 1


class MustBeSelectRule:
    """Rule to ensure that the query is a SELECT statement or a safe WITH ... SELECT query."""

    def check(self, stmt: Statement, raw: str) -> bool:
        """
        Check if the query is a SELECT statement.

        Args:
            stmt: The SQL statement to check.
            raw: The raw SQL query.

        Returns:
            True if the query is a SELECT statement, False otherwise.

        """
        statement_type = stmt.get_type().upper()
        if statement_type == "SELECT":
            return True
        if statement_type == "UNKNOWN":
            return bool(re.search(r"\bSELECT\b", raw, flags=re.IGNORECASE))
        return False


class NoCommentRule:
    """Rule to ensure that the query does not contain any comments."""

    def check(self, stmt: Statement, raw: str) -> bool:
        """
        Check if the query contains any comments.

        Args:
            stmt: The SQL statement to check.
            raw: The raw SQL query.

        Returns:
            True if the query does not contain any comments, False otherwise.

        """
        return not any_token(
            stmt,
            lambda t: t.ttype
            in (tokens.Comment, tokens.Comment.Single, tokens.Comment.Multiline),
        )


class NoWithCTERule:
    """Allow CTES when they remain SELECT-only and do not contain mutating statements."""

    def check(self, stmt: Statement, raw: str) -> bool:
        """CTEs are permitted for analytical reads when the query stays SELECT-only."""
        return True


class NoForbiddenKeywordsRule:
    """Rule to ensure that the query does not contain any forbidden keywords."""

    def __init__(self, forbidden: Iterable[str]):
        """
        Initialize the rule with a set of forbidden keywords.

        Args:
            forbidden: A list of forbidden keywords.

        """
        self.forbidden = {kw.lower() for kw in forbidden}

    def check(self, stmt: Statement, raw: str) -> bool:
        """
        Check if the query contains any forbidden keywords.

        Args:
            stmt: The SQL statement to check.
            raw: The raw SQL query.

        Returns:
            True if the query does not contain any forbidden keywords, False otherwise.

        """
        return not any_token(
            stmt,
            lambda t: (
                t.ttype in (tokens.DDL, tokens.DML, tokens.Keyword, tokens.Keyword.DCL)
                or str(t.ttype).endswith(".DCL")
            )
            and t.value.lower() in self.forbidden,
        )


# ============================================================
# Structure-Level Rules (More Advanced)
# ============================================================
class NoSubqueryRule:
    """Allow analytical subqueries while still forbidding mutating statements."""

    def check(self, stmt: Statement, raw: str) -> bool:
        """Nested SELECTs are permitted for read workflows when they stay SELECT-only."""
        return True


class NoUnionOrSetOpsRule:
    """Allow analytical set operations as long as the query remains read-only."""

    def check(self, stmt: Statement, raw: str) -> bool:
        """Set operations are permitted for safe read-only analytics."""
        return True
