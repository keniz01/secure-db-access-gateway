"""Regression tests for the Track A fail-closed hardening.

Each test here pins a specific authorization bypass that existed before this
work. They are written to fail against the old code, so a future refactor that
reintroduces a permissive default will be caught rather than silently shipped.

Covered invariants:

* A1 - ``is_ci()`` parses CI variables strictly, and no production fail-fast is
      gated on a bare ``os.getenv("CI")`` truthiness check.
* A2 - production with no ``OPA_URL`` and no explicit opt-out refuses to start
      rather than silently downgrading to the deprecated in-process evaluator.
* A3 - an OPA decision document is validated strictly. A truthy string
      (``"allowed": "false"``) must not be coerced to ALLOW, and a
      valid-JSON non-object must deny rather than raise.
* A4 - ``GovernedQueryGateway`` has no default policy evaluator; omitting one is
      a ``TypeError``, not a silent ALLOW-ALL.
* A5 - ``/readyz`` reports 503 in production when the evaluator would ALLOW
      everything.
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from auth import Principal
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker
from services.opa_policy_engine import OpaConfig, OpaPolicyEvaluator
from services.policy_engine import Policy, PolicyEvaluator
from services.query_gateway import GovernedQueryGateway
from shared_secrets import MissingSecretError

REPO_SQL_API_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _principal(roles: frozenset[str] | None = None) -> Principal:
    return Principal(
        user_id="user-1",
        email="user@example.com",
        org_id="org-42",
        roles=roles or frozenset({"viewer"}),
        attributes={},
    )


def _opa_evaluator_returning(result: object) -> OpaPolicyEvaluator:
    """Build an enabled evaluator whose single POST returns ``result``."""
    evaluator = OpaPolicyEvaluator(config=OpaConfig(url="http://opa:8181", enabled=True))
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": result}
    mock_response.raise_for_status = MagicMock()
    client = AsyncMock()
    client.post = AsyncMock(return_value=mock_response)
    client.is_closed = False
    evaluator._client = client
    return evaluator


# ---------------------------------------------------------------------------
# A1 - strict CI detection
# ---------------------------------------------------------------------------


class TestIsCiIsStrict:
    """A1: `CI=false` must not be read as truthy.

    The old gates were `not os.getenv("CI")`. `os.getenv` returns a string, so any
    non-empty value - including "false", "0", "no" - evaluated truthy and
    disabled three production fail-fasts.
    """

    @pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes", "YES"])
    def test_truthy_spellings_detected(self, value: str) -> None:
        from shared_secrets import is_ci

        with patch.dict("os.environ", {"CI": value}, clear=True):
            assert is_ci() is True

    @pytest.mark.parametrize("value", ["false", "False", "FALSE", "0", "no", "off", "", " ", "maybe"])
    def test_non_truthy_spellings_rejected(self, value: str) -> None:
        from shared_secrets import is_ci

        with patch.dict("os.environ", {"CI": value}, clear=True):
            assert is_ci() is False

    def test_absent_ci_is_not_ci(self) -> None:
        from shared_secrets import is_ci

        with patch.dict("os.environ", {}, clear=True):
            assert is_ci() is False

    def test_github_actions_env_detected(self) -> None:
        from shared_secrets import is_ci

        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}, clear=True):
            assert is_ci() is True

    def test_github_actions_false_does_not_fool_ci(self) -> None:
        from shared_secrets import is_ci

        with patch.dict("os.environ", {"GITHUB_ACTIONS": "false", "CI": ""}, clear=True):
            assert is_ci() is False

    def test_ci_false_does_not_disable_production_policy_gate(self) -> None:
        """The headline A1 regression: CI=false must not unlock the fail-fast.

        Asserted on the *outcome* rather than a specific exception, so the test
        stays valid regardless of which of the two production gates (the
        `read_secret(required=...)` call or the explicit `RuntimeError`) fires.
        The invariant is: production + no policy document must never yield an
        ALLOW-ALL evaluator, whatever CI happens to be set to.
        """
        import os

        for ci_value in ("false", "0", "no", "off", "", "garbage"):
            env = {"ENVIRONMENT": "production", "CI": ci_value}
            with patch.dict("os.environ", env, clear=False):
                for key in ("POLICY_POLICIES_JSON", "POLICY_POLICIES_JSON_FILE"):
                    os.environ.pop(key, None)
                try:
                    evaluator = PolicyEvaluator.from_environment()
                except MissingSecretError:
                    continue  # refused to start - correct
                except RuntimeError as exc:
                    assert "POLICY_POLICIES_JSON" in str(exc)
                    continue
                # Startup was allowed to proceed. That is only acceptable if the
                # resulting evaluator actually enforces policy.
                assert evaluator.enabled is True, (
                    f"CI={ci_value!r} produced an ALLOW-ALL evaluator in production"
                )

    def test_no_module_gates_production_on_bare_ci_truthiness(self) -> None:
        """No `os.getenv("CI")` truthiness gate may reappear in service code.

        `not os.getenv("CI")` is the exact expression that created the bypass.
        Callers must go through `shared_secrets.is_ci()`.
        """
        offenders: list[str] = []
        for path in REPO_SQL_API_ROOT.rglob("*.py"):
            if ".venv" in path.parts or "__pycache__" in path.parts:
                continue
            if path.name == pathlib.Path(__file__).name:
                continue  # this file names the pattern it forbids
            source = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), start=1):
                if 'getenv("CI")' in line or "getenv('CI')" in line:
                    offenders.append(f"{path.relative_to(REPO_SQL_API_ROOT)}:{lineno}: {line.strip()}")
        assert offenders == [], "bare os.getenv(\"CI\") truthiness gate reintroduced:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# A2 - production must not silently downgrade away from OPA
# ---------------------------------------------------------------------------


class TestProductionRequiresOpaUrl:
    """A2: missing OPA_URL in production must fail startup, not downgrade."""

    def test_production_without_opa_url_raises(self) -> None:
        env = {"ENVIRONMENT": "production", "CI": "", "OPA_URL": "", "OPA_ENABLED": ""}
        with patch.dict("os.environ", env, clear=False):
            import os

            for key in ("OPA_URL", "OPA_URL_FILE", "OPA_ENABLED", "OPA_ENABLED_FILE"):
                os.environ.pop(key, None)
            with pytest.raises(RuntimeError, match="OPA_URL is required"):
                OpaConfig.from_environment()

    def test_production_with_opa_enabled_but_no_url_raises(self) -> None:
        """The specific silent-downgrade bug: OPA_ENABLED=true, no URL."""
        env = {"ENVIRONMENT": "production", "CI": "", "OPA_ENABLED": "true"}
        with patch.dict("os.environ", env, clear=False):
            import os

            os.environ.pop("OPA_URL", None)
            os.environ.pop("OPA_URL_FILE", None)
            with pytest.raises(RuntimeError, match="OPA_URL is required"):
                OpaConfig.from_environment()

    def test_explicit_opa_disabled_is_an_accepted_opt_out(self) -> None:
        """OPA_ENABLED=false is a deliberate operator choice, so it must not raise."""
        env = {"ENVIRONMENT": "production", "CI": "", "OPA_ENABLED": "false"}
        with patch.dict("os.environ", env, clear=False):
            import os

            os.environ.pop("OPA_URL", None)
            os.environ.pop("OPA_URL_FILE", None)
            config = OpaConfig.from_environment()
            assert config.enabled is False
            assert config.url == ""

    def test_production_with_url_does_not_raise(self) -> None:
        env = {
            "ENVIRONMENT": "production",
            "CI": "",
            "OPA_URL": "http://opa:8181",
            "OPA_ENABLED": "true",
        }
        with patch.dict("os.environ", env, clear=False):
            config = OpaConfig.from_environment()
            assert config.enabled is True
            assert config.url == "http://opa:8181"

    def test_ci_relaxes_the_opa_url_requirement(self) -> None:
        """CI needs no OPA sidecar; the requirement is a production invariant."""
        with patch.dict("os.environ", {"ENVIRONMENT": "production", "CI": "true"}, clear=False):
            import os

            os.environ.pop("OPA_URL", None)
            os.environ.pop("OPA_URL_FILE", None)
            os.environ.pop("OPA_ENABLED", None)
            os.environ.pop("OPA_ENABLED_FILE", None)
            assert OpaConfig.from_environment().enabled is False

    def test_dev_is_unaffected(self) -> None:
        with patch.dict("os.environ", {"ENVIRONMENT": "dev"}, clear=False):
            import os

            os.environ.pop("OPA_URL", None)
            os.environ.pop("OPA_ENABLED", None)
            assert OpaConfig.from_environment().enabled is False


# ---------------------------------------------------------------------------
# A3 - strict validation of the OPA decision document
# ---------------------------------------------------------------------------


class TestOpaDecisionValidation:
    """A3: malformed OPA responses must deny, never allow."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "allowed_value",
        ["false", "False", "no", "0", "", "deny", 1, "true-ish"],
    )
    async def test_truthy_non_bool_never_allows(self, allowed_value: object) -> None:
        """`bool("false")` is True. Only the literal JSON `true` may allow."""
        evaluator = _opa_evaluator_returning({"allowed": allowed_value, "reason": "nope"})
        decision = await evaluator.evaluate(_principal(), "default", ["album"])
        assert decision.allowed is False, f"allowed={allowed_value!r} was coerced to ALLOW"

    @pytest.mark.asyncio
    async def test_literal_true_allows(self) -> None:
        evaluator = _opa_evaluator_returning({"allowed": True, "reason": "Allowed by policy."})
        decision = await evaluator.evaluate(_principal(), "default", ["album"])
        assert decision.allowed is True
        assert decision.reason == "Allowed by policy."

    @pytest.mark.asyncio
    async def test_missing_allowed_key_denies(self) -> None:
        evaluator = _opa_evaluator_returning({"reason": "no decision field"})
        decision = await evaluator.evaluate(_principal(), "default", ["album"])
        assert decision.allowed is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("result", [True, False, ["allowed"], "allowed", 1, 0])
    async def test_non_object_result_denies_without_raising(self, result: object) -> None:
        """A valid-JSON non-object has no .get; AttributeError must not escape."""
        evaluator = _opa_evaluator_returning(result)
        decision = await evaluator.evaluate(_principal(), "default", ["album"])
        assert decision.allowed is False
        assert "Invalid OPA response structure" in decision.reason

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["policy_ids", "masked_columns"])
    async def test_string_array_field_is_rejected(self, field: str) -> None:
        """A bare string would otherwise be iterated character by character.

        `"masked_columns": "ssn"` would mask the columns s, s, n - i.e. mask
        nothing while appearing to apply masking.
        """
        evaluator = _opa_evaluator_returning({"allowed": True, field: "ssn"})
        decision = await evaluator.evaluate(_principal(), "default", ["album"])
        assert decision.allowed is False
        assert "Invalid OPA response structure" in decision.reason

    @pytest.mark.asyncio
    async def test_mapping_field_rejects_non_object(self) -> None:
        evaluator = _opa_evaluator_returning({"allowed": True, "row_restrictions": ["region=region"]})
        decision = await evaluator.evaluate(_principal(), "default", ["album"])
        assert decision.allowed is False
        assert "Invalid OPA response structure" in decision.reason

    @pytest.mark.asyncio
    async def test_null_optional_fields_are_treated_as_empty(self) -> None:
        evaluator = _opa_evaluator_returning(
            {
                "allowed": True,
                "reason": "ok",
                "policy_ids": None,
                "row_restrictions": None,
                "masked_columns": None,
            }
        )
        decision = await evaluator.evaluate(_principal(), "default", ["album"])
        assert decision.allowed is True
        assert decision.policy_ids == ()
        assert decision.row_restrictions == {}
        assert decision.masked_columns == frozenset()

    @pytest.mark.asyncio
    async def test_well_formed_decision_round_trips(self) -> None:
        evaluator = _opa_evaluator_returning(
            {
                "allowed": True,
                "reason": "Allowed by policy.",
                "policy_ids": ["allow-album"],
                "row_restrictions": {"region": "region"},
                "masked_columns": ["total"],
            }
        )
        decision = await evaluator.evaluate(_principal(), "default", ["album"])
        assert decision.allowed is True
        assert decision.policy_ids == ("allow-album",)
        assert decision.row_restrictions == {"region": "region"}
        assert decision.masked_columns == frozenset({"total"})

    @pytest.mark.asyncio
    @pytest.mark.parametrize("result", [True, "x", 3, ["a"]])
    async def test_effective_access_non_object_denies(self, result: object) -> None:
        evaluator = _opa_evaluator_returning(result)
        access = await evaluator.effective_access(_principal(), "default")
        assert access.accessible_tables == frozenset()
        assert access.allowed_columns == frozenset()

    @pytest.mark.asyncio
    async def test_effective_access_string_array_denies(self) -> None:
        evaluator = _opa_evaluator_returning({"accessible_tables": "album"})
        access = await evaluator.effective_access(_principal(), "default")
        assert access.accessible_tables == frozenset()

    @pytest.mark.asyncio
    async def test_transport_failure_still_denies(self) -> None:
        evaluator = OpaPolicyEvaluator(config=OpaConfig(url="http://opa:8181", enabled=True))
        client = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        client.is_closed = False
        evaluator._client = client
        decision = await evaluator.evaluate(_principal(), "default", ["album"])
        assert decision.allowed is False

    def test_is_enabled_property_reflects_config(self) -> None:
        assert OpaPolicyEvaluator(config=OpaConfig(url="", enabled=False)).is_enabled is False
        assert OpaPolicyEvaluator(config=OpaConfig(url="http://opa:8181", enabled=True)).is_enabled is True


# ---------------------------------------------------------------------------
# A4 - no default policy evaluator on the gateway
# ---------------------------------------------------------------------------


class TestGatewayRequiresExplicitEvaluator:
    """A4: omitting the evaluator must raise, not fall back to ALLOW-ALL."""

    def test_policy_evaluator_is_keyword_only_and_has_no_default(self) -> None:
        """The signature itself is the control: no default exists to fall back on."""
        import inspect

        params = inspect.signature(GovernedQueryGateway.__init__).parameters
        assert params["policy_evaluator"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["policy_evaluator"].default is inspect.Parameter.empty

    def test_omitting_evaluator_raises_type_error(self) -> None:
        """Omitting the argument must be a hard error, not a silent ALLOW-ALL."""
        provider = MagicMock()
        with pytest.raises(TypeError, match="policy_evaluator"):
            GovernedQueryGateway(provider, DefaultSqlSafetyChecker())

    def test_explicit_none_evaluator_raises_type_error(self) -> None:
        """Passing None is caught by an explicit guard with a useful message."""
        provider = MagicMock()
        with pytest.raises(TypeError, match="requires an explicit policy_evaluator"):
            GovernedQueryGateway(provider, DefaultSqlSafetyChecker(), policy_evaluator=None)

    @pytest.mark.asyncio
    async def test_explicit_permissive_evaluator_still_allows(self) -> None:
        """The escape hatch still exists - but only when named out loud."""
        service = MagicMock()
        service.execute_sql_statement = AsyncMock(return_value=[{"id": 1}])
        provider = MagicMock()
        from services.tenant_database_resolver import TenantDatabaseConfig

        provider.resolve.return_value = (
            TenantDatabaseConfig("org-42", "default", "sqlite+aiosqlite:///:memory:"),
            service,
        )
        gateway = GovernedQueryGateway(
            provider,
            DefaultSqlSafetyChecker(),
            policy_evaluator=PolicyEvaluator(enabled=False),
        )
        from services.query_gateway import GovernedQueryRequest

        result = await gateway.execute(
            GovernedQueryRequest(principal=_principal(), database_id="default", sql="SELECT id FROM album")
        )
        assert result == [{"id": 1}]

    def test_no_test_silently_relies_on_the_removed_default(self) -> None:
        """Every GovernedQueryGateway call must name its evaluator.

        Prevents the permissive default from creeping back in as an unnamed
        argument in a new test.
        """
        offenders: list[str] = []
        this_file = pathlib.Path(__file__).name
        for path in (REPO_SQL_API_ROOT / "tests").rglob("*.py"):
            if path.name == this_file:
                continue  # this file constructs one on purpose, to assert it raises
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if name != "GovernedQueryGateway":
                    continue
                if "policy_evaluator" not in {kw.arg for kw in node.keywords}:
                    offenders.append(f"{path.name}:{node.lineno}")
        assert offenders == [], "GovernedQueryGateway call without policy_evaluator:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# A5 - readiness reflects authorization posture
# ---------------------------------------------------------------------------


class TestReadinessAssertsPolicyEnforcement:
    """A5: /readyz must not report ready while the gateway would ALLOW-ALL."""

    def test_problem_detected_for_allow_all_in_production(self) -> None:
        from routes import health_routes

        with patch("routes.sql_query_controller._policy_evaluator", PolicyEvaluator(enabled=False)):
            with patch.dict("os.environ", {"ENVIRONMENT": "production"}):
                problem = health_routes._policy_enforcement_problem()
        assert problem is not None
        assert "ALLOW-ALL" in problem

    @pytest.mark.asyncio
    async def test_production_allow_all_evaluator_returns_503(self) -> None:
        """End-to-end: /readyz must refuse traffic when the gateway allows all."""
        from fastapi import HTTPException

        from routes import health_routes

        with patch("routes.sql_query_controller._policy_evaluator", PolicyEvaluator(enabled=False)):
            with patch.dict("os.environ", {"ENVIRONMENT": "production"}):
                with patch.object(health_routes, "TenantDatabaseResolver") as resolver:
                    resolver.from_environment.return_value = object()
                    with pytest.raises(HTTPException) as exc:
                        await health_routes.readiness()
        assert exc.value.status_code == 503
    def test_enforcing_legacy_evaluator_is_ready(self) -> None:
        from routes import health_routes

        enforcing = PolicyEvaluator(
            [Policy(id="allow-album", effect="allow", org_id="org-42", table="album")],
            enabled=True,
        )
        with patch("routes.sql_query_controller._policy_evaluator", enforcing):
            with patch.dict("os.environ", {"ENVIRONMENT": "production"}):
                assert health_routes._policy_enforcement_problem() is None

    def test_deny_all_legacy_evaluator_is_ready(self) -> None:
        """Deny-all is fail-closed, so a missing policy must not block startup."""
        from routes import health_routes

        with patch("routes.sql_query_controller._policy_evaluator", PolicyEvaluator((), enabled=True)):
            with patch.dict("os.environ", {"ENVIRONMENT": "production"}):
                assert health_routes._policy_enforcement_problem() is None

    def test_disabled_opa_is_ready_because_it_denies(self) -> None:
        """A disabled OPA evaluator denies everything, which is safe."""
        from routes import health_routes

        evaluator = OpaPolicyEvaluator(config=OpaConfig(url="", enabled=False))
        with patch("routes.sql_query_controller._policy_evaluator", evaluator):
            with patch.dict("os.environ", {"ENVIRONMENT": "production"}):
                assert health_routes._policy_enforcement_problem() is None

    def test_non_production_is_not_gated(self) -> None:
        """Dev/test must not fail readiness; /readyz is the stack healthcheck."""
        from routes import health_routes

        with patch("routes.sql_query_controller._policy_evaluator", PolicyEvaluator(enabled=False)):
            with patch.dict("os.environ", {"ENVIRONMENT": "test"}):
                assert health_routes._policy_enforcement_problem() is None

    def test_readiness_endpoint_still_ok_in_default_test_env(self) -> None:
        from app_factory import create_app

        with TestClient(create_app()) as client:
            assert client.get("/readyz").status_code == 200

    def test_liveness_unaffected(self) -> None:
        from app_factory import create_app

        with TestClient(create_app()) as client:
            assert client.get("/healthz").status_code == 200
