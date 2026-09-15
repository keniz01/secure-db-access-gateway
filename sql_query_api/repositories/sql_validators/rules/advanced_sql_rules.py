"""
Advanced SQL safety rules for production hardening.

These rules complement the existing simple keyword-based checks by using
`sqlglot` to inspect the parsed abstract syntax tree (AST) of a query. They
provide deterministic guarantees that only allowed constructs are present.
"""

import sqlglot
import sqlparse
from sqlglot import exp

from repositories.sql_validators.ast_analyzer import normalize_function_name


class AllowedNodeTypesRule:
    """
    Allow only a whitelist of sqlglot AST node types.

    The rule parses the raw SQL with sqlglot (PostgreSQL dialect) and walks the
    expression tree. If any node class name is not present in ``allowed`` the
    query is rejected. This provides a strong guarantee that no unexpected
    constructs (e.g. COPY, CALL, procedural statements) can slip through.
    """

    def __init__(self, allowed: set[str]):
        # Normalise to class names without the ``Expression`` suffix.
        self.allowed = {name.lower() for name in allowed}

    def check(self, stmt: sqlparse.sql.Statement, raw: str) -> bool:  # noqa: D401
        """
        Return True if every AST node is in the allowed whitelist.

        Args:
            stmt: Ignored; the raw statement is re-parsed for a reliable AST.
            raw: The original query string.

        """
        try:
            parsed = sqlglot.parse_one(raw, read="postgres")
        except Exception:
            return False
        for node in parsed.walk():
            node_name = type(node).__name__.replace("Expression", "").lower()
            if node_name not in self.allowed:
                return False
        return True


class ForbiddenFunctionsRule:
    """
    Reject queries that reference disallowed functions.

    The rule looks for ``sqlglot.exp.Func`` nodes and checks the function
    name against a user-provided ``forbidden`` set (case-insensitive).
    """

    def __init__(self, forbidden: set[str]):
        self.forbidden = {name.lower() for name in forbidden}

    def check(self, stmt: sqlparse.sql.Statement, raw: str) -> bool:
        """Reject the statement when it references any forbidden function."""
        try:
            parsed = sqlglot.parse_one(raw, read="postgres")
        except Exception:
            return False
        for node in parsed.walk():
            if isinstance(node, exp.Func):
                func_name = normalize_function_name(node.name)
                if func_name in self.forbidden:
                    return False
        return True


class ForbiddenTableRule:
    """
    Block reads from system/catalog tables/schemas unless explicitly allowed.

    Any table whose schema (``database``/``schema``/``db``) or name starts with
    ``pg_`` or matches ``information_schema`` causes the rule to reject. The
    FULLY QUALIFIED name is inspected — not just the bare table name — so
    ``information_schema.tables`` and quoted/mixed-case ``"pg_catalog"."pg_class"``
    cannot evade the prefix check the way a bare-name probe would.
    """

    def __init__(self, forbidden_prefixes: set[str] | None = None):
        self.forbidden_prefixes = {p.lower() for p in (forbidden_prefixes or {"pg_", "information_schema"})}

    def check(self, stmt: sqlparse.sql.Statement, raw: str) -> bool:
        """Reject the statement when it reads from any forbidden table."""
        try:
            parsed = sqlglot.parse_one(raw, read="postgres")
        except Exception:
            return False
        for node in parsed.walk():
            if not isinstance(node, exp.Table):
                continue
            parts = [node.catalog, node.db, node.name]
            parts = [p.replace('"', "").replace("`", "").strip().lower() for p in parts if p]
            if not parts:
                continue
            qualified = ".".join(parts)
            unqualified = parts[-1]
            for prefix in self.forbidden_prefixes:
                if qualified.startswith(prefix) or unqualified.startswith(prefix):
                    return False
        return True
