# Docker Setup

This project runs the application and its tenant PostgreSQL database in Docker
Compose. PostgreSQL is available to host-side tools on loopback port `55432`
and to backend containers at `postgres:5432`.

## Prerequisites

- Docker and Docker Compose installed
- Docker Desktop running (on macOS)

## Quick Start

1. **Bootstrap local env + TLS certs** (first time only):
   ```bash
   ./scripts/bootstrap-dev.sh
   # Creates .env from .env.example (never overwrites) and generates an
   # mkcert-signed TLS certificate into certs/ (requires brew install mkcert).
   ```
2. **Fill in real values in `.env`** — Auth0 credentials, `APP_SECRET_KEY`
   (generate with `openssl rand -hex 32`), AI keys, tenant mapping and policy
   JSON (single line each). Re-run bootstrap after tenant mapping changes.
   See `.env.example` for details.

3. **Run the application stack**:
   ```bash
   docker compose up --build
   ```

This will start:
- **Nginx** (reverse proxy / TLS edge) on ports 8080 (HTTP→HTTPS redirect) and 8443 (HTTPS) – serves SPA and proxies `/api/*` to Auth0 API
- Auth0 API on port 8001 (reachable via nginx at https://localhost:8443/api; BFF proxies `/api/graphql` to SQL Query API)
- SQL Query API on port 8002 (internal, via auth0_api)
- Web App on port 5173 (dev, via nginx; not host-exposed in prod `docker-compose.prod.yml:43`)
- Redis on 6379 (expose, session store), OPA on 8181 (expose), otel-lgtm on 3000/4317/4318/9090
- PostgreSQL 16 with pgvector on `127.0.0.1:55432` (host tools only; internal service name `postgres`)

PostgreSQL uses a named Docker volume, PostgreSQL 16, and pgvector 0.8.0. The
published host port is bound to `127.0.0.1` only; production Compose removes
that port mapping. Host-side tools use `DATABASE_URL` at `localhost:55432`,
while the tenant mapping mounted at `/config/tenant_databases.json` uses the
Docker service hostname `postgres`.

The local bootstrap creates `secrets/tenant_databases.local.json` from the
tenant IDs and database IDs in `.env`, configures it for rotated, read-only
`analytics` credentials, and points Compose at that ignored file. Shared
deployments continue to use `config/tenant_databases.json` or an explicitly
configured `TENANT_DATABASES_CONFIG_SOURCE`.

The local migration archive includes only the `music` tables except
`recording_artist`, plus `meta.schema_embeddings`. Since selected tables have
foreign keys referencing the excluded table, only those two foreign keys are
omitted; all other table constraints and data are restored. Migration backups
are kept outside the repository with owner-only permissions.

**TLS**: `scripts/bootstrap-dev.sh` generates a certificate signed by the
mkcert local CA (`certs/web_tls_cert.pem` / `certs/web_tls_key.pem`). The first
bootstrap run installs that CA into the OS trust store, so the browser accepts
`https://localhost:8443` with no warning. For production, replace the cert with
a trusted CA certificate (or ACME). Because HTTPS is enforced, the browser
stores session cookies with the `Secure` flag.

## Services

### Nginx (Reverse Proxy / TLS edge)
- **Image**: nginx:alpine
- **Ports**: 8080 (HTTP, redirects to HTTPS), 8443 (HTTPS)
- **Role**: Terminates TLS, serves SPA (`/` → `web_app:5173`), proxies `/api/*` → `auth0_api:8001`; auth0_api BFF then proxies `/api/graphql` → `sql_query_api:8002`
- **Config**: `./nginx/nginx.conf` (HSTS preload, CSP, CORS, rate limits `auth_limit 5r/m`, `api_limit 60r/m`, correlation IDs)
- **TLS certs**: bind-mounted from `certs/web_tls_cert.pem` and `certs/web_tls_key.pem`

### Auth0 API
- **Build**: context `.` / `auth0_api/Dockerfile`
- **Port**: 8001
- **Secrets**: Auth0, AI, and session-signing credentials injected as environment variables from `.env` (`env_file`)

### SQL Query API
- **Build**: context `.` / `sql_query_api/Dockerfile`
- **Port**: 8002
- **Configuration**: Rotated tenant database mappings mounted from
  `TENANT_DATABASES_CONFIG_SOURCE`; access policies are injected as
  `POLICY_POLICIES_JSON` from `.env`

### Web App
- **Build**: ./web-app
- **Port**: 5173 (dev `docker-compose.yml:22` host-mapped; prod `docker-compose.prod.yml:43` has `ports: !override []` — served only via nginx :443)
- **Environment**: `VITE_API_BASE_URL=https://localhost:8443` (dev) / `https://app.secure-db-access-gateway.org` (prod)

### Redis
- **Image**: `redis:7-alpine` on `6379` (expose)
- **Role**: Shared session store (`REDIS_URL=redis://redis:6379/0`, mandatory in prod fail-closed `session_store.py:63`)

### OPA
- **Image**: `openpolicyagent/opa:latest` on `8181` (expose, not host-mapped)
- **Role**: Policy bundle evaluation at `/v1/data/gateway/evaluate`, fail-closed when unreachable

### otel-lgtm
- **Image**: `grafana/otel-lgtm` on `3000`/`4317`/`4318`/`9090` (Grafana + OTLP)

## Secrets Management

No secret values are committed to the repository. All credentials live in a single
env file injected by Compose:

- **Dev**: `.env` (created from `.env.example` by `scripts/bootstrap-dev.sh`)
- **Production**: `/etc/gateway/gateway.env` (provisioned manually, `chmod 600`)
- Compose injects it into `auth0_api` and `sql_query_api` via `env_file`
  (override the path with `GATEWAY_ENV_FILE`)
- `scripts/bootstrap-dev.sh` creates the gitignored
  `secrets/pg_rotator_admin_pass.txt` if it is missing, with mode `0600`.
  `PG_ROTATOR_ADMIN_PASS_FILE` overrides its path; existing secret files are
  never overwritten.
- Both services read values with the shared `read_secret` loader
  (`shared/shared_secrets`), which checks the env var first and then an
  optional `NAME_FILE` path for orchestrators that mount secrets as files
- In `ENVIRONMENT=production` the services fail fast if a required secret is
  missing, so a misconfigured deployment aborts at startup

### Editing a secret (dev)

```bash
# values are plaintext in .env — just edit and restart:
docker compose up -d
```

### Sending secrets to production

```bash
# Source of truth: your password manager / secret manager.
# Copy the env file to the host:
sudo install -m 600 -o deploy -g deploy gateway.env /etc/gateway/gateway.env
docker compose --env-file /etc/gateway/gateway.env up -d --build
```

Keep a copy of the env file in a password manager — the host file is a copy,
not the backup.

## Development

For development, you can edit `.env` and rebuild:

```bash
docker compose down
docker compose up --build
```

## Production

### Infrastructure model

- **Images never build on the host.** `.github/workflows/docker.yml` and
  `deploy.yml` call a SHA-pinned reusable builder, which pushes images by digest, signs and verifies each
  digest, then promotes public tags; the host only pulls verified tags.
- **Secrets never enter CI.** `/etc/gateway/gateway.env` (or any
  `GATEWAY_ENV_FILE`) is provisioned on the host and injected via Compose
  `env_file`. The deploy workflow only references the *path*.
- **Readiness gate.** `ENVIRONMENT=production` fails fast on missing secrets,
  each service has a `healthcheck`, and Compose `up --wait` aborts the deploy
  if any container does not become healthy within `--wait-timeout`.
- **Stable image tags.** Release images are tagged `vX.Y.Z` (semver), `latest`,
  and `sha-<commit>`. `edge` tracks the default branch. Rollback = re-pin an
  older tag.

### One-time production host provisioning

1. Host basics:
   - Docker (Compose v2, or `docker-compose` plugin) and a deploy user in the
     `docker` group. The production Compose stack includes PostgreSQL; plan
     database backups and volume recovery independently of application images.
   - A checkout of this repo at e.g. `/opt/secure-db-access-gateway` writable
     by the deploy user (the workflow `git fetch` + `git checkout` it).
   - TLS certs in `certs/` on the host: either run
     `./scripts/bootstrap-dev.sh` once (mkcert + installs the local CA) or drop
     in trusted CA certificates / ACME material. `certs/` is gitignored and is
     bind-mounted into nginx.
   - **Database least privilege (required).** For every tenant database, run
     `sql_query_api/scripts/setup_least_privilege_gateway_role.sql` as a
     PostgreSQL superuser to create the dedicated SELECT-only
     `gateway_readonly_user` role (single purpose: `CONNECT` on the database,
     `USAGE` + `SELECT` on the data/metadata schemas, no ownership/DDL/DML,
     `default_transaction_read_only=on`; fail-closed and idempotent). The same
     script attaches the database-side resource controls (statement/lock/
     idle-in-transaction timeouts, `work_mem`, a `CONNECTION LIMIT`) — tune via
     `-v gateway_statement_timeout=30s ... -v gateway_connection_limit=20` and
     keep `gateway_statement_timeout >= SQL_QUERY_TIMEOUT_SECONDS`. Use that
     role's credentials in `TENANT_DATABASES_JSON` and set
     `SQL_READONLY_ROLE=gateway_readonly_user` in the env file — the gateway
     fails fast in production if it is missing.
2. Secrets file: `sudo install -m 600 -o deploy -g deploy gateway.env /etc/gateway/gateway.env`
   (copy to the host from your secret manager — the host file is a copy, not
   the backup).
3. GitHub settings needed by the deploy workflow:
   - **Repository secrets** (Actions → Settings → Secrets):
     - `DEPLOY_HOST` — hostname/IP of the production host.
     - `DEPLOY_USER` — SSH user with access to `DEPLOY_DIR` and docker.
     - `DEPLOY_SSH_KEY` — private key (Ed25519) of a dedicated deploy keypair;
       the public key goes in the deploy user's `~/.ssh/authorized_keys`.
     - `CR_PAT` — GitHub PAT with **`read:packages`** scope; the host uses it
       to `docker login ghcr.io`.
     - Optional: `DEPLOY_PORT` (default 22), `DEPLOY_DIR`
       (default `/opt/secure-db-access-gateway`), `GHCR_USER` (token owner,
       defaults to `DEPLOY_USER`), `GATEWAY_ENV_FILE`
       (default `/etc/gateway/gateway.env`).
   - **Repository variables** (optional): `VITE_API_BASE_URL` and
     `VITE_SQL_GRAPHQL_BASE_URL` if the SPA origin differs from
     `https://app.secure-db-access-gateway.org`.

### Deploy and roll back

| Action | How |
| --- | --- |
| Release | Push a tag: `git tag v1.2.3 && git push origin v1.2.3` |
| Re-deploy / roll back | GitHub Actions → **Deploy to Production** → *Run workflow* → set `image_tag` to a **published** GHCR tag (e.g. the previous `v1.2.2` to roll back). Leave blank to re-deploy `edge`. |
| Ad-hoc build | **Build and Push Images** → *Run workflow* → optional `tag` input |

The deploy job pulls the pinned tag and runs
`docker compose ... up -d --no-build --wait --wait-timeout 300`; the run fails
if any container does not become healthy. Because Compose recreates containers
with the new image before health is confirmed, roll back promptly via
`image_tag` = previous tag (see table above).

Sanity-check a rollout:
```bash
docker compose --env-file /etc/gateway/gateway.env -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose --env-file /etc/gateway/gateway.env logs --tail=50 nginx web_app auth0_api sql_query_api
```

## Troubleshooting

### Check container logs
```bash
docker compose logs [service_name]
```

### Restart services
```bash
docker compose restart [service_name]
```

### "Required secret ... is not set" on startup
Your `.env`/`gateway.env` is missing a value listed in `.env.example`. Fill it
in and `docker compose up -d`. This guard intentionally fails fast when
`ENVIRONMENT=production`.

### Clean rebuild
```bash
docker compose down -v
docker compose up --build
```

## Architecture

The Docker setup creates a complete development environment with:

- **Nginx reverse proxy / TLS edge** – serves SPA and proxies `https://localhost:8443/api` to auth0_api; auth0_api BFF holds JWT server-side and proxies GraphQL to sql_query_api
- **Backend APIs** with proper networking (PostgreSQL is on the private Compose backend network)
- **Redis + OPA + otel-lgtm** sidecars
- **Frontend** served with hot reload (dev) / via nginx (prod)
- Env-file secret management
- Health checks for all services

### Request flow (Auth API)

```
Browser (localhost:8443, TLS) → nginx (proxies SPA to web_app:5173, /api to auth0_api) → auth0_api (internal:8001) → sql_query_api (internal:8002)
```
