"""The single governed query execution pipeline used by all access modes."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, Union

from auth import Principal
from config.app_logger import log_audit_event
from metrics import (
    observe_query,
    record_auth_failed,
    record_policy_denied,
    record_query_rejected_overload,
    record_query_validation_failed,
    set_tenant_quota_utilization,
    set_active_tenant_connections,
)
from repositories.sql_validators.sql_safety_checker import SqlSafetyChecker
from services.abstract_sql_query_service import ISqlQueryService
from services.policy_engine import (
    PolicyDecision,
    PolicyEvaluator,
    apply_row_restrictions,
    mask_rows,
    referenced_columns,
    rewrite_masked_columns,
    tables_touched,
)
from services.tenant_database_resolver import TenantDatabaseConfig


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


@dataclass(slots=True)
class TenantQuota:
    """Per-tenant concurrency quota."""
    semaphore: asyncio.Semaphore
    max_concurrent: int
    queue_limit: int
    current_queued: int = 0


class GovernedQueryGateway:
    """Authenticate, resolve, validate, control, execute, and audit a query."""

    def __init__(
        self,
        provider: QueryServiceProvider | Callable[[], QueryServiceProvider],
        safety_checker: SqlSafetyChecker,
        audit: Callable[..., None] = log_audit_event,
        policy_evaluator: Union[PolicyEvaluator, Any, None] = None,
        *,
        tenant_concurrent_limit: int | None = None,
        tenant_queue_limit: int | None = None,
    ) -> None:
        self._provider = provider
        self._safety_checker = safety_checker
        self._audit = audit
        self._policy_evaluator = policy_evaluator or PolicyEvaluator(enabled=False)
        # Per-tenant concurrency control
        self._tenant_concurrent_limit = tenant_concurrent_limit or int(os.getenv("TENANT_CONCURRENT_LIMIT", "5"))
        self._tenant_queue_limit = tenant_queue_limit or int(os.getenv("TENANT_QUEUE_LIMIT", "10"))
        self._tenant_quotas: dict[tuple[str, str], TenantQuota] = {}
        self._quotas_lock = asyncio.Lock()

    def _get_tenant_quota(self, org_id: str, database_id: str) -> TenantQuota:
        """Get or create quota for a tenant/database pair."""
        key = (org_id, database_id)
        if key not in self._tenant_quotas:
            self._tenant_quotas[key] = TenantQuota(
                semaphore=asyncio.Semaphore(self._tenant_concurrent_limit),
                max_concurrent=self._tenant_concurrent_limit,
                queue_limit=self._tenant_queue_limit,
            )
        return self._tenant_quotas[key]

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

        # Per-tenant concurrency control
        quota = self._get_tenant_quota(request.principal.org_id, binding.database_id)
        async with self._quotas_lock:
            if quota.current_queued >= quota.queue_limit:
                self._audit("query_rejected_overload", user=request.principal.email,
                            org_id=request.principal.org_id, database_id=binding.database_id,
                            reason="Tenant query queue limit exceeded", queue_limit=quota.queue_limit)
                record_query_rejected_overload(request.principal.org_id, binding.database_id)
                raise PermissionError(f"Tenant query queue full (limit: {quota.queue_limit}). Please retry later.")
            quota.current_queued += 1
            # Update quota utilization metrics
            set_tenant_quota_utilization(request.principal.org_id, binding.database_id, "queued", quota.current_queued / quota.queue_limit)
            set_active_tenant_connections(request.principal.org_id, binding.database_id, quota.current_queued)

        # Acquire semaphore with timeout to prevent indefinite blocking
        semaphore_acquired = False
        try:
            # Wait for semaphore with a reasonable timeout (e.g., 30 seconds)
            try:
                await asyncio.wait_for(quota.semaphore.acquire(), timeout=30.0)
                semaphore_acquired = True
                # Update active connections metric
                set_active_tenant_connections(request.principal.org_id, binding.database_id, 
                                             quota.max_concurrent - quota.semaphore._value)
            except asyncio.TimeoutError:
                self._audit("query_rejected_overload", user=request.principal.email,
                            org_id=request.principal.org_id, database_id=binding.database_id,
                            reason="Tenant concurrency limit timeout", concurrent_limit=quota.max_concurrent)
                record_query_rejected_overload(request.principal.org_id, binding.database_id)
                raise PermissionError(f"Tenant concurrency limit busy (limit: {quota.max_concurrent}). Please retry later.")

            cleaned_sql = self._safety_checker.clean_and_validate_sql(request.sql)
            decision = await self.evaluate(request, cleaned_sql)
            if not decision.allowed:
                self._audit("policy_denied", user=request.principal.email, org_id=request.principal.org_id,
                            database_id=binding.database_id, reason=decision.reason,
                            policy_ids=list(decision.policy_ids))
                record_policy_denied(request.principal.org_id, decision.reason)
                raise PermissionError(decision.reason)

            # Row scoping is injected per-SELECT at the AST level so every branch,
            # subquery, and CTE that reads the affected tables is constrained.
            restricted_sql = apply_row_restrictions(
                cleaned_sql, decision.row_restrictions, request.principal
            )
            # Masked columns are nulled before execution so their values (and any
            # derivative computed from them) can never reach the result rows.
            execution_sql = rewrite_masked_columns(restricted_sql, decision.masked_columns)
            started_at = time.perf_counter()
            query_hash = hashlib.sha256(execution_sql.encode("utf-8")).hexdigest()
            audit_payload: dict[str, Any] = {
                "user": request.principal.email,
                "org_id": request.principal.org_id,
                "database_id": binding.database_id,
                "database_target": getattr(service.repository, "database_target", "primary"),
                "query_hash": query_hash,
                "tables_touched": tables_touched(execution_sql),
            }
            if os.getenv("AUDIT_LOG_RAW_SQL", "").strip().lower() in {"true", "1", "yes"}:
                audit_payload["query"] = execution_sql

            self._audit(
                "sql_query",
                **audit_payload,
            )
            result = await service.execute_sql_statement(execution_sql, request.params)
            observe_query(
                org_id=request.principal.org_id,
                row_count=len(result),
                duration_seconds=time.perf_counter() - started_at,
            )
            # Name-based post-masking remains as defense-in-depth for star
            # projections (SELECT *), which the AST rewrite cannot expand. The
            # pre-rewrite statement is used for alias tracing so derived values
            # are still nulled even when the source column was already removed.
            return mask_rows(result, decision.masked_columns, restricted_sql)

        finally:
            if semaphore_acquired:
                quota.semaphore.release()
                # Update metrics on release
                set_active_tenant_connections(request.principal.org_id, binding.database_id,
                                             quota.max_concurrent - quota.semaphore._value)
            async with self._quotas_lock:
                quota.current_queued = max(0, quota.current_queued - 1)
                set_tenant_quota_utilization(request.principal.org_id, binding.database_id, "queued", 
                                             quota.current_queued / quota.queue_limit)

    async def evaluate(self, request: GovernedQueryRequest, sql: str | None = None) -> PolicyDecision:
        """Evaluate the policy decision for a request against the given statement."""
        statement = sql or request.sql
        # Support both sync (PolicyEvaluator) and async (OpaPolicyEvaluator) evaluators
        result = self._policy_evaluator.evaluate(
            request.principal,
            request.database_id,
            tables_touched(statement),
            referenced_columns=referenced_columns(statement),
        )
        # Handle async evaluators (e.g., OpaPolicyEvaluator)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def simulate(self, request: GovernedQueryRequest) -> PolicyDecision:
        """Evaluate policy without resolving a database or reading protected data."""
        return await self.evaluate(request)
