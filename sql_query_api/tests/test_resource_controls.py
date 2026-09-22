"""Hermetic tests for the database-side resource controls."""

import pytest

from dependencies.dependency_container import pool_settings_from_env, pool_settings_from_config
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


class TestPoolSettingsFromConfig:
    """Tests for per-tenant pool settings merging with global defaults."""

    def test_empty_config_uses_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT_SECONDS", "DB_POOL_RECYCLE_SECONDS"):
            monkeypatch.delenv(key, raising=False)
        assert pool_settings_from_config({}) == {
            "pool_size": 5,
            "max_overflow": 10,
            "pool_timeout": 30.0,
            "pool_recycle": 1800,
        }

    def test_partial_override_merges_with_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT_SECONDS", "DB_POOL_RECYCLE_SECONDS"):
            monkeypatch.delenv(key, raising=False)
        # Only override pool_size and pool_timeout
        result = pool_settings_from_config({"pool_size": 10, "pool_timeout": 60.0})
        assert result == {
            "pool_size": 10,
            "max_overflow": 10,
            "pool_timeout": 60.0,
            "pool_recycle": 1800,
        }

    def test_full_override_replaces_all_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT_SECONDS", "DB_POOL_RECYCLE_SECONDS"):
            monkeypatch.delenv(key, raising=False)
        result = pool_settings_from_config({
            "pool_size": 20,
            "max_overflow": 5,
            "pool_timeout": 15.0,
            "pool_recycle": 900,
        })
        assert result == {
            "pool_size": 20,
            "max_overflow": 5,
            "pool_timeout": 15.0,
            "pool_recycle": 900,
        }

    def test_none_values_use_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT_SECONDS", "DB_POOL_RECYCLE_SECONDS"):
            monkeypatch.delenv(key, raising=False)
        result = pool_settings_from_config({
            "pool_size": None,
            "max_overflow": None,
            "pool_timeout": None,
            "pool_recycle": None,
        })
        assert result == {
            "pool_size": 5,
            "max_overflow": 10,
            "pool_timeout": 30.0,
            "pool_recycle": 1800,
        }

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("pool_size", 0),
            ("pool_size", -1),
            ("max_overflow", -1),
            ("pool_timeout", 0),
            ("pool_timeout", -5.0),
            ("pool_recycle", -1),
        ],
    )
    def test_invalid_override_fail_fast(self, key: str, value: int | float, monkeypatch: pytest.MonkeyPatch) -> None:
        for k in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT_SECONDS", "DB_POOL_RECYCLE_SECONDS"):
            monkeypatch.delenv(k, raising=False)
        with pytest.raises(ValueError):
            pool_settings_from_config({key: value})
