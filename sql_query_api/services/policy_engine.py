"""Policy evaluation and SQL enforcement for governed read queries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from auth import Principal


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
    if select_match:
        select_clause = select_match.group(1)
        if "*" in select_clause:
            return {"*"}
        sql_words = {
            "as", "asc", "desc", "and", "or", "not", "null", "true", "false",
            "case", "when", "then", "else", "end", "distinct",
        }
        for identifier in re.findall(r"\b[a-zA-Z_][\w]*\b", select_clause):
            lowered = identifier.lower()
            if lowered not in sql_words and not re.search(rf"\b{re.escape(identifier)}\s*\(", select_clause):
                columns.add(lowered.split(".")[-1])
    return columns


def apply_row_restrictions(sql: str, restrictions: dict[str, str], principal: Principal) -> str:
    """Add immutable predicates to a SELECT without trusting user SQL."""
    if not restrictions:
        return sql
    predicates = []
    for column, subject_attribute in restrictions.items():
        if subject_attribute not in principal.attributes:
            raise PermissionError(f"Required subject attribute '{subject_attribute}' is absent.")
        value = principal.attributes[subject_attribute]
        if value is None:
            raise PermissionError(f"Required subject attribute '{subject_attribute}' is absent.")
        escaped = str(value).replace("'", "''")
        predicates.append(f"{column} = '{escaped}'")
    suffix = " AND ".join(predicates)
    match = re.search(r"\b(order\s+by|group\s+by|having|limit)\b", sql, re.IGNORECASE)
    if match:
        head, tail = sql[: match.start()], sql[match.start() :]
    else:
        head, tail = sql, ""
    conjunction = " AND " if re.search(r"\bwhere\b", head, re.IGNORECASE) else " WHERE "
    return f"{head.rstrip()}{conjunction}{suffix} {tail}".strip()


def mask_rows(rows: list[dict[str, Any]], columns: frozenset[str]) -> list[dict[str, Any]]:
    if not columns:
        return rows
    return [
        {key: (None if key.lower().split(".")[-1] in columns else value) for key, value in row.items()}
        for row in rows
    ]
