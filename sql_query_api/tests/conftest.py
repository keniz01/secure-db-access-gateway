"""Test-only configuration for the production tenant resolver."""

import os

import pytest

# Tests run as non-production (fail-open legacy path) - explicit to match shared_secrets default production
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "TENANT_DATABASES_JSON",
    '[{"org_id":"org-42","database_id":"default","connection_string":"sqlite+aiosqlite:///:memory:"}]',
)

# Every variable shared_secrets.is_ci() inspects. Keep in sync with that function.
CI_DETECTION_VARS = ("GITHUB_ACTIONS", "CI", "CONTINUOUS_INTEGRATION", "BUILD_NUMBER")


@pytest.fixture
def non_ci_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee a non-CI environment for tests that assert a gate *fires*.

    Production fail-fasts are suppressed inside CI, which is correct. But a test
    that deletes only ``CI`` still sees ``GITHUB_ACTIONS=true`` on GitHub
    Actions, so the gate is suppressed, no exception is raised, and the test
    fails in CI while passing locally. Any test relying on a production gate
    actually firing must request this fixture rather than clearing ``CI`` alone.
    """
    for name in CI_DETECTION_VARS:
        monkeypatch.delenv(name, raising=False)
