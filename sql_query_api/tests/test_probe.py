"""Hermetic tests for the operator connectivity probe (probe/run.py)."""

import pytest

from probe.run import _effective_targets, main, normalize_connection_string, probe_one
from services.tenant_database_resolver import TenantDatabaseConfig


class TestNormalizeConnectionString:
    """The probe must mirror the gateway's read-only SQLite handling."""

    def test_file_sqlite_gets_readonly_uri(self) -> None:
        normalized = normalize_connection_string("sqlite:///tmp/data.db")
        assert normalized == "sqlite:///file:/tmp/data.db?mode=ro&uri=true"

    def test_uri_sqlite_without_mode_gets_readonly(self) -> None:
        normalized = normalize_connection_string("sqlite+aiosqlite:///data.db?journal_mode=WAL")
        assert "mode=ro" in normalized and "uri=true" in normalized

    def test_memory_sqlite_is_left_alone(self) -> None:
        assert normalize_connection_string("sqlite+aiosqlite:///:memory:") == "sqlite+aiosqlite:///:memory:"

    def test_postgres_is_not_touched(self) -> None:
        url = "postgresql+asyncpg://user:pass@db:5432/tenant"
        assert normalize_connection_string(url) == url


class TestProbeOne:
    async def test_reaches_in_memory_sqlite(self) -> None:
        result = await probe_one("sqlite+aiosqlite:///:memory:")
        assert result.ok is True

    async def test_readonly_missing_file_fails(self) -> None:
        result = await probe_one("sqlite+aiosqlite:///file:/does-not-exist-probe.db?mode=ro&uri=true")
        assert result.ok is False
        assert result.detail


class TestEffectiveTargets:
    def test_deduplicates_and_labels_replicas(self) -> None:
        configs = [
            TenantDatabaseConfig("org-a", "db-1", "sqlite+aiosqlite:///one.db"),
            TenantDatabaseConfig("org-b", "db-1", "sqlite+aiosqlite:///one.db"),
            TenantDatabaseConfig(
                "org-a",
                "db-2",
                "sqlite+aiosqlite:///two.db",
                replica_connection_string="sqlite+aiosqlite:///two-replica.db",
                use_read_replica=True,
            ),
        ]
        targets = _effective_targets(configs)
        assert targets["sqlite+aiosqlite:///one.db"] == "org-a/db-1"
        assert targets["sqlite+aiosqlite:///two-replica.db"] == "org-a/db-2 (replica)"


class TestMain:
    def test_main_ok_for_configured_memory_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TENANT_DATABASES_JSON", '{"org-a": {"db-1": "sqlite+aiosqlite:///:memory:"}}')
        assert main(["--timeout", "5"]) == 0

    def test_main_fails_when_a_target_is_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "TENANT_DATABASES_JSON",
            '{"org-a": {"db-1": "sqlite+aiosqlite:///file:/missing-probe.db?mode=ro&uri=true"}}',
        )
        assert main(["--timeout", "5"]) == 1