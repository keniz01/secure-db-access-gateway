# Secure DB Access Gateway

Secure DB Access Gateway is a read-only database exploration platform that lets users safely inspect schemas and execute SELECT-only queries behind Auth0-based identity and organization-aware authorization.

The project is structured as a three-part system:
- `web-app/` — React + TypeScript UI for schema browsing, queries, and authentication
- `sql_query_api/` — FastAPI + Strawberry GraphQL API for validated read-only SQL execution
- `auth0_api/` — Auth0 session and user orchestration layer with org-aware claims and optional AI greeting support

## What’s included

- SELECT-only SQL validation with blocking of DML/DDL and unsafe patterns (fail-closed `clean_sql` - no `SELECT *` synthesis)
- Automatic query limits, cost/timeout/row-byte guards and audit logging for every SQL execution
- Dynamic schema introspection filtered by `EffectiveAccess` (OPA/policy engine)
- Auth0 JWT validation (`RS256`, `leeway=2s`, scope `openid/profile/email` enforced) with RBAC1 hierarchy (`admin` -> `viewer`) and SoD via OPA
- Tenant-aware principal mapping using required `https://app.secure-db-access-gateway.org/tenant_id` claim, spoofable `X-User/Org/Tenant` headers stripped at edge + middleware
- Server-side tenant database resolution via opaque `database_id` (no connection strings from client)
- OPA bundle policy management (`sql_query_api/opa/policies/gateway.rego` + `data.json`, hot-reload via `scripts/build-opa-bundle.sh`, fail-closed on unreachable)
- Docker Compose with `frontend`/`backend` network segmentation, Redis `requirepass`, pinned images, and hardened nginx TLS (Mozilla intermediate)
- CI with SHA-pinned actions, `bandit`/`pip-audit` (both services) and `npm audit` supply-chain gates

## Current architecture

### Web application
- React 19
- TypeScript
- Vite
- Tailwind CSS
- TanStack Query
- Browser-based schema inspection and GraphQL calls to the SQL API

### SQL Query API
- FastAPI + Strawberry GraphQL + SQLAlchemy async (per-tenant pooled, `gateway_readonly_user` `SET ROLE` + `READ ONLY`)
- SQLite/PostgreSQL introspection via quoted `PRAGMA` identifiers (injection hardened)
- Governed gateway: tenant resolve -> `clean_sql` (no widening) -> `DefaultSqlSafetyChecker`/`AstSqlAnalyzer` -> OPA `PolicyEvaluator` (hierarchy/SoD, deny-precedence) -> `READ ONLY` + `SET ROLE` + timeouts/limits -> post-masking -> audit
- Auth via `httpOnly` JWT (`leeway=2s`, scope enforced) -> `Principal{roles,org_id}` (`has_any_role`), authorization delegated to OPA bundles

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
- Redis: redis://redis:6379/0 (session store, mandatory in prod)
- OPA: http://opa:8181 (expose only, policy evaluation)
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
cd sql_query_api && uv sync && uv run uvicorn main:app --reload --port 8002
# or: pip install -e ../shared && pip install -e . && ENVIRONMENT=dev python main.py
```

#### 2) Auth0 API

```bash
cd auth0_api && uv sync && uv run uvicorn main:app --reload --port 8001
# or: pip install -e ../shared && pip install -e . && ENVIRONMENT=dev python main.py
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
# .env (gitignored; real values) - see .env.example for full list
ENVIRONMENT=production # default fail-closed; dev must set ENVIRONMENT=dev
APP_SECRET_KEY=<openssl rand -hex 32> # ephemeral random in dev if missing
AUTH0_DOMAIN=your-domain.auth0.com
AUTH0_CLIENT_ID=...
AUTH0_CLIENT_SECRET=...
AUTH0_AUDIENCE=https://your-api-audience
FRONTEND_URL=https://localhost:8443
REDIS_PASSWORD=<openssl rand -hex 32> # -> REDIS_URL=redis://:PASSWORD@redis:6379/0
SESSION_MAX_AGE=1800 # 30m per NIST 800-63B

# Tenant mappings (prefer file, not inline) / OPA bundle (preferred over inline JSON)
TENANT_DATABASES_JSON=[{"org_id":"...","database_id":"default","connection_string":"..."}] # or TENANT_DATABASES_JSON_FILE
# Policies: OPA bundle sql_query_api/opa/policies/gateway.rego + data.json (scripts/build-opa-bundle.sh)
# Legacy inline (deprecated, dev only): POLICY_POLICIES_JSON=[{"id":"allow-album","effect":"allow","table":"album"}]

# AI services
OPENROUTER_API_KEY=...
GEMINI_API_KEY=...
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

This application is designed around a read-only gateway with defense-in-depth:

- **SQL:** only `SELECT`/`WITH` accepted (`clean_sql` rejects non-SELECT, no `SELECT *` synthesis), DDL/DML blocked by `AstSqlAnalyzer`, per-query `statement_timeout`/`lock_timeout` + `SET ROLE gateway_readonly_user` at DB
- **Identity:** Auth0 JWT `RS256` verified (`leeway=2s`, `scope` enforced), tenant claim `https://app.secure-db-access-gateway.org/tenant_id` required, `X-User/Org/Tenant` stripped at `nginx` + `RBACMiddleware` (auth-only, no hardcoded `ALLOWED_ROLES`)
- **Authorization:** OPA bundles (`gateway.rego` `role_hierarchy admin->viewer`, `sod_constraints`, `action` `select` default, deny-precedence, fail-closed on unreachable) or deprecated `POLICY_POLICIES_JSON` fallback (dev only). Every operation resolves `(tenant_id, database_id)` server-side; `database_id="default"` must be explicit per org
- **Network:** `frontend` (`nginx`, `web_app`, `auth0_api`) + `backend` (`sql_query_api`, `opa`, `redis`, `auth0_api`) segmentation, Redis `requirepass`, `otel-lgtm` `127.0.0.1` only, images pinned (`nginx:1.27`, `redis:7.4`, `opa:1.8.0`), `read_only`/`no-new-privileges`
- **Edge:** `nginx` TLS Mozilla intermediate (`ECDHE`+`TLS1.3`, `session_tickets off`, `client_max_body_size 1m`), HSTS `preload`, `CSP` `frame-ancestors none`, `X-Content-Type-Options nosniff`, rate limits `60r/m` burst 20 / `5r/m` burst 3
- **Supply chain:** GitHub Actions SHA-pinned, `bandit`/`pip-audit` (both services, `ecdsa` PYSEC-2026-1325 ignored as upstream out-of-scope), `npm ci --ignore-scripts` + `audit --audit-level=moderate`
- **Audit:** `log_audit_event` for `sql_query`/`policy_denied`/`auth_failed`/`schema_introspection` with `query_hash` (raw SQL only if `AUDIT_LOG_RAW_SQL=true`, off in prod), quarterly review via `docs/runbook-access-review.md`

For full controls see [SECURITY.md](SECURITY.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

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
- [BACKUP_DR.md](BACKUP_DR.md) — tenant database backup/restore procedures and DR objectives
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
