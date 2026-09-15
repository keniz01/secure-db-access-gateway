"""Hermetic tests for the database-level read-only guardrails."""

import pytest

from probe.run import _redact_url
from repositories.sql_query_repository import (
    enforce_readonly_role_guardrail,
    validate_readonly_role_name,
)


class TestValidateReadonlyRoleName:
    def test_none_passes_through(self) -> None:
        assert validate_readonly_role_name(None) is None

    def test_bare_role_name_round_trips(self) -> None:
        assert validate_readonly_role_name("gateway_readonly_user") == "gateway_readonly_user"

    def test_whitespace_is_stripped(self) -> None:
        assert validate_readonly_role_name("  gateway_readonly_user  ") == "gateway_readonly_user"

    @pytest.mark.parametrize(
        "candidate",
        [
            "",
            "   ",
            "public.gateway_role",
            "music.gateway_role",
            "gateway role",
            "gateway-role",
            "gateway'role",
            "gateway;DROP TABLE t",
            "1gateway",
        ],
    )
    def test_rejects_non_identifiers(self, candidate: str) -> None:
        with pytest.raises(ValueError):
            validate_readonly_role_name(candidate)


class TestEnforceReadonlyRoleGuardrail:
    def test_sqlite_never_requires_a_role(self) -> None:
        enforce_readonly_role_guardrail("sqlite+aiosqlite:///:memory:", production=True)

    def test_postgres_allowed_outside_production(self) -> None:
        enforce_readonly_role_guardrail(
            "postgresql+asyncpg://user:pw@db:5432/t", production=False
        )

    def test_postgres_production_without_role_fails(  # type: ignore[misc]
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SQL_READONLY_ROLE", raising=False)
        with pytest.raises(RuntimeError):
            enforce_readonly_role_guardrail(
                "postgresql+asyncpg://user:pw@db:5432/t", production=True
            )

    def test_postgres_production_with_role_passes(  # type: ignore[misc]
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SQL_READONLY_ROLE", "gateway_readonly_user")
        enforce_readonly_role_guardrail(
            "postgresql+asyncpg://user:pw@db:5432/t", production=True
        )

    def test_postgres_production_with_invalid_role_fails(  # type: ignore[misc]
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SQL_READONLY_ROLE", "public.gateway_role")
        with pytest.raises(ValueError):
            enforce_readonly_role_guardrail(
                "postgresql+asyncpg://user:pw@db:5432/t", production=True
            )


class TestRedactUrl:
    def test_password_is_removed(self) -> None:
        url = "postgresql+asyncpg://gateway_readonly_user:supersecret@db.example:5432/music"
        assert _redact_url(url) == "postgresql+asyncpg://gateway_readonly_user@db.example:5432/music"

    def test_without_credentials_is_unchanged(self) -> None:
        url = "postgresql+asyncpg://db.example:5432/music"
        assert _redact_url(url) == url

    def test_only_user_is_kept(self) -> None:
        url = "postgresql+asyncpg://replicator:rot@db.example:5432/music?sslmode=require"
        redacted = _redact_url(url)
        assert "rot" not in redacted
        assert "replicator@" in redacted