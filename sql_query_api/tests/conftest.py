"""Test-only configuration for the production tenant resolver."""

import os


os.environ.setdefault(
    "TENANT_DATABASES_JSON",
    '[{"org_id":"org-42","database_id":"default","connection_string":"sqlite+aiosqlite:///:memory:"}]',
)
