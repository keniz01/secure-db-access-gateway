from __future__ import annotations

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
    """Rule to ensure that the query is a SELECT statement."""

    def check(self, stmt: Statement, raw: str) -> bool:
        """
        Check if the query is a SELECT statement.

        Args:
            stmt: The SQL statement to check.
            raw: The raw SQL query.

        Returns:
            True if the query is a SELECT statement, False otherwise.

        """
        return stmt.get_type() == "SELECT"


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
    """Rule to ensure that the query does not contain any CTEs."""

    def check(self, stmt: Statement, raw: str) -> bool:
        """
        Check if the query contains any CTEs.

        Args:
            stmt: The SQL statement to check.
            raw: The raw SQL query.

        Returns:
            True if the query does not contain any CTEs, False otherwise.

        """
        return not any_token(stmt, lambda t: t.match(tokens.Keyword.CTE, "WITH"))


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
            lambda t: (t.ttype in (tokens.DDL, tokens.DML, tokens.Keyword))
            and t.value.lower() in self.forbidden,
        )


# ============================================================
# Structure-Level Rules (More Advanced)
# ============================================================
class NoSubqueryRule:
    """Disallow SELECT inside parentheses or nested SELECTs."""

    def check(self, stmt: Statement, raw: str) -> bool:
        """
        Check if the query contains any subqueries.

        Args:
            stmt: The SQL statement to check.
            raw: The raw SQL query.

        Returns:
            True if the query does not contain any subqueries, False otherwise.

        """
        # Allow the top-level SELECT
        # (i.e., the first DML token can be SELECT)
        found_top_level_select = False

        for tok in stmt.tokens:
            if tok.ttype is DML and tok.value.lower() == 'select':
                found_top_level_select = True
                continue

            # look for nested SELECT
            if self._contains_select(tok):
                return False

        return True

    def _contains_select(self, token):
        """
        Recursively check if a token contains a nested SELECT.
        Never count the top-level DML token.
        """

        # Parenthesis containing a SELECT = SUBQUERY
        if isinstance(token, Parenthesis):
            inner = token.value.lower()
            if 'select' in inner:
                return True

        # Walk inside group tokens
        if hasattr(token, 'tokens'):
            for tok in token.tokens:
                if tok.ttype is DML and tok.value.lower() == 'select':
                    return True
                if self._contains_select(tok):
                    return True

        return False


class NoUnionOrSetOpsRule:
    """Rule to ensure that the query does not contain any set operations."""

    def check(self, stmt: Statement, raw: str) -> bool:
        """
        Check if the query contains any set operations.

        Args:
            stmt: The SQL statement to check.
            raw: The raw SQL query.

        Returns:
            True if the query does not contain any set operations, False otherwise.

        """
        # UNION, INTERSECT, EXCEPT
        set_ops = {"union", "intersect", "except"}
        return not any_token(
            stmt,
            lambda t: t.ttype == tokens.Keyword and t.value.lower() in set_ops,
        )
