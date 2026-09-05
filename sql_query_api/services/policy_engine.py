"""Policy evaluation and SQL enforcement for governed read queries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from auth import Principal

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
        lower_item = item.lower()
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
    def from_dict(cls, value: dict[str, Any]) -> "Policy":
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
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "policy_ids": list(self.policy_ids),
            "row_restrictions": dict(self.row_restrictions),
            "masked_columns": sorted(self.masked_columns),
        }


class PolicyEvaluator:
    """Evaluate policy documents with deny precedence and fail-closed matching."""

    def __init__(self, policies: list[Policy] | tuple[Policy, ...] = (), *, enabled: bool = True):
        self.policies = tuple(policies)
        self.enabled = enabled

    @classmethod
    def from_environment(cls) -> "PolicyEvaluator":
        import os

        raw = os.getenv("POLICY_POLICIES_JSON", "").strip()
        if not raw:
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
        for column, subject_attribute in row_restrictions.items():
            if subject_attribute not in principal.attributes or getattr(principal, subject_attribute, None) is None:
                return PolicyDecision(False, f"Required subject attribute '{subject_attribute}' is absent.")

        return PolicyDecision(
            True,
            "Allowed by policy.",
            tuple(policy.id for policy in table_policies),
            row_restrictions,
            frozenset(masked),
        )


_TABLE_PATTERN = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)
_COLUMN_PATTERN = re.compile(r"\b([a-zA-Z_][\w]*)\s*(?:=|<>|!=|<|>|<=|>=|\bin\b|\blike\b)", re.IGNORECASE)


def tables_touched(sql: str) -> list[str]:
    return list(dict.fromkeys(match.split(".")[-1].lower() for match in _TABLE_PATTERN.findall(sql)))


def referenced_columns(sql: str) -> set[str]:
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


def apply_row_restrictions(sql: str, restrictions: dict[str, str], principal: Principal) -> str:
    """Add immutable predicates to a SELECT without trusting user SQL."""
    if not restrictions:
        return sql

    table_aliases = []
    for match in re.finditer(
        r"\b(?:from|join)\s+(?:only\s+)?(?P<table>(?:[A-Za-z_][\w]*\.)?[A-Za-z_][\w]*)(?:\s+(?:as\s+)?(?P<alias>[A-Za-z_][\w]*))?",
        sql,
        re.IGNORECASE,
    ):
        alias = match.group("alias")
        table_name = match.group("table")
        identifier = alias or table_name.rsplit(".", 1)[-1]
        if identifier:
            table_aliases.append(identifier.lower())

    predicates: list[str] = []
    for column, subject_attribute in restrictions.items():
        if subject_attribute not in principal.attributes:
            raise PermissionError(f"Required subject attribute '{subject_attribute}' is absent.")
        value = principal.attributes[subject_attribute]
        if value is None:
            raise PermissionError(f"Required subject attribute '{subject_attribute}' is absent.")
        escaped = str(value).replace("'", "''")
        targets = [column]
        if table_aliases:
            targets = [f"{alias}.{column}" for alias in table_aliases]
        predicates.extend(f"{target} = '{escaped}'" for target in targets)

    suffix = " AND ".join(predicates)
    match = re.search(r"\b(order\s+by|group\s+by|having|limit)\b", sql, re.IGNORECASE)
    if match:
        head, tail = sql[: match.start()], sql[match.start() :]
    else:
        head, tail = sql, ""
    conjunction = " AND " if re.search(r"\bwhere\b", head, re.IGNORECASE) else " WHERE "
    return f"{head.rstrip()}{conjunction}{suffix} {tail}".strip()


def mask_rows(rows: list[dict[str, Any]], columns: frozenset[str], sql: str | None = None) -> list[dict[str, Any]]:
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
