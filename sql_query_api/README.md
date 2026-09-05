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
  `TENANT_DATABASES_FILE` containing entries such as:

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
  `TENANT_DATABASES_FILE` is required; there is no single-database fallback.

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
