# AGENTS.md

Three independently-installed services plus an nginx TLS edge, wired together by `docker-compose.yml`:

- `web-app/` — React 19 + Vite + TypeScript UI (port 5173)
- `auth0_api/` — FastAPI OAuth2/Auth0 sessions + optional AI greeting (port 8001)
- `sql_query_api/` — FastAPI + Strawberry GraphQL read-only SQL gateway (port 8002)

Backend services each have their own `pyproject.toml` and `.venv` (Python 3.12). Deps are managed with `uv` (`uv.lock`), but CI installs via pip.

## Commands

Run checks from inside the service dir with its venv (e.g. `sql_query_api/.venv/bin/python`):

- SQL API tests: `python -m pytest` — hermetic, uses aiosqlite (`tests/conftest.py` sets `TENANT_DATABASES_JSON`), no DB or service required.
- Auth0 API tests: `python -m pytest`.
- Web app: `npm run lint` (eslint), `npm test` (typecheck only via `tsc -b` — there are NO unit tests), `npm run build`, `npm run test:e2e` (Playwright, auto-starts the Vite dev server; only real browser suite).
- CI (`.github/workflows/ci.yml`) = SQL pytest + bandit + pip-audit, auth0 pytest, web npm audit + lint + typecheck + e2e + build. Security gates: bandit + pip-audit on `sql_query_api`; npm audit on `web-app`.

## Ruff is diff-aware only

- `sql_query_api/.pre-commit-config.yaml` uses a custom hook that lints only changed lines of staged files: `python .pre-commit-scripts/ruff-diff-check.py`.
- Full `ruff check .` reports 600+ pre-existing errors and is NOT part of CI. Don't try to make the whole repo ruff-clean; keep new and edited lines compliant.

## Commit review gate

- A blocking pre-commit hook runs an opencode code-review before every commit
  (source: `.githooks/pre-commit`, installed to `.git/hooks/pre-commit`).
  It runs `opencode run --command code-review -m ${REVIEW_MODEL:-opencode/muse-spark-1.2-contributor-free}`
  (skill: `.opencode/skills/code-review/`, read-only agent: `.opencode/agent/code-reviewer.md`)
  against the staged diff and requires a y/N approval when run interactively;
  in non-interactive contexts the review runs, its report is printed, and the
  commit proceeds. Override the review model with `REVIEW_MODEL=` (e.g. `opencode/big-pickle`).
- Toggle to advisory with `BLOCK=false` in `.githooks/pre-commit`; uninstall with
  `rm .git/hooks/pre-commit`.

## Env & config gotchas

- `ENVIRONMENT` defaults to `production` in `sql_query_api/main.py`. In production (and not `CI`), startup fails fast if `POLICY_POLICIES_JSON` or `POLICY_POLICIES_JSON_FILE` is missing (`services/policy_engine.py`). For local dev runs set `ENVIRONMENT=dev` (also enables uvicorn reload).
- `TENANT_DATABASES_JSON` or `TENANT_DATABASES_JSON_FILE` is required — there is no single-database fallback. It maps `org_id`/`database_id` → connection strings; clients send only a logical `database_id`, never connection strings.
- Every token must carry the trusted tenant claim `https://app.secure-db-access-gateway.org/tenant_id`; RBAC middleware enforces it.
- Secrets come only from env vars (or a `*_FILE` path injected by an orchestrator). Real values live in a single gitignored env file — `.env` for dev (copied from `.env.example` by `scripts/bootstrap-dev.sh`), `/etc/gateway/gateway.env` on a host (manual provisioning) — and are read via the shared `read_secret` loader. There is no `secrets/` directory and no encrypted secret files. Never hardcode credentials.

## Don't regress these design constraints

- `sql_query_api` is strictly SELECT-only. Any new query path must go through the governed pipeline (safety/AST validation, tenant resolution, auto-LIMIT, read-only transaction flags, masking, audit) — see `services/query_gateway.py` and `middlewares/rbac_middleware.py`.
- Auth is httpOnly-cookie JWT; `localStorage` may hold only the `app_jwt_exists` flag. Don't add JS-visible token storage.
- Root `explore.py` is a local headless operator CLI that bypasses the multi-tenant gateway (it re-execs inside `sql_query_api/.venv` and requires a validated access token). It is NOT a governed access path.

Deeper context: `ARCHITECTURE.md`, `SECURITY.md`, `GEMINI.md` (AI/CLI guardrails), per-service `README.md`.