Use UV to to initialise a project and create virtual environment
- uv init
- uv venv .venv

Start web app
- uv uvicorn main:app --reload --log-level debug --port 8002

Linux/MacOS
- export DATABASE_URL="postgresql+asyncpg://user:password@localhost:5432/mydb"

Windows
- setx DATABASE_URL "postgresql+asyncpg://user:password@localhost:5432/mydb"
- $env:DATABASE_URL="postgresql+asyncpg://user:password@localhost:5432/mydb"
- Production uses opaque logical database identifiers and server-side mappings.
  Set `ENVIRONMENT=production` and provide `TENANT_DATABASES_JSON` or
  `TENANT_DATABASES_JSON_FILE` containing entries such as:

  ```json
  [
    {
      "org_id": "auth0-org-id",
      "database_id": "analytics",
      "connection_string": "******host:5432/appdb",
      "data_schema": "public",
      "metadata_schema": "meta"
    }
  ]
  ```

  Clients send only `database_id`; connection strings and credentials are never
  accepted from GraphQL requests. `TENANT_DATABASES_JSON` or
  `TENANT_DATABASES_JSON_FILE` is required; there is no single-database fallback.

  Every authenticated access token must contain the configured tenant claim:
  `https://app.secure-db-access-gateway.org/tenant_id`. The application does
  not create users or manage tenant membership; Auth0 or an upstream identity
  provider must issue this claim.

### Policy enforcement

The governed query gateway can load a central, default-deny policy document
from `POLICY_POLICIES_JSON`. Each policy may target `org_id`, `principal_id`,
`roles`, `database_id`, and `table`, and may specify `columns`,
`masked_columns`, and `row_scope` mappings from database columns to validated
subject attributes:

```json
[
  {
    "id": "eu-orders",
    "effect": "allow",
    "org_id": "auth0-org-id",
    "roles": ["viewer"],
    "database_id": "analytics",
    "table": "orders",
    "columns": ["id", "region", "total"],
    "masked_columns": ["total"],
    "row_scope": {"region": "region"}
  }
]
```

Denials take precedence, missing subject attributes fail closed, and row
predicates are added by the gateway before execution. `simulatePolicy` is
available to administrators through GraphQL and returns the decision without
reading protected data.

### Headless CLI authentication

The headless `explore.py` query path requires a validated OIDC/Auth0 access
token. Provide a short-lived token through `CLI_ACCESS_TOKEN` or, preferably
for automation, `CLI_ACCESS_TOKEN_FILE`. The CLI uses the same issuer,
audience, signature, expiry, tenant claim, role, and subject-attribute
validation as the API before constructing a principal. It does not accept
locally configured roles or tenant identity as authorization inputs.

### Database read-only enforcement (production)

App-level validation is defense-in-depth, not the boundary. Every PostgreSQL
connection:

1. runs `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` (all
   transactions, including introspection, are read-only at the engine);
2. runs `SET ROLE SQL_READONLY_ROLE` when configured (the bare role name is
   validated against `^[A-Za-z_][A-Za-z0-9_$]*$`);
3. pushes the per-query budgets as `SET SESSION` (statement and lock timeouts)
   so they hold across every transaction on the pooled connection.

Provision the dedicated `gateway_readonly_user` role per tenant database with
`scripts/setup_least_privilege_gateway_role.sql` (idempotent, fail-closed,
requires an explicit `gateway_password`; grants only `CONNECT` + `USAGE` +
`SELECT`, denies ownership/DDL/DML/`CREATE`/`TEMPORARY` and dangerous function
execution, and attaches `default_transaction_read_only=on`).

`ENVIRONMENT=production` without `SQL_READONLY_ROLE` fails fast at startup for
PostgreSQL tenants (skipped under `CI`).

### Database resource controls (production)

The same script attaches **role-level budgets** so the engine — not just the
app — bounds runaway work, even for a direct psql login:

- `statement_timeout` (default `30s`; keep `>= SQL_QUERY_TIMEOUT_SECONDS`),
  `lock_timeout` (default `5s`),
  `idle_in_transaction_session_timeout` (default `10s`);
- `work_mem` (default `4MB`) and `max_parallel_workers_per_gather`
  (default `0`) to cap per-operation and parallel-query memory;
- `CONNECTION LIMIT` (default `20`) to cap role connections per cluster.

All are tunable psql variables (`-v gateway_statement_timeout=...`, etc.) and
re-verified by the script's fail-closed verification block. The app-side pool is
bounded per engine by `DB_POOL_SIZE` + `DB_MAX_OVERFLOW` (validated at startup,
see `dependency_container.pool_settings_from_env`) and must fit under the role's
connection limit. `scripts/probe-production.sh` (probe P5) fails any tenant with
a disabled statement/lock/idle timeout or no connection limit.
