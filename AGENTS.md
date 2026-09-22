# AGENTS.md

Three independently-installed services plus an nginx TLS edge, wired together by `docker-compose.yml`:

- `web-app/` — React 19 + Vite + TypeScript UI (port 5173)
- `auth0_api/` — FastAPI OAuth2/Auth0 sessions + optional AI greeting (port 8001)
- `sql_query_api/` — FastAPI + Strawberry GraphQL read-only SQL gateway (port 8002)

Backend services each have their own `pyproject.toml` and `.venv` (Python 3.12). Deps are managed with `uv` (`uv.lock`), but CI installs via pip. A shared `shared/` package (`shared_secrets`) provides the `read_secret` loader and must be installed first.

## Local dev setup

```bash
./scripts/bootstrap-dev.sh   # copies .env.example → .env, generates mkcert TLS cert
docker compose up --build     # starts all services + nginx + otel-lgtm
```

Or run individual services directly:

```bash
cd sql_query_api && uv sync && uv run uvicorn main:app --reload --port 8002
cd auth0_api && uv sync && uv run uvicorn main:app --reload --port 8001
cd web-app && npm install && npm run dev
```

## Commands

Run checks from inside the service dir with its venv (e.g. `sql_query_api/.venv/bin/python`):

- SQL API tests: `python -m pytest` — hermetic, uses aiosqlite (`tests/conftest.py` sets `TENANT_DATABASES_JSON`), no external DB or services required.
- Auth0 API tests: `python -m pytest`.
- Web app: `npm run lint` (eslint), `npm test` (typecheck only via `tsc -b` — there are NO unit tests), `npm run build`, `npm run test:e2e` (Playwright, auto-starts the Vite dev server; only real browser suite).
- Shared secrets smoke test (CI only): `python -m pip install -e ./shared` then run inline assertions.
- CI (`.github/workflows/ci.yml`) = secret-scan (gitleaks) + shared-secrets smoke + runbook lint + SQL pytest + bandit + pip-audit, auth0 pytest, web npm audit + lint + typecheck + e2e + build. Security gates: bandit + pip-audit on `sql_query_api`; npm audit on `web-app`.
- Web app Node version: 20. Python services: 3.12.

## Ruff is diff-aware only

- `sql_query_api/.pre-commit-config.yaml` uses a custom hook that lints only changed lines of staged files: `python .pre-commit-scripts/ruff-diff-check.py`.
- Full `ruff check .` reports 600+ pre-existing errors and is NOT part of CI. Don't try to make the whole repo ruff-clean; keep new and edited lines compliant.

## Commit review gate

- A blocking pre-commit hook runs an opencode code-review before every commit (source: `.githooks/pre-commit`, installed to `.git/hooks/pre-commit`). It runs `opencode run --command code-review -m ${REVIEW_MODEL:-opencode/muse-spark-1.2-contributor-free}` (skill: `.opencode/skills/code-review/`, read-only agent: `.opencode/agent/code-reviewer.md`) against the staged diff and requires a y/N approval when run interactively; in non-interactive contexts the review runs, its report is printed, and the commit proceeds. Override the review model with `REVIEW_MODEL=` (e.g. `opencode/big-pickle`).
- Toggle to advisory with `BLOCK=false` in `.githooks/pre-commit`; uninstall with `rm .git/hooks/pre-commit`.

## Env & config gotchas

- `ENVIRONMENT` defaults to `production` in `sql_query_api/main.py`. In production (and not `CI`), startup fails fast if `POLICY_POLICIES_JSON` or `POLICY_POLICIES_JSON_FILE` is missing (`services/policy_engine.py`). For local dev runs set `ENVIRONMENT=dev` (also enables uvicorn reload).
- `TENANT_DATABASES_JSON` or `TENANT_DATABASES_JSON_FILE` is required — there is no single-database fallback. It maps `org_id`/`database_id` → connection strings; clients send only a logical `database_id`, never connection strings.
- Every token must carry the trusted tenant claim `https://app.secure-db-access-gateway.org/tenant_id`; RBAC middleware enforces it.
- Secrets come only from env vars (or a `*_FILE` path injected by an orchestrator). The `shared_secrets.read_secret` loader checks: env var → `*_FILE` path → default. Real values live in a single gitignored env file — `.env` for dev (copied from `.env.example` by `scripts/bootstrap-dev.sh`), `/etc/gateway/gateway.env` on a host (manual provisioning). There is no `secrets/` directory and no encrypted secret files. Never hardcode credentials.

### SQL safety & query execution

- `sql_query_api` is strictly SELECT-only. Any new query path must go through the governed pipeline (safety/AST validation, tenant resolution, auto-LIMIT, read-only transaction flags, masking, audit) — see `services/query_gateway.py` and `middlewares/rbac_middleware.py`.
- `clean_sql()` in `sql_cleaner.py` may prepend `SELECT * ` if the cleaned query doesn't start with SELECT but contains FROM/JOIN — beware widening scans.
- The GraphQL `SqlStatementRequest.database_id` defaults to `"default"` — verify this is a valid database_id for the org; otherwise tenant resolution may succeed unexpectedly.
- Cost threshold defaults to score 16 → deny (`SQL_QUERY_COST_THRESHOLD`); row limit default 5000 (`SQL_QUERY_MAX_ROW_LIMIT`); result size limit default 5MB (`SQL_QUERY_MAX_RESULT_BYTES`); query timeout default 30s (`SQL_QUERY_TIMEOUT_SECONDS`).
- GraphQL introspection (`__schema`/`__type`) is disabled in production via `DisableIntrospection` extension; always-on in dev.
- `EXPOSE_DB_ERROR_DETAIL=1` env var allows detailed DB error names/columns to reach GraphQL callers; default is `0` (generic messages only).
- `AUDIT_LOG_RAW_SQL=true` includes the executed SQL in audit payloads; disable in production to avoid log leakage.
- `SQL_READONLY_ROLE` must be configured in production PostgreSQL tenants; enforces `SET ROLE` to a dedicated SELECT-only role at connection time.

### Nginx / Docker

- Rate limits: `api_limit` (60r/m burst 20), `auth_limit` (5r/m burst 3 for login/auth/logout). IP-based only; no per-user granularity.
- `/nginx-health` on port 80 (plaintext) is the only open HTTP endpoint; it just returns `OK`.
- HSTS, X-Content-Type-Options nosniff, X-Frame-Options DENY, Strict-Transport-Security all enforced.
- Correlation ID headers (`X-Correlation-ID`, `X-Request-ID`) are charset/length-validated at the nginx boundary; spoofable headers (`X-User-*`, `X-Org-*`, `X-Tenant-*`) are stripped by both nginx and RBAC middleware.

### DoS / resource exhaustion

- GraphQL depth limiter: max depth 6; alias limiter: max 100 aliases.
- Introspection disabled in production — but `get_table_schema` and `introspect_schema` remain available to authenticated users; no per-query rate limiting beyond nginx IP limits.
- Query cost, timeout, and row/byte limits are the primary DB-enforced DoS guards.

### Audit logging

- `log_audit_event` is called for: `sql_query`, `schema_embedding_lookup`, `schema_introspection`, `auth_failed`, `tenant_resolution_failed`, `sql_query_started`, `sql_validation_failed`, `simulate_policy_evaluated`.
- Audit payload includes `user`, `org_id`, `database_id`, `query_hash`, `tables_touched`, `reason`, `decision_allowed`, `decision_reason`.
- Raw SQL included in audit when `AUDIT_LOG_RAW_SQL=true` — disable in production.
- Policy denies and `permission_error` events may not all be logged — verify coverage if audit compliance is required.

### Dependency / supply-chain risk

- Python deps managed with `uv`/`uv.lock`; CI runs `pip-audit` and `bandit` on `sql_query_api`.
- Node deps audited via `npm audit` in web-app CI.
- Each service has its own `.venv` and `pyproject.toml`; no shared runtime deps beyond auth0 pyramid.

### Observability

- `docker compose up` includes an `otel-lgtm` sidecar (Grafana OTel LGTM stack) on ports 3000 (UI), 4317/4318 (OTLP gRPC/HTTP), 9090 (Prometheus). All backend services depend on it.

### Open Policy Agent (OPA)

- OPA sidecar (`openpolicyagent/opa:latest`) runs on port 8181 for centralized policy evaluation.
- Toggle between OPA and built-in evaluator via `OPA_ENABLED` env var (default: `true` when `OPA_URL` is set).
- OPA policy files are mounted from `sql_query_api/opa/policies/` and evaluated at `/v1/data/gateway/evaluate`.
- When OPA is unreachable, the evaluator **fails closed** (denies all requests).
- The `OpaPolicyEvaluator` class implements the same interface as `PolicyEvaluator` for seamless switching.
- Rego policies in `sql_query_api/opa/policies/gateway.rego` define allow/deny rules based on principal, org, database, table, and column attributes.
- For production, configure OPA bundle loading (S3/GCS/HTTP) for hot-reload of policies without restarts.

### What to never regress

- `sql_query_api` is strictly SELECT-only through the governed pipeline.
- Auth is httpOnly-cookie JWT; `localStorage` may hold only the `app_jwt_exists` flag.
- Root `explore.py` is a local headless operator CLI that bypasses the multi-tenant gateway (it re-execs inside `sql_query_api/.venv` and requires a validated access token). It is NOT a governed access path.
- `TENANT_DATABASES_JSON` is required at startup — no fallback.
- `ENVIRONMENT=dev` is needed for local development; production defaults to strict enforcement.
- OPA integration must fail closed: when `OPA_URL` is set but unreachable, all queries are denied.

### Deployment

- Production uses `docker-compose.prod.yml` override (ports 80/443 instead of 8080/8443, pre-built GHCR images).
- `deploy.yml` triggers on `v*` tag push (builds release images) or `workflow_dispatch` (deploys existing tag for rollback).
- `docker.yml` builds multi-arch (amd64/arm64) images to GHCR on push to main/master/feat/*.

Deeper context: `ARCHITECTURE.md`, `SECURITY.md`, `GEMINI.md` (AI/CLI guardrails), per-service `README.md`.