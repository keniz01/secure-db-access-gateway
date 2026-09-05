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
