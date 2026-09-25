"""Test-only configuration for the production tenant resolver."""

import os

# Tests run as non-production (fail-open legacy path) - explicit to match shared_secrets default production
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "TENANT_DATABASES_JSON",
    '[{"org_id":"org-42","database_id":"default","connection_string":"sqlite+aiosqlite:///:memory:"}]',
)
