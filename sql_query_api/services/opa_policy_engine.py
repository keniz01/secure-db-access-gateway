"""Open Policy Agent (OPA) integration for policy evaluation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from auth import Principal
from services.policy_engine import EffectiveAccess, PolicyDecision


@dataclass(frozen=True, slots=True)
class OpaConfig:
    """Configuration for the OPA sidecar connection."""

    url: str
    timeout: float = 5.0
    enabled: bool = True

    @classmethod
    def from_environment(cls) -> OpaConfig:
        """Load OPA configuration from environment variables."""
        from shared_secrets import is_environment_production, read_secret

        raw_url = os.getenv("OPA_URL", "").strip()
        if not raw_url:
            raw_url = read_secret("OPA_URL", required=False) or ""

        raw_enabled = os.getenv("OPA_ENABLED", "").strip().lower()
        if not raw_enabled:
            raw_enabled = read_secret("OPA_ENABLED", required=False) or "true"

        enabled = raw_enabled in ("true", "1", "yes")
        production = is_environment_production()

        if not raw_url:
            if production and not os.getenv("CI"):
                logger.warning(
                    "OPA_URL is not set in production. "
                    "Falling back to in-process policy evaluator. "
                    "Set OPA_URL to enable OPA-based policy enforcement."
                )
            return cls(url="", timeout=5.0, enabled=False)

        return cls(url=raw_url.rstrip("/"), timeout=5.0, enabled=enabled)


class OpaPolicyEvaluator:
    """Evaluate policies against an OPA sidecar.

    This evaluator implements the same interface as PolicyEvaluator but
    delegates policy decisions to an OPA sidecar over HTTP. The OPA sidecar
    is expected to expose a data path (e.g., ``/v1/data/gateway/evaluate``)
    that accepts a structured input and returns a boolean allow/deny decision
    with metadata.

    The OPA data path must implement the following contract:

    **Input document:**

    .. code-block:: json

        {
            "principal": {
                "user_id": "...",
                "org_id": "...",
                "roles": ["viewer"]
            },
            "database_id": "default",
            "tables": ["album"],
            "referenced_columns": ["album_id", "title"]
        }

    **Expected output (``result`` field):**

    .. code-block:: json

        {
            "allowed": true,
            "reason": "Allowed by policy.",
            "policy_ids": ["allow-album"],
            "row_restrictions": {"region": "region"},
            "masked_columns": ["total"]
        }

    If the OPA sidecar is unreachable or returns an unexpected response, the
    evaluator **fails closed** and denies the request.
    """

    def __init__(self, config: OpaConfig | None = None, *, enabled: bool = True):
        self._config = config or OpaConfig.from_environment()
        self._enabled = enabled and self._config.enabled and self._config.url != ""
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def from_environment(cls) -> OpaPolicyEvaluator:
        """Create an evaluator from environment configuration."""
        config = OpaConfig.from_environment()
        return cls(config=config)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client for OPA communication."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._config.url,
                timeout=self._config.timeout,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _query_opa(
        self,
        path: str,
        input_doc: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Send a query to OPA and return the result, or None on failure."""
        try:
            client = await self._get_client()
            response = await client.post(
                f"/v1/data{path}",
                json={"input": input_doc},
            )
            response.raise_for_status()
            data = response.json()
            return data.get("result")
        except httpx.TimeoutException:
            logger.error("OPA query timed out for path: {}", path)
            return None
        except httpx.HTTPStatusError as exc:
            logger.error("OPA returned HTTP {}: {}", exc.response.status_code, exc.response.text)
            return None
        except Exception:
            logger.exception("OPA query failed for path: {}", path)
            return None

    async def evaluate(
        self,
        principal: Principal,
        database_id: str,
        tables: list[str],
        *,
        referenced_columns: set[str] | None = None,
    ) -> PolicyDecision:
        """Evaluate a query against OPA policies with deny precedence."""
        if not self._enabled:
            # Fail closed: disabled evaluator denies all requests
            return PolicyDecision(False, "OPA is disabled.")
        if not tables:
            return PolicyDecision(False, "No protected table could be identified.")

        input_doc = {
            "principal": {
                "user_id": principal.user_id,
                "org_id": principal.org_id,
                "roles": sorted(principal.roles),
            },
            "database_id": database_id,
            "tables": tables,
            "referenced_columns": sorted(referenced_columns) if referenced_columns else [],
        }

        result = await self._query_opa("/gateway/evaluate", input_doc)

        if result is None:
            # Fail closed: OPA unreachable or returned invalid response
            return PolicyDecision(
                False,
                "OPA policy evaluation failed (unreachable or invalid response).",
            )

        try:
            allowed = bool(result.get("allowed", False))
            reason = str(result.get("reason", "No reason provided."))
            policy_ids = tuple(str(pid) for pid in result.get("policy_ids", []))
            row_restrictions = dict(result.get("row_restrictions", {}))
            masked_columns = frozenset(
                str(col) for col in result.get("masked_columns", [])
            )

            return PolicyDecision(
                allowed=allowed,
                reason=reason,
                policy_ids=policy_ids,
                row_restrictions=row_restrictions,
                masked_columns=masked_columns,
            )
        except (TypeError, ValueError):
            logger.error("Invalid OPA response structure: {}", result)
            return PolicyDecision(False, "Invalid OPA response structure.")

    async def effective_access(
        self,
        principal: Principal,
        database_id: str,
    ) -> EffectiveAccess:
        """Compute the schema surface a principal may read via OPA."""
        if not self._enabled:
            # Fail closed: disabled evaluator returns empty access
            return EffectiveAccess(
                accessible_tables=frozenset(),
                allowed_columns=frozenset(),
                masked_columns=frozenset(),
            )

        input_doc = {
            "principal": {
                "user_id": principal.user_id,
                "org_id": principal.org_id,
                "roles": sorted(principal.roles),
            },
            "database_id": database_id,
            "query_type": "effective_access",
        }

        result = await self._query_opa("/gateway/effective_access", input_doc)

        if result is None:
            # Fail closed
            return EffectiveAccess(
                accessible_tables=frozenset(),
                allowed_columns=frozenset(),
                masked_columns=frozenset(),
            )

        try:
            return EffectiveAccess(
                accessible_tables=frozenset(result.get("accessible_tables", [])),
                allowed_columns=frozenset(result.get("allowed_columns", [])),
                masked_columns=frozenset(result.get("masked_columns", [])),
            )
        except (TypeError, ValueError):
            logger.error("Invalid OPA effective_access response: {}", result)
            return EffectiveAccess(
                accessible_tables=frozenset(),
                allowed_columns=frozenset(),
                masked_columns=frozenset(),
            )
