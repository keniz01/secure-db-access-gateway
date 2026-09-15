from __future__ import annotations

from collections import defaultdict
from typing import Protocol

import sqlparse
from sqlparse import tokens as sql_tokens

from repositories.sql_validators.ast_analyzer import FORBIDDEN_FUNCTIONS, AstSqlAnalyzer
from repositories.sql_validators.rules.advanced_sql_rules import (
    ForbiddenFunctionsRule,
    ForbiddenTableRule,
)
from repositories.sql_validators.rules.sql_rules import (
    MustBeSelectRule,
    NoCommentRule,
    NoForbiddenKeywordsRule,
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
    """Validator for "safe" SELECT SQL queries using AST parsing and a pluggable rule system."""

    def __init__(
        self,
        max_query_length: int = 10000,
        table_allowlist: set[str] | None = None,
        column_allowlist: dict[str, set[str]] | None = None,
    ):
        """Initialize the DefaultSqlSafetyChecker with a set of validation rules."""
        self.max_query_length = max_query_length
        self.table_allowlist = {table.lower() for table in (table_allowlist or set())}
        self.column_allowlist = {
            table.lower(): {column.lower() for column in columns}
            for table, columns in (column_allowlist or {}).items()
        }
        self.ast_analyzer = AstSqlAnalyzer()
        self.rules: list[SqlSafetyRule] = [
            # Fundamental
            SingleStatementRule(),
            MustBeSelectRule(),
            NoCommentRule(),
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
                    "truncate",
                    "grant",
                    "revoke",
                    "merge",
                    "call",
                    "execute",
                    "copy",
                ],
            ),
            # The AST node‑type allowlist is intentionally disabled: it rejects
            # legitimate analytical queries (subqueries, CTEs, UNION/INTERSECT/EXCEPT,
            # aggregate functions). The gap is covered by the remaining layered rules:
            # single‑statement + SELECT‑only checks, the mutation/dangerous‑function
            # traversal in AstSqlAnalyzer.is_strictly_read_only, the forbidden
            # keyword/function/table rules below, and the read‑only DB enforcement.
            # AllowedNodeTypesRule(
            #     allowed={
            #         "select",
            #         "from",
            #         "join",
            #         "where",
            #         "group",
            #         "having",
            #         "order",
            #         "limit",
            #         "identifier",
            #         "literal",
            #         "column",
            #         "expression",
            #     }
            # ),
            # The forbidden-function set is the SAME canonical set the AST
            # analyzer walks with (ast_analyzer.FORBIDDEN_FUNCTIONS), so both
            # layers enforce exactly the same list — no gap between rule and
            # analyzer coverage.
            ForbiddenFunctionsRule(forbidden=FORBIDDEN_FUNCTIONS),
            ForbiddenTableRule(
                forbidden_prefixes={"pg_", "information_schema"}
            ),
        ]

    def _extract_table_names(self, stmt: sqlparse.sql.Statement) -> set[str]:
        tables: set[str] = set()
        in_from_clause = False

        for token in stmt.tokens:
            if token.ttype in sql_tokens.Keyword and token.value.upper() in {"FROM", "JOIN"}:
                in_from_clause = True
                continue
            if in_from_clause and token.ttype in sql_tokens.Keyword and token.value.upper() in {
                "WHERE",
                "GROUP",
                "HAVING",
                "ORDER",
                "LIMIT",
                "UNION",
                "INTERSECT",
                "EXCEPT",
            }:
                break
            if not in_from_clause:
                continue

            if isinstance(token, sqlparse.sql.Identifier):
                table_name = token.get_real_name()
                if table_name:
                    tables.add(table_name.lower())
            elif isinstance(token, sqlparse.sql.IdentifierList):
                for identifier in token.get_identifiers():
                    table_name = identifier.get_real_name()
                    if table_name:
                        tables.add(table_name.lower())

        return tables

    def _extract_selected_columns(self, stmt: sqlparse.sql.Statement) -> dict[str, set[str]]:
        selected_columns: dict[str, set[str]] = defaultdict(set)
        in_select_clause = False

        for token in stmt.tokens:
            if token.ttype in sql_tokens.Keyword and token.value.upper() == "SELECT":
                in_select_clause = True
                continue
            if in_select_clause and token.ttype in sql_tokens.Keyword and token.value.upper() in {
                "FROM",
                "WHERE",
                "GROUP",
                "HAVING",
                "ORDER",
                "LIMIT",
                "UNION",
                "INTERSECT",
                "EXCEPT",
                "JOIN",
                "LEFT",
                "RIGHT",
                "FULL",
                "OUTER",
                "INNER",
                "CROSS",
            }:
                break
            if not in_select_clause:
                continue

            if isinstance(token, sqlparse.sql.IdentifierList):
                for identifier in token.get_identifiers():
                    parent_name = identifier.get_parent_name()
                    real_name = identifier.get_real_name()
                    if real_name:
                        selected_columns[(parent_name or "*").lower()].add(real_name.lower())
            elif isinstance(token, sqlparse.sql.Identifier):
                parent_name = token.get_parent_name()
                real_name = token.get_real_name()
                if real_name:
                    selected_columns[(parent_name or "*").lower()].add(real_name.lower())
            elif isinstance(token, sqlparse.sql.Function) and "*" in str(token):
                selected_columns["*"].add("*")

        return selected_columns

    def _enforces_allowlist(self, stmt: sqlparse.sql.Statement, raw: str) -> bool:
        if not self.table_allowlist and not self.column_allowlist:
            return True

        # Use AST extraction as the primary, most accurate mechanism
        ast_tables = set(self.ast_analyzer.extract_tables(raw))
        fallback_tables = self._extract_table_names(stmt)
        tables = ast_tables or fallback_tables

        if self.table_allowlist and (tables - self.table_allowlist):
            return False

        if self.column_allowlist:
            ast_columns = self.ast_analyzer.extract_selected_columns_by_table(raw)
            selected_columns = ast_columns or self._extract_selected_columns(stmt)
            if any("*" in columns for columns in selected_columns.values()):
                return False

            for table_name, allowed_columns in self.column_allowlist.items():
                if table_name not in tables:
                    continue

                requested_columns = set()
                for requested_table, columns in selected_columns.items():
                    if requested_table in {"*", table_name}:
                        requested_columns |= columns

                if requested_columns and not requested_columns.issubset(allowed_columns):
                    return False

        return True

    def _contains_forbidden_mutation(self, stmt: sqlparse.sql.Statement) -> bool:
        for token in stmt.flatten():
            if (
                token.ttype in (sql_tokens.DDL, sql_tokens.DML, sql_tokens.Keyword, sql_tokens.Keyword.DCL)
                or str(token.ttype).endswith(".DCL")
            ):
                value = str(token.value).strip().lower()
                if value in {
                    "delete",
                    "insert",
                    "update",
                    "drop",
                    "create",
                    "alter",
                    "commit",
                    "rollback",
                    "truncate",
                    "grant",
                    "revoke",
                    "merge",
                    "call",
                    "execute",
                }:
                    return True
        return False

    def is_safe_select_query(self, query: str) -> bool:
        """
        Check if a given SQL query is a "safe" SELECT statement.

        Fail-closed: any unexpected parser/analyzer exception (deep nesting,
        malformed quoting, control bytes, …) yields ``False``, never a raise,
        so a hostile query can neither crash classification nor slip through
        an unhandled error path.

        Args:
            query: The SQL query string to validate.

        Returns:
            True if the query is a safe SELECT statement, False otherwise.

        """
        try:
            return self._is_safe_select_query(query)
        except Exception:
            # Fuzz/edge-case inputs must be REJECTED, not raised on. The DB
            # never sees a query whose classification path raised.
            return False

    def _is_safe_select_query(self, query: str) -> bool:
        if not query or not query.strip():
            return False

        if "\x00" in query:
            return False

        if len(query) > self.max_query_length:
            return False

        parsed = sqlparse.parse(query)
        if not parsed:
            return False

        stmt = parsed[0]
        if not all(rule.check(stmt, query) for rule in self.rules):
            return False

        if self._contains_forbidden_mutation(stmt):
            return False

        # Additional AST-level verification via sqlglot
        if not self.ast_analyzer.is_strictly_read_only(query):
            return False

        return self._enforces_allowlist(stmt, query)

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
            ValueError: If SQL cannot be cleaned or fails validation.

        """
        # Step 1: Clean the SQL (remove LLM artifacts)
        cleaned_sql = clean_sql(sql)

        # Step 2: Validate the cleaned SQL
        if not self.is_safe_select_query(cleaned_sql):
            raise ValueError(
                "SQL query failed safety validation. Only simple SELECT statements are allowed."
            )

        return cleaned_sql
