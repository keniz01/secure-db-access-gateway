"""The single governed query execution pipeline used by all access modes."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from auth import Principal
from config.app_logger import log_audit_event
from metrics import observe_query
from repositories.sql_validators.sql_safety_checker import SqlSafetyChecker
from services.abstract_sql_query_service import ISqlQueryService
from services.tenant_database_resolver import TenantDatabaseConfig
from services.policy_engine import (
    PolicyDecision,
    PolicyEvaluator,
    apply_row_restrictions,
    mask_rows,
    referenced_columns,
    tables_touched,
)


class QueryServiceProvider(Protocol):
    """Resolve a trusted principal and logical database to a query service."""

    def resolve(
        self, principal: Principal, database_id: str | None
    ) -> tuple[TenantDatabaseConfig, ISqlQueryService]:
        """Return the server-owned binding and service for a request."""
        ...


@dataclass(frozen=True, slots=True)
class GovernedQueryRequest:
    """Input contract for every governed SQL execution."""

    principal: Principal
    database_id: str
    sql: str
    params: dict[str, Any] | None = None


def _tables_touched(sql: str) -> list[str]:
    matches = re.findall(
        r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_.]*)",
        sql,
        flags=re.IGNORECASE,
    )
    return list(dict.fromkeys(match.split(".")[-1] for match in matches))


class GovernedQueryGateway:
    """Authenticate, resolve, validate, control, execute, and audit a query."""

    def __init__(
        self,
        provider: QueryServiceProvider | Callable[[], QueryServiceProvider],
        safety_checker: SqlSafetyChecker,
        audit: Callable[..., None] = log_audit_event,
        policy_evaluator: PolicyEvaluator | None = None,
    ) -> None:
        self._provider = provider
        self._safety_checker = safety_checker
        self._audit = audit
        self._policy_evaluator = policy_evaluator or PolicyEvaluator(enabled=False)

    def _provider_instance(self) -> QueryServiceProvider:
        if hasattr(self._provider, "resolve"):
            return self._provider
        return self._provider()

    async def execute(self, request: GovernedQueryRequest) -> list[dict[str, Any]]:
        """Run the complete governed query pipeline and return filtered rows."""
        if not isinstance(request.principal, Principal):
            raise PermissionError("Authenticated principal is required.")
        if not request.sql or not request.sql.strip():
            raise ValueError("SQL statement cannot be empty.")

        binding, service = self._provider_instance().resolve(
            request.principal, request.database_id
        )
        cleaned_sql = self._safety_checker.clean_and_validate_sql(request.sql)
        decision = self.evaluate(request, cleaned_sql)
        if not decision.allowed:
            self._audit("policy_denied", user=request.principal.email, org_id=request.principal.org_id,
                        database_id=binding.database_id, reason=decision.reason,
                        policy_ids=list(decision.policy_ids))
            raise PermissionError(decision.reason)
        cleaned_sql = apply_row_restrictions(cleaned_sql, decision.row_restrictions, request.principal)
        started_at = time.perf_counter()
        self._audit(
            "sql_query",
            user=request.principal.email,
            org_id=request.principal.org_id,
            database_id=binding.database_id,
            database_target=getattr(service.repository, "database_target", "primary"),
            query=cleaned_sql,
            tables_touched=_tables_touched(cleaned_sql),
        )
        result = await service.execute_sql_statement(cleaned_sql, request.params)
        observe_query(
            org_id=request.principal.org_id,
            row_count=len(result),
            duration_seconds=time.perf_counter() - started_at,
        )
        return mask_rows(result, decision.masked_columns, cleaned_sql)

    def evaluate(self, request: GovernedQueryRequest, sql: str | None = None) -> PolicyDecision:
        statement = sql or request.sql
        return self._policy_evaluator.evaluate(
            request.principal,
            request.database_id,
            tables_touched(statement),
            referenced_columns=referenced_columns(statement),
        )

    def simulate(self, request: GovernedQueryRequest) -> PolicyDecision:
        """Evaluate policy without resolving a database or reading protected data."""
        return self.evaluate(request)
