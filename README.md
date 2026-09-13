# Secure DB Access Gateway

Secure DB Access Gateway is a read-only database exploration platform that lets users safely inspect schemas and execute SELECT-only queries behind Auth0-based identity and organization-aware authorization.

The project is structured as a three-part system:
- `web-app/` — React + TypeScript UI for schema browsing, queries, and authentication
- `sql_query_api/` — FastAPI + Strawberry GraphQL API for validated read-only SQL execution
- `auth0_api/` — Auth0 session and user orchestration layer with org-aware claims and optional AI greeting support

## What’s included

- SELECT-only SQL validation with blocking of DML/DDL and unsafe patterns
- Automatic query limits and audit logging for every SQL execution
- Dynamic schema introspection for arbitrary database tables and foreign keys
- Auth0 JWT validation with `viewer` / `admin` role checks
- Tenant-aware principal mapping using a required trusted tenant claim and enforcement at the middleware layer
- Server-side tenant database resolution using opaque logical `database_id` values
- Web, API, AI, and CLI execution through the shared governed query gateway
- Schema browser and admin-safe UI for browsing connected database metadata
- Docker Compose setup for the web app, APIs, Nginx gateway, and OTEL/LGTM observability stack
- CI pipeline for backend and frontend validation

## Current architecture

### Web application
- React 19
- TypeScript
- Vite
- Tailwind CSS
- TanStack Query
- Browser-based schema inspection and GraphQL calls to the SQL API

### SQL Query API
- FastAPI
- Strawberry GraphQL
- SQLAlchemy async engine
- SQLite/PostgreSQL-compatible schema introspection
- Read-only validation enforced before execution
- Middleware-based Auth0 and RBAC enforcement
- One execution pipeline for tenant resolution, SQL classification, limits, read-only execution, masking, and audit

### Auth0 API
- FastAPI service for auth flows and session management
- Auth0 OAuth integration and JWT identity validation
- Optional AI greeting support via Azure/OpenAI-compatible services
- Org-aware user metadata handling

## Repository layout

```text
secure-db-access-gateway/
├── .github/workflows/ci.yml
├── auth0_api/
│   ├── app/
│   ├── prompts/
│   ├── tests/
│   ├── main.py
│   ├── pyproject.toml
│   └── README.md
├── sql_query_api/
│   ├── config/
│   ├── dependencies/
│   ├── graphql_schema/
│   ├── middlewares/
│   ├── repositories/
│   ├── routes/
│   ├── services/
│   ├── tests/
│   ├── app_factory.py
│   ├── auth.py
│   ├── main.py
│   ├── pyproject.toml
│   └── README.md
├── web-app/
│   ├── src/
│   ├── public/
│   ├── package.json
│   └── vite.config.ts
├── nginx/
├── .env.example
├── docker-compose.yml
├── ARCHITECTURE.md
├── SECURITY.md
├── GEMINI.md
├── LICENSE
├── README.md
└── explore.py
```

## Quick start

### Docker Compose (recommended)

```bash
./scripts/bootstrap-dev.sh
# fill in real values in .env (edit the empty fields)
# generate a session key with: openssl rand -hex 32

docker compose up --build
```

This starts:
- Auth0 API: http://localhost:8001
- SQL Query API: http://localhost:8002
- Nginx gateway (HTTPS, serves the web app + APIs): https://localhost:8443 (plaintext on :8080 redirects to HTTPS)
- OTEL/LGTM stack: http://localhost:3000

Browse the web app at **https://localhost:8443** — the SPA is served through the
TLS edge so auth cookies (httpOnly + Secure) work end-to-end. The Vite dev
server still runs on http://localhost:5173 but is not the supported entrypoint.

The Nginx gateway terminates TLS using a certificate signed by the **mkcert
local CA** (`certs/web_tls_cert.pem` / `certs/web_tls_key.pem`).
`scripts/bootstrap-dev.sh` runs `mkcert -install` once (macOS password prompt),
which makes every browser on this machine trust the local CA — so
`https://localhost:8443` loads with **no security warning**. Requires
`brew install mkcert`. For production, replace the cert with one from a trusted
CA (or set up ACME).

### Manual setup

#### 1) SQL Query API

```bash
cd sql_query_api
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
python main.py
```

#### 2) Auth0 API

```bash
cd auth0_api
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
python main.py
```

#### 3) Web app

```bash
cd web-app
npm install
npm run dev
```

## Environment and secrets

There are no secret files in this repository. Values are injected as
environment variables from a single gitignored env file:

- **Dev**: `.env` — copied from `.env.example` by `scripts/bootstrap-dev.sh`
- **Production**: `/etc/gateway/gateway.env` on the host (manual provisioning)

Compose injects the file into both backend services via `env_file` (override
the path with `GATEWAY_ENV_FILE`), and the shared `read_secret` loader
(`shared/shared_secrets`) reads the values, falling back to an optional
`NAME_FILE` path for orchestrators that mount secrets as files.

```dotenv
# .env (gitignored; real values)
ENVIRONMENT=production
APP_SECRET_KEY=<openssl rand -hex 32>
AUTH0_DOMAIN=your-domain.auth0.com
AUTH0_CLIENT_ID=...
AUTH0_CLIENT_SECRET=...
AUTH0_AUDIENCE=https://your-api-audience
FRONTEND_URL=https://localhost:8443

# Tenant mappings (one line) / data-access policy (one line)
TENANT_DATABASES_JSON=[{"org_id":"...","database_id":"default","connection_string":"...","data_schema":"music","metadata_schema":"meta"}]
POLICY_POLICIES_JSON=[{"id":"allow-album","effect":"allow","database_id":"default","table":"album"}]

# AI services
OPENROUTER_API_KEY=...
GEMINI_API_KEY=...
EMBEDDING_DIMENSIONS=768
AI_MODEL=openai/gpt-4o
EMBEDDING_MODEL=text-embedding-004
```

See `.env.example` for the authoritative list of variables and comments.

Production note: the host env file is a *copy* — keep the source of truth in a
password manager or secret manager and rotate by updating the source, rewriting
the host file, and running `docker compose up -d`.

## Automated deployment (CI/CD)

Images are built once in CI and pushed to GHCR; the host only pulls them and
secrets never enter the pipeline. See `DOCKER_README.md` → *Production* for
full details.

- **Image builds**: `.github/workflows/docker.yml` builds all three services on
  every push to `main` (`edge`) and on `v*` tags (semver + `latest`).
- **Deploys**: `.github/workflows/deploy.yml` SSHes to the host, checks out the
  deployed commit, `docker login`s with a read-scoped PAT, and runs
  `docker compose ... up -d --no-build --wait` — failing if any healthcheck
  stays red (`ENVIRONMENT=production` also fails fast on missing secrets).
- **Rollback**: re-run the **Deploy to Production** workflow with `image_tag`
  set to an older published tag (e.g. `v1.2.2`). No source revert and no rebuild.

## Security model

This application is designed around a read-only database access model:

- only SELECT-style queries are accepted by the SQL safety layer
- DDL/DML and other mutating statements are rejected
- request identity comes from validated Auth0 JWT claims, not caller-supplied headers
- tenant scoping is driven from the required signed tenant claim
  `https://app.secure-db-access-gateway.org/tenant_id` and is enforced in middleware
- every governed operation resolves `(tenant_id, database_id)` against server-side configuration
- requests without a trusted tenant claim are rejected; the application does not manage users or memberships
- audit logging captures trusted user, org, database, and table access metadata
- rate limiting and structured logging are enabled for operational control

For the full policy and threat model, see [SECURITY.md](SECURITY.md).

## Testing

Run the project checks with the same tooling used in CI:

```bash
# SQL Query API
cd sql_query_api
python -m pytest

# Auth0 API
cd ../auth0_api
python -m pytest

# Web app
cd ../web-app
npm run lint
npm test -- --pretty false
npm run build
```

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — system design and component responsibilities
- [SECURITY.md](SECURITY.md) — security controls and governance notes
- [GEMINI.md](GEMINI.md) — AI/CLI guardrails and project context
- [RUNBOOKS.md](RUNBOOKS.md) — incident response runbooks and escalation model
- [PRODUCTION_READINESS_ROADMAP.md](PRODUCTION_READINESS_ROADMAP.md) — gaps and next steps to production readiness
- [sql_query_api/README.md](sql_query_api/README.md)
- [auth0_api/README.md](auth0_api/README.md)
- [web-app/README.md](web-app/README.md)

## Contributing

1. Create a feature branch from your work branch
2. Keep changes focused and testable
3. Update relevant docs when behavior changes
4. Run the targeted backend/frontend checks before opening a PR

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
