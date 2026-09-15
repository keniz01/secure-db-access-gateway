"""Hermetic tests for the database-side resource controls."""

import pytest

from dependencies.dependency_container import pool_settings_from_env
from probe.run import check_db_side_resource_controls
from repositories.sql_query_repository import SqlQueryRepository


class TestPoolSettingsFromEnv:
    def test_defaults_are_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT_SECONDS", "DB_POOL_RECYCLE_SECONDS"):
            monkeypatch.delenv(key, raising=False)
        assert pool_settings_from_env() == {
            "pool_size": 5,
            "max_overflow": 10,
            "pool_timeout": 30.0,
            "pool_recycle": 1800,
        }

    def test_custom_values_round_trip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_POOL_SIZE", "2")
        monkeypatch.setenv("DB_MAX_OVERFLOW", "3")
        monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "15")
        monkeypatch.setenv("DB_POOL_RECYCLE_SECONDS", "900")
        assert pool_settings_from_env() == {
            "pool_size": 2,
            "max_overflow": 3,
            "pool_timeout": 15.0,
            "pool_recycle": 900,
        }

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("DB_POOL_SIZE", "0"),
            ("DB_POOL_SIZE", "-1"),
            ("DB_MAX_OVERFLOW", "-1"),
            ("DB_POOL_TIMEOUT_SECONDS", "0"),
            ("DB_POOL_TIMEOUT_SECONDS", "-5"),
            ("DB_POOL_RECYCLE_SECONDS", "-1"),
        ],
    )
    def test_invalid_values_fail_fast(
        self, key: str, value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(key, value)
        with pytest.raises(ValueError):
            pool_settings_from_env()


class TestLockTimeoutValidation:
    def test_non_positive_lock_timeout_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SQL_LOCK_TIMEOUT_SECONDS", "0")
        with pytest.raises(ValueError):
            SqlQueryRepository(engine=None, sql_safety_checker=None)  # type: ignore[arg-type]

    def test_default_lock_timeout_is_milliseconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SQL_LOCK_TIMEOUT_SECONDS", raising=False)
        repo = SqlQueryRepository(engine=None, sql_safety_checker=None)  # type: ignore[arg-type]
        assert repo._lock_timeout_ms == 5000


class TestCheckDbSideResourceControls:
    def test_all_configured_passes(self) -> None:
        violations = check_db_side_resource_controls(
            {
                "statement_timeout": "30s",
                "lock_timeout": "5s",
                "idle_in_transaction_session_timeout": "10s",
            },
            connection_limit=20,
        )
        assert violations == []

    @pytest.mark.parametrize(
        "settings",
        [
            {"statement_timeout": "0", "lock_timeout": "5s", "idle_in_transaction_session_timeout": "10s"},
            {"statement_timeout": "30s", "lock_timeout": "0s", "idle_in_transaction_session_timeout": "10s"},
            {"statement_timeout": "30s", "lock_timeout": "5s", "idle_in_transaction_session_timeout": "0"},
            {"statement_timeout": "off", "lock_timeout": "5s", "idle_in_transaction_session_timeout": "10s"},
            {"statement_timeout": "30s"},  # missing lock + idle
        ],
    )
    def test_disabled_or_missing_budget_is_a_violation(self, settings: dict[str, str]) -> None:
        violations = check_db_side_resource_controls(settings, connection_limit=20)
        assert violations
        assert all("not configured" in violation for violation in violations)

    @pytest.mark.parametrize("connection_limit", [None, 0, -1])
    def test_missing_connection_limit_is_a_violation(self, connection_limit: int | None) -> None:
        violations = check_db_side_resource_controls(
            {
                "statement_timeout": "30s",
                "lock_timeout": "5s",
                "idle_in_transaction_session_timeout": "10s",
            },
            connection_limit=connection_limit,
        )
        assert any("connection limit" in violation for violation in violations)
