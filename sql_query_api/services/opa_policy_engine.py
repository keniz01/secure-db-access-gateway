"""Open Policy Agent (OPA) integration for policy evaluation."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from auth import Principal
from config.app_logger import get_current_correlation_id
from metrics import (
    observe_opa_evaluation,
    record_opa_evaluation_failed,
)
from services.policy_engine import EffectiveAccess, PolicyDecision


class _InvalidOpaResponse(TypeError):
    """Raised when an OPA decision document has an unexpected shape."""


def _as_str_tuple(value: Any, field: str) -> tuple[str, ...]:
    """Coerce an OPA array field to a tuple of strings, or raise.

    Rejects strings outright: iterating a str yields characters, so a policy
    document returning ``"masked_columns": "ssn"`` would otherwise be silently
    interpreted as masking the columns ``s``, ``s``, ``n``.
    """
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise _InvalidOpaResponse(f"'{field}' must be an array, got {type(value).__name__}")
    return tuple(str(item) for item in value)


def _as_str_mapping(value: Any, field: str) -> dict[str, str]:
    """Coerce an OPA object field to a dict of strings, or raise."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _InvalidOpaResponse(f"'{field}' must be an object, got {type(value).__name__}")
    return {str(key): str(item) for key, item in value.items()}


@dataclass(frozen=True, slots=True)
class OpaConfig:
    """Configuration for the OPA sidecar connection."""

    url: str
    timeout: float = 5.0
    enabled: bool = True
    # mTLS configuration
    mtls_enabled: bool = False
    mtls_cert_path: str | None = None
    mtls_key_path: str | None = None
    mtls_ca_path: str | None = None
    mtls_verify: bool = True

    @classmethod
    def from_environment(cls) -> OpaConfig:
        """Load OPA configuration from environment variables.

        Preferred production path is OPA bundles (sql_query_api/opa/config.yaml
        + BUNDLE_SERVICE_URL). OPA_URL/OPA_ENABLED control the gateway->OPA
        data-plane; bundles are fetched by OPA itself, not the app.
        """
        from shared_secrets import is_ci, is_environment_production, read_secret

        raw_url = os.getenv("OPA_URL", "").strip()
        if not raw_url:
            raw_url = read_secret("OPA_URL", required=False) or ""

        raw_enabled = os.getenv("OPA_ENABLED", "").strip().lower()
        if not raw_enabled:
            raw_enabled = read_secret("OPA_ENABLED", required=False) or ""

        enabled = raw_enabled in ("true", "1", "yes")
        # Distinguish "operator never mentioned OPA" from "operator explicitly
        # turned it off". Only the former is a misconfiguration worth failing on.
        explicitly_disabled = raw_enabled in ("false", "0", "no", "off")
        production = is_environment_production()

        # mTLS configuration
        mtls_enabled = os.getenv("OPA_MTLS_ENABLED", "").strip().lower() in ("true", "1", "yes")
        mtls_cert_path = os.getenv("OPA_MTLS_CERT_PATH") or read_secret("OPA_MTLS_CERT_PATH", required=False)
        mtls_key_path = os.getenv("OPA_MTLS_KEY_PATH") or read_secret("OPA_MTLS_KEY_PATH", required=False)
        mtls_ca_path = os.getenv("OPA_MTLS_CA_PATH") or read_secret("OPA_MTLS_CA_PATH", required=False)
        mtls_verify = os.getenv("OPA_MTLS_VERIFY", "").strip().lower() not in ("false", "0", "no")

        if not raw_url:
            if production and not explicitly_disabled and not is_ci():
                # Fail closed at startup. Previously an empty OPA_URL silently
                # downgraded to the deprecated in-process evaluator regardless of
                # OPA_ENABLED - and that evaluator is ALLOW-ALL when no
                # POLICY_POLICIES_JSON is set, so a misconfigured production host
                # would serve ALLOW-ALL while /readyz still reported healthy.
                #
                # To legitimately run the deprecated inline evaluator, the operator
                # must say so with OPA_ENABLED=false. An undeclared OPA_URL
                # absence in production is a misconfiguration, not an opt-out.
                raise RuntimeError(
                    "OPA_URL is required when ENVIRONMENT=production. Refusing to "
                    "fall back to the deprecated in-process policy evaluator, which "
                    "cannot enforce RBAC1 hierarchy or separation-of-duties. Set "
                    "OPA_URL (e.g. http://opa:8181) with OPA_ENABLED=true, or set "
                    "OPA_ENABLED=false explicitly to opt out."
                )
            if production and explicitly_disabled:
                logger.warning(
                    "OPA_ENABLED=false in production: using the deprecated "
                    "in-process policy evaluator. It does NOT implement RBAC1 "
                    "hierarchy or separation-of-duties."
                )
            return cls(url="", timeout=5.0, enabled=False, mtls_enabled=mtls_enabled)

        # Log bundle hint in production so operators migrate off inline JSON
        if production and os.getenv("POLICY_POLICIES_JSON"):
            logger.warning(
                "POLICY_POLICIES_JSON is deprecated; policies should be served via "
                "OPA bundle (sql_query_api/opa/config.yaml + BUNDLE_SERVICE_URL)."
            )

        return cls(
            url=raw_url.rstrip("/"),
            timeout=5.0,
            enabled=enabled,
            mtls_enabled=mtls_enabled,
            mtls_cert_path=mtls_cert_path,
            mtls_key_path=mtls_key_path,
            mtls_ca_path=mtls_ca_path,
            mtls_verify=mtls_verify,
        )


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

    @property
    def is_enabled(self) -> bool:
        """Whether this evaluator will actually consult OPA.

        When False, evaluate() denies every request (fail-closed).
        """
        return self._enabled

    @classmethod
    def from_environment(cls) -> OpaPolicyEvaluator:
        """Create an evaluator from environment configuration."""
        config = OpaConfig.from_environment()
        return cls(config=config)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client for OPA communication with optional mTLS."""
        if self._client is None or self._client.is_closed:
            client_kwargs = {
                "base_url": self._config.url,
                "timeout": self._config.timeout,
                "headers": {"Content-Type": "application/json"},
            }
            if self._config.mtls_enabled:
                if not self._config.mtls_cert_path or not self._config.mtls_key_path:
                    raise RuntimeError("OPA mTLS enabled but cert/key paths not configured")
                client_kwargs["cert"] = (self._config.mtls_cert_path, self._config.mtls_key_path)
                if self._config.mtls_ca_path:
                    client_kwargs["verify"] = self._config.mtls_ca_path
                else:
                    client_kwargs["verify"] = self._config.mtls_verify
            self._client = httpx.AsyncClient(**client_kwargs)
        return self._client

    def _get_correlation_header(self) -> dict[str, str]:
        """Get correlation ID header for OPA requests."""
        cid = get_current_correlation_id()
        if cid and cid != "N/A":
            return {"X-Correlation-ID": cid}
        return {}

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _query_opa(
        self,
        path: str,
        input_doc: dict[str, Any],
        org_id: str,
        database_id: str,
    ) -> dict[str, Any] | None:
        """Send a query to OPA and return the result, or None on failure."""
        started = time.perf_counter()
        try:
            client = await self._get_client()
            headers = {"Content-Type": "application/json"}
            headers.update(self._get_correlation_header())
            response = await client.post(
                f"/v1/data{path}",
                json={"input": input_doc},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
            observe_opa_evaluation(org_id, database_id, time.perf_counter() - started)
            return data.get("result")
        except httpx.TimeoutException:
            logger.error("OPA query timed out for path: {}", path)
            record_opa_evaluation_failed(org_id, database_id)
            return None
        except httpx.HTTPStatusError as exc:
            logger.error("OPA returned HTTP {}: {}", exc.response.status_code, exc.response.text)
            record_opa_evaluation_failed(org_id, database_id)
            return None
        except Exception:
            logger.exception("OPA query failed for path: {}", path)
            record_opa_evaluation_failed(org_id, database_id)
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

        result = await self._query_opa("/gateway/evaluate", input_doc, principal.org_id, database_id)

        if result is None:
            # Fail closed: OPA unreachable or returned invalid response
            return PolicyDecision(
                False,
                "OPA policy evaluation failed (unreachable or invalid response).",
            )

        if not isinstance(result, dict):
            # Fail closed: a valid-JSON non-object (bool/list/str) has no .get,
            # and AttributeError is not caught by the handler below.
            logger.error("Invalid OPA response structure (not an object): type={}", type(result).__name__)
            record_opa_evaluation_failed(principal.org_id, database_id)
            return PolicyDecision(False, "Invalid OPA response structure.")

        try:
            # Strict boolean: anything other than the literal JSON `true` denies.
            # A previous `bool(result.get("allowed", False))` allowed a truthy
            # string such as "false" or "no" through as an ALLOW decision.
            allowed = result.get("allowed") is True
            reason = str(result.get("reason", "No reason provided."))
            policy_ids = _as_str_tuple(result.get("policy_ids", []), "policy_ids")
            row_restrictions = _as_str_mapping(result.get("row_restrictions", {}), "row_restrictions")
            masked_columns = _as_str_tuple(result.get("masked_columns", []), "masked_columns")

            if not allowed:
                logger.debug("OPA denied request: {}", reason)

            return PolicyDecision(
                allowed=allowed,
                reason=reason,
                policy_ids=policy_ids,
                row_restrictions=row_restrictions,
                masked_columns=frozenset(masked_columns),
            )
        except _InvalidOpaResponse as exc:
            logger.error("Invalid OPA response structure: {}", exc)
            record_opa_evaluation_failed(principal.org_id, database_id)
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

        result = await self._query_opa("/gateway/effective_access", input_doc, principal.org_id, database_id)

        if result is None:
            # Fail closed
            return EffectiveAccess(
                accessible_tables=frozenset(),
                allowed_columns=frozenset(),
                masked_columns=frozenset(),
            )

        if not isinstance(result, dict):
            logger.error(
                "Invalid OPA effective_access response (not an object): type={}",
                type(result).__name__,
            )
            record_opa_evaluation_failed(principal.org_id, database_id)
            return EffectiveAccess(
                accessible_tables=frozenset(),
                allowed_columns=frozenset(),
                masked_columns=frozenset(),
            )

        try:
            return EffectiveAccess(
                accessible_tables=frozenset(
                    _as_str_tuple(result.get("accessible_tables", []), "accessible_tables")
                ),
                allowed_columns=frozenset(
                    _as_str_tuple(result.get("allowed_columns", []), "allowed_columns")
                ),
                masked_columns=frozenset(
                    _as_str_tuple(result.get("masked_columns", []), "masked_columns")
                ),
            )
        except _InvalidOpaResponse as exc:
            logger.error("Invalid OPA effective_access response: {}", exc)
            record_opa_evaluation_failed(principal.org_id, database_id)
            return EffectiveAccess(
                accessible_tables=frozenset(),
                allowed_columns=frozenset(),
                masked_columns=frozenset(),
            )
