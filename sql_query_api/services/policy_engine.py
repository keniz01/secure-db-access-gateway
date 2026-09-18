"""Policy evaluation and SQL enforcement for governed read queries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from auth import Principal
from repositories.sql_validators.ast_analyzer import AstSqlAnalyzer

# SQLGlot dialect used for policy rewrites. Statements that reach the
# governed pipeline are already guaranteed to parse as a single Query node by
# the safety checker, so rewriting here is safe and fail-closed.
_DIALECT = "postgres"

_SQL_KEYWORDS = {
    "select",
    "from",
    "where",
    "group",
    "having",
    "order",
    "by",
    "limit",
    "offset",
    "join",
    "left",
    "right",
    "inner",
    "outer",
    "cross",
    "on",
    "as",
    "asc",
    "desc",
    "and",
    "or",
    "not",
    "null",
    "true",
    "false",
    "case",
    "when",
    "then",
    "else",
    "end",
    "distinct",
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "concat",
    "coalesce",
    "cast",
    "lower",
    "upper",
    "trim",
    "substring",
    "date",
    "time",
    "timestamp",
    "round",
    "abs",
    "length",
}


def _normalize_identifier(value: str | None) -> str:
    if value is None:
        return ""
    cleaned = str(value).strip().lower()
    cleaned = cleaned.strip("`\"[]")
    if "." in cleaned:
        cleaned = cleaned.rsplit(".", 1)[-1]
    return cleaned


def _split_sql_list(value: str) -> list[str]:
    items: list[str] = []
    buffer: list[str] = []
    depth = 0
    quote: str | None = None
    for char in value:
        if quote:
            buffer.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
            buffer.append(char)
            continue
        if char in "([":
            depth += 1
            buffer.append(char)
            continue
        if char in ")]":
            if depth > 0:
                depth -= 1
            buffer.append(char)
            continue
        if char == "," and depth == 0:
            item = "".join(buffer).strip()
            if item:
                items.append(item)
            buffer = []
            continue
        buffer.append(char)
    item = "".join(buffer).strip()
    if item:
        items.append(item)
    return items


def _column_candidates_from_expression(expression: str) -> set[str]:
    cleaned = expression.strip()
    if not cleaned:
        return set()
    candidates = set()
    identifier_pattern = re.compile(r"[A-Za-z_][\w$]*")
    for match in identifier_pattern.finditer(cleaned):
        identifier = match.group(0).lower()
        if identifier in _SQL_KEYWORDS:
            continue
        candidates.add(identifier)
    if not candidates:
        candidates.add(_normalize_identifier(cleaned))
    return candidates


def _select_alias_map(sql: str) -> dict[str, set[str]]:
    match = re.search(r"\bselect\s+(.*?)\s+\bfrom\b", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return {}

    select_clause = match.group(1)
    alias_map: dict[str, set[str]] = {}
    for item in _split_sql_list(select_clause):
        alias_match = re.search(r"\bAS\s+([A-Za-z_][\w$]*)\s*$", item, re.IGNORECASE)
        alias = alias_match.group(1).lower() if alias_match else None
        expression = item if alias is None else item[: alias_match.start()].rstrip()
        source_ids = _column_candidates_from_expression(expression)
        if alias:
            alias_map[_normalize_identifier(alias)] = source_ids
        else:
            last_column = _normalize_identifier(expression.rsplit(".", 1)[-1])
            if last_column:
                alias_map[last_column] = source_ids
    return alias_map


@dataclass(frozen=True, slots=True)
class Policy:
    """A server-owned read policy."""

    id: str
    effect: str = "allow"
    org_id: str | None = None
    principal_id: str | None = None
    roles: frozenset[str] = frozenset()
    database_id: str | None = None
    table: str | None = None
    columns: frozenset[str] = frozenset()
    masked_columns: frozenset[str] = frozenset()
    row_scope: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Policy:
        """Build a policy from its raw configured representation."""
        return cls(
            id=str(value.get("id") or value.get("name") or "unnamed"),
            effect=str(value.get("effect", "allow")).lower(),
            org_id=value.get("org_id") or value.get("organisation_id"),
            principal_id=value.get("principal_id") or value.get("user_id"),
            roles=frozenset(str(role).lower() for role in value.get("roles", []) or []),
            database_id=value.get("database_id"),
            table=(str(value["table"]).lower() if value.get("table") else None),
            columns=frozenset(str(column).lower() for column in value.get("columns", []) or []),
            masked_columns=frozenset(
                str(column).lower() for column in value.get("masked_columns", []) or []
            ),
            row_scope={
                str(column).lower(): str(subject_attribute)
                for column, subject_attribute in (value.get("row_scope") or {}).items()
            },
        )


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Explainable result of evaluating a request against policy."""

    allowed: bool
    reason: str
    policy_ids: tuple[str, ...] = ()
    row_restrictions: dict[str, str] = field(default_factory=dict)
    masked_columns: frozenset[str] = frozenset()

    def as_dict(self) -> dict[str, Any]:
        """Render the decision as a JSON-serializable mapping."""
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "policy_ids": list(self.policy_ids),
            "row_restrictions": dict(self.row_restrictions),
            "masked_columns": sorted(self.masked_columns),
        }


@dataclass(frozen=True, slots=True)
class EffectiveAccess:
    """
    Server-side view of the schema a principal may read for a logical database.

    Mirrors the query-path decision in ``PolicyEvaluator.evaluate`` exactly:
    a table is visible only when an allow policy matches it and no deny does,
    and a column is visible only when it satisfies the same whitelist union
    the evaluator enforces for column-gated policies.
    """

    accessible_tables: frozenset[str] = frozenset()
    allowed_columns: frozenset[str] = frozenset()
    masked_columns: frozenset[str] = frozenset()
    unrestricted: bool = False

    def table_accessible(self, table: str) -> bool:
        """Return whether the given table name may be surfaced to the principal."""
        if self.unrestricted:
            return True
        return _normalize_identifier(table) in self.accessible_tables

    def column_accessible(self, table: str, column: str) -> bool:
        """Return whether a column of a visible table may be surfaced."""
        if not self.table_accessible(table):
            return False
        if self.unrestricted or not self.allowed_columns:
            return True
        return _normalize_identifier(column) in self.allowed_columns

    def is_masked(self, column: str) -> bool:
        """Return whether the column is readable-but-masked for the principal."""
        return _normalize_identifier(column) in self.masked_columns


class PolicyEvaluator:
    """Evaluate policy documents with deny precedence and fail-closed matching."""

    def __init__(self, policies: list[Policy] | tuple[Policy, ...] = (), *, enabled: bool = True):
        self.policies = tuple(policies)
        self.enabled = enabled

    @classmethod
    def from_environment(cls) -> PolicyEvaluator:
        """Load policy configuration from the environment, failing fast in production."""
        import os

        from shared_secrets import is_environment_production, read_secret

        _production = is_environment_production()
        raw = os.getenv("POLICY_POLICIES_JSON", "").strip()
        if not raw:
            raw = read_secret(
                "POLICY_POLICIES_JSON",
                required=_production and not os.getenv("CI"),
                error_message=(
                    "Missing required POLICY_POLICIES_JSON environment variable or "
                    "POLICY_POLICIES_JSON_FILE secret for policy configuration."
                ),
            )
        if not raw:
            # In production we require an explicit policy configuration; otherwise fall back to an empty evaluator.
            if _production and not os.getenv("CI"):
                raise RuntimeError(
                    "Missing required POLICY_POLICIES_JSON environment variable or "
                    "POLICY_POLICIES_JSON_FILE secret for policy configuration."
                )
            # Non-production (e.g., CI, local dev) – disable policy enforcement.
            return cls(enabled=False)
        try:
            values = json.loads(raw)
            if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
                raise ValueError
            return cls([Policy.from_dict(item) for item in values])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Policy configuration is invalid.") from exc

    @staticmethod
    def _matches(policy: Policy, principal: Principal, database_id: str, table: str) -> bool:
        return (
            (policy.org_id is None or policy.org_id == principal.org_id)
            and (policy.principal_id is None or policy.principal_id == principal.user_id)
            and (not policy.roles or bool(policy.roles & principal.roles))
            and (policy.database_id is None or policy.database_id == database_id)
            and (policy.table is None or policy.table == table.lower())
        )

    def evaluate(
        self,
        principal: Principal,
        database_id: str,
        tables: list[str],
        *,
        referenced_columns: set[str] | None = None,
    ) -> PolicyDecision:
        """Evaluate a query against the configured policies with deny precedence."""
        if not self.enabled:
            return PolicyDecision(True, "Policy document not configured; legacy gateway controls apply.")
        if not tables:
            return PolicyDecision(False, "No protected table could be identified.")

        applicable = [
            policy
            for policy in self.policies
            if any(self._matches(policy, principal, database_id, table) for table in tables)
        ]
        denies = [policy for policy in applicable if policy.effect == "deny"]
        if denies:
            return PolicyDecision(False, "Denied by policy.", tuple(policy.id for policy in denies))

        table_policies = [
            policy for policy in applicable if policy.effect == "allow" and policy.table is not None
        ]
        if not table_policies:
            return PolicyDecision(False, "No allow policy matched the requested table.")

        allowed_tables = {policy.table for policy in table_policies}
        if any(table.lower() not in allowed_tables for table in tables):
            return PolicyDecision(False, "A requested table is not allowed by policy.")

        column_policies = [policy for policy in table_policies if policy.columns]
        if referenced_columns and column_policies:
            allowed_columns = set().union(*(policy.columns for policy in column_policies))
            if not referenced_columns.issubset(allowed_columns):
                return PolicyDecision(False, "A referenced column is not allowed by policy.")

        row_restrictions: dict[str, str] = {}
        masked: set[str] = set()
        for policy in table_policies:
            row_restrictions.update(policy.row_scope)
            masked.update(policy.masked_columns)
        for subject_attribute in row_restrictions.values():
            if subject_attribute not in principal.attributes or getattr(principal, subject_attribute, None) is None:
                return PolicyDecision(False, f"Required subject attribute '{subject_attribute}' is absent.")

        return PolicyDecision(
            True,
            "Allowed by policy.",
            tuple(policy.id for policy in table_policies),
            row_restrictions,
            frozenset(masked),
        )

    def effective_access(self, principal: Principal, database_id: str) -> EffectiveAccess:
        """
        Compute the schema surface a principal may read for a logical database.

        This is the display-side mirror of ``evaluate``: deny-documents remove
        tables, allow-documents grant them, and the column whitelist union is
        exactly the set that a governed query would be allowed to reference.
        When the evaluator is disabled every table and column is visible, so
        local development and non-policy configurations are unaffected.
        """
        if not self.enabled:
            return EffectiveAccess(unrestricted=True)

        deny_all = False
        denied_tables: set[str] = set()
        allow_by_table: dict[str, list[Policy]] = {}
        for policy in self.policies:
            if not self._matches(policy, principal, database_id, policy.table or ""):
                continue
            if policy.effect == "deny":
                if policy.table is None:
                    deny_all = True
                else:
                    denied_tables.add(policy.table)
            elif policy.table is not None:
                allow_by_table.setdefault(policy.table, []).append(policy)

        accessible = {
            table for table in allow_by_table if table not in denied_tables
        }
        if deny_all:
            accessible = set()

        column_policies = [
            policy
            for policies in allow_by_table.values()
            for policy in policies
            if policy.columns
        ]
        allowed_columns = (
            set().union(*(policy.columns for policy in column_policies))
            if column_policies
            else set()
        )
        masked_columns = {
            column
            for policies in allow_by_table.values()
            for policy in policies
            for column in policy.masked_columns
        }

        return EffectiveAccess(
            accessible_tables=frozenset(accessible),
            allowed_columns=frozenset(allowed_columns),
            masked_columns=frozenset(masked_columns),
        )


_ast_analyzer = AstSqlAnalyzer()
_TABLE_PATTERN = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)
_COLUMN_PATTERN = re.compile(r"\b([a-zA-Z_][\w]*)\s*(?:=|<>|!=|<|>|<=|>=|\bin\b|\blike\b)", re.IGNORECASE)


def tables_touched(sql: str) -> list[str]:
    """Extract tables touched using AST analyzer with regex fallback."""
    ast_tables = _ast_analyzer.extract_tables(sql)
    if ast_tables:
        return ast_tables
    return list(dict.fromkeys(match.split(".")[-1].lower() for match in _TABLE_PATTERN.findall(sql)))


def referenced_columns(sql: str) -> set[str]:
    """Extract referenced columns using AST analyzer with regex fallback."""
    ast_columns = _ast_analyzer.extract_referenced_columns(sql)
    if ast_columns:
        return ast_columns

    columns = {match.lower() for match in _COLUMN_PATTERN.findall(sql)}
    select_match = re.search(r"\bselect\s+(.*?)\s+\bfrom\b", sql, re.IGNORECASE | re.DOTALL)
    if not select_match:
        return columns

    select_clause = select_match.group(1)
    if "*" in select_clause:
        return {"*"}

    for item in _split_sql_list(select_clause):
        expression = item
        alias_match = re.search(r"\bAS\s+([A-Za-z_][\w$]*)\s*$", item, re.IGNORECASE)
        if alias_match:
            expression = item[: alias_match.start()].rstrip()
        for identifier in re.findall(r"\b[a-zA-Z_][\w]*\b", expression):
            lowered = identifier.lower()
            if lowered in _SQL_KEYWORDS:
                continue
            if re.search(rf"\b{re.escape(identifier)}\s*\(", expression):
                continue
            columns.add(lowered.split(".")[-1])
    return columns


def _row_scope_from_tables(select: exp.Select) -> dict[str, list[str]]:
    """
    Map the physical tables directly referenced by a SELECT's FROM/JOIN clauses.

    Returns ``{normalized_table_name: [qualifiers]}`` where each qualifier is
    the alias the query uses for that table (or the table name itself when no
    alias is given). Nested selects (derived tables, CTE bodies, subqueries,
    UNION branches) are handled separately because they each get their own
    row-scope predicate from the AST walk.
    """
    aliases: dict[str, list[str]] = {}

    def scan(node: exp.Table) -> None:
        if not isinstance(node, exp.Table):
            return
        name = (node.name or "").strip().lower()
        qualifier = node.alias_or_name
        if name and qualifier:
            aliases.setdefault(name, []).append(qualifier)

    from_ = select.args.get("from_")
    if from_ is not None and isinstance(from_.this, exp.Table):
        scan(from_.this)
    for join in select.args.get("joins") or []:
        if isinstance(join.this, exp.Table):
            scan(join.this)
    return aliases


def apply_row_restrictions(sql: str, restrictions: dict[str, str], principal: Principal) -> str:
    """
    Add immutable predicates to a SELECT without trusting user SQL.

    Rewrites the statement at the AST level so every branch, subquery, CTE
    body, and derived table that references the affected tables carries its
    own scope predicate. Unlike a trailing textual ``WHERE``, this cannot be
    detached from earlier UNION branches or nested reads.
    """
    if not restrictions:
        return sql

    resolved: dict[str, str] = {}
    for column, subject_attribute in restrictions.items():
        if subject_attribute not in principal.attributes:
            raise PermissionError(f"Required subject attribute '{subject_attribute}' is absent.")
        value = principal.attributes[subject_attribute]
        if value is None:
            raise PermissionError(f"Required subject attribute '{subject_attribute}' is absent.")
        resolved[str(column).lower()] = str(value)

    try:
        tree = sqlglot.parse_one(sql, read=_DIALECT)
    except Exception:
        raise ValueError("Row-restriction rewrite failed to parse statement.") from None
    if tree is None:
        raise ValueError("Row-restriction rewrite failed to parse statement.")

    for select in tree.find_all(exp.Select):
        qualifiers = {
            qualifier
            for table_aliases in _row_scope_from_tables(select).values()
            for qualifier in table_aliases
        }
        if not qualifiers:
            continue
        for column, value in resolved.items():
            for qualifier in sorted(qualifiers):
                predicate = exp.EQ(this=exp.column(column, qualifier), expression=exp.Literal.string(value))
                existing = select.args.get("where")
                merged = exp.and_(existing.this, predicate, dialect=_DIALECT) if existing else predicate
                select.set("where", exp.Where(this=merged))

    return tree.sql(dialect=_DIALECT)


def rewrite_masked_columns(sql: str, masked_columns: frozenset[str]) -> str:
    """
    Replace references to masked columns with NULL at the source of execution.

    Nulling the column before the database executes guarantees that values
    cannot leak through derivatives the result-name heuristics miss:
    aggregates (``sum``/``avg``/``count``), implicit aliases, scalar
    subqueries, and re-bound nested projections all compute over ``NULL``.
    """
    if not masked_columns:
        return sql
    masked = {str(column).lower() for column in masked_columns}
    try:
        tree = sqlglot.parse_one(sql, read=_DIALECT)
    except Exception:
        raise ValueError("Masking rewrite failed to parse statement.") from None
    if tree is None:
        raise ValueError("Masking rewrite failed to parse statement.")

    for column in tree.find_all(exp.Column):
        if (column.name or "").lower() not in masked:
            continue
        parent = column.parent
        # Preserve the result column header for bare projections
        # (SELECT salary -> SELECT NULL AS salary) so callers still see the
        # column name while the value is nulled.
        if isinstance(parent, exp.Select) and any(expr is column for expr in parent.expressions):
            column.replace(exp.alias_(exp.Null(), column.name, quoted=False))
        else:
            column.replace(exp.Null())

    return tree.sql(dialect=_DIALECT)


def mask_rows(rows: list[dict[str, Any]], columns: frozenset[str], sql: str | None = None) -> list[dict[str, Any]]:
    """Return rows with the given columns substituted by null values."""
    if not columns:
        return rows

    alias_map = _select_alias_map(sql) if sql else {}
    normalized_columns = {str(column).lower() for column in columns}

    def should_mask(key: str) -> bool:
        normalized = _normalize_identifier(key)
        if not normalized:
            return False
        if normalized in normalized_columns:
            return True
        return any(source in normalized_columns for source in alias_map.get(normalized, set()))

    return [
        {key: (None if should_mask(str(key)) else value) for key, value in row.items()}
        for row in rows
    ]
