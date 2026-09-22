"""Tests for the OPA policy evaluator integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auth import Principal
from services.opa_policy_engine import OpaConfig, OpaPolicyEvaluator
from services.policy_engine import EffectiveAccess, PolicyDecision


def _principal(
    user_id: str = "user-1",
    org_id: str = "org-42",
    roles: frozenset[str] | None = None,
    email: str = "user@example.com",
) -> Principal:
    return Principal(
        user_id=user_id,
        org_id=org_id,
        roles=roles or frozenset({"viewer"}),
        email=email,
        attributes={},
    )


class TestOpaConfig:
    """Tests for OPA configuration loading."""

    def test_config_from_environment_defaults(self):
        with patch.dict("os.environ", {}, clear=False):
            config = OpaConfig.from_environment()
            assert config.enabled is False
            assert config.url == ""

    def test_config_from_environment_with_url(self):
        with patch.dict("os.environ", {"OPA_URL": "http://localhost:8181"}):
            config = OpaConfig.from_environment()
            assert config.enabled is True
            assert config.url == "http://localhost:8181"

    def test_config_from_environment_disabled(self):
        with patch.dict("os.environ", {"OPA_URL": "http://localhost:8181", "OPA_ENABLED": "false"}):
            config = OpaConfig.from_environment()
            assert config.enabled is False

    def test_config_strips_trailing_slash(self):
        with patch.dict("os.environ", {"OPA_URL": "http://localhost:8181/"}):
            config = OpaConfig.from_environment()
            assert config.url == "http://localhost:8181"


class TestOpaPolicyEvaluator:
    """Tests for the OPA policy evaluator."""

    @pytest.mark.asyncio
    async def test_disabled_evaluator_always_denies(self):
        evaluator = OpaPolicyEvaluator(config=OpaConfig(url="", enabled=False))
        principal = _principal()
        decision = await evaluator.evaluate(principal, "default", ["album"])
        # Disabled evaluator fails closed
        assert decision.allowed is False
        assert "disabled" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_enabled_evaluator_with_mocked_opa(self):
        config = OpaConfig(url="http://localhost:8181", enabled=True)
        evaluator = OpaPolicyEvaluator(config=config)

        # Mock the OPA response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "allowed": True,
                "reason": "Allowed by policy.",
                "policy_ids": ["allow-album"],
                "row_restrictions": {},
                "masked_columns": [],
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False
        evaluator._client = mock_client

        principal = _principal()
        decision = await evaluator.evaluate(principal, "default", ["album"])

        assert decision.allowed is True
        assert decision.reason == "Allowed by policy."
        assert decision.policy_ids == ("allow-album",)
        mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_enabled_evaluator_denied_by_opa(self):
        config = OpaConfig(url="http://localhost:8181", enabled=True)
        evaluator = OpaPolicyEvaluator(config=config)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "allowed": False,
                "reason": "Denied by policy.",
                "policy_ids": ["deny-all"],
                "row_restrictions": {},
                "masked_columns": [],
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False
        evaluator._client = mock_client

        principal = _principal()
        decision = await evaluator.evaluate(principal, "default", ["album"])

        assert decision.allowed is False
        assert "Denied by policy" in decision.reason

    @pytest.mark.asyncio
    async def test_opa_unreachable_fails_closed(self):
        import httpx

        config = OpaConfig(url="http://localhost:8181", enabled=True)
        evaluator = OpaPolicyEvaluator(config=config)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Connection timed out"))
        mock_client.is_closed = False
        evaluator._client = mock_client

        principal = _principal()
        decision = await evaluator.evaluate(principal, "default", ["album"])

        # Fail closed: OPA unreachable = deny
        assert decision.allowed is False
        assert "unreachable" in decision.reason.lower() or "failed" in decision.reason.lower()

    @pytest.mark.asyncio
    async def test_effective_access_with_mocked_opa(self):
        config = OpaConfig(url="http://localhost:8181", enabled=True)
        evaluator = OpaPolicyEvaluator(config=config)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "accessible_tables": ["album", "artist"],
                "allowed_columns": ["album_id", "title", "artist_id"],
                "masked_columns": ["total"],
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False
        evaluator._client = mock_client

        principal = _principal()
        access = await evaluator.effective_access(principal, "default")

        assert isinstance(access, EffectiveAccess)
        assert "album" in access.accessible_tables
        assert "artist" in access.accessible_tables
        assert "total" in access.masked_columns

    @pytest.mark.asyncio
    async def test_effective_access_opa_unreachable_fails_closed(self):
        import httpx

        config = OpaConfig(url="http://localhost:8181", enabled=True)
        evaluator = OpaPolicyEvaluator(config=config)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client.is_closed = False
        evaluator._client = mock_client

        principal = _principal()
        access = await evaluator.effective_access(principal, "default")

        # Fail closed: empty access
        assert access.accessible_tables == frozenset()
        assert access.allowed_columns == frozenset()

    @pytest.mark.asyncio
    async def test_disabled_effective_access_returns_empty(self):
        evaluator = OpaPolicyEvaluator(config=OpaConfig(url="", enabled=False))
        principal = _principal()
        access = await evaluator.effective_access(principal, "default")
        # Disabled evaluator fails closed: empty access
        assert access.accessible_tables == frozenset()
        assert access.allowed_columns == frozenset()

    @pytest.mark.asyncio
    async def test_empty_tables_returns_denied(self):
        config = OpaConfig(url="http://localhost:8181", enabled=True)
        evaluator = OpaPolicyEvaluator(config=config)
        principal = _principal()
        decision = await evaluator.evaluate(principal, "default", [])
        # Empty tables = denied
        assert decision.allowed is False
        assert "No protected table" in decision.reason


class TestOpaPolicyEvaluatorInterface:
    """Verify OpaPolicyEvaluator implements the same interface as PolicyEvaluator."""

    def test_has_evaluate_method(self):
        assert hasattr(OpaPolicyEvaluator, "evaluate")

    def test_has_effective_access_method(self):
        assert hasattr(OpaPolicyEvaluator, "effective_access")

    def test_has_from_environment_classmethod(self):
        assert hasattr(OpaPolicyEvaluator, "from_environment")

    def test_has_close_method(self):
        assert hasattr(OpaPolicyEvaluator, "close")
