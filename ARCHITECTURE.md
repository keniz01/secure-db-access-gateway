# Solution Architecture

This document provides a detailed overview of the system architecture, component interactions, and data flow for the Secure DB Access Gateway application.

## Overview

The Secure DB Access Gateway is designed with a microservices-inspired architecture that separates concern across distinct service layers:
- **Frontend (Web Application):** React 19 + TypeScript single-page application.
- **Auth0 API:** FastAPI authentication service handling OAuth2 authentication, user sessions, and AI greetings.
- **SQL Query API:** FastAPI service running Strawberry GraphQL, SQLAlchemy, and async pg for read-only database query execution.
- **Reverse Proxy / Gateway:** Nginx gateway managing route proxying and rate limiting.

---

## Service Architecture

### 1. Web Application (`web-app/`)

- **Tech Stack:** React 19, TypeScript, Vite, Tailwind CSS 4, TanStack Query.
- **Port:** `5173` (development)

#### Key Features & Components
- `LoginPage`: Initial login interface with Auth0 integration.
- `AuthCallback`: Handles OAuth2 authorization code callback.
- `Dashboard`: Protected main user interface displaying user profile, AI greetings, and query interface.

#### Token Management & Session State
- **JWT Storage:** Tokens are issued and set by the Auth0 API as `httpOnly`, `Secure`, `SameSite` cookies (not exposed to JavaScript or stored as JWT strings in `localStorage`).
- **localStorage Usage:** `localStorage` stores only a boolean flag (`app_jwt_exists`) to indicate token presence for frontend UI state management.
- **API Client:** `axios` configured with `withCredentials: true` and `X-Requested-With: XMLHttpRequest` headers to send `httpOnly` cookies automatically on requests.

---

### 2. Auth0 API (`auth0_api/`)

- **Tech Stack:** Python 3.12+, FastAPI, Auth0 OAuth2, Azure OpenAI, Pydantic.
- **Port:** `8001`

#### Core Responsibilities
- Auth0 OAuth2 integration and token exchange.
- Setting `httpOnly` session cookies on authentication.
- Providing user profile and health endpoints (`/api/auth/me`, `/api/health`).
- AI service integration for personalized user greeting generation.

---

### 3. SQL Query API (`sql_query_api/`)

- **Tech Stack:** Python 3.12+, FastAPI, Strawberry GraphQL, SQLAlchemy (asyncpg), PostgreSQL.
- **Port:** `8002`

#### Core Responsibilities
- GraphQL query interface (`/graphql`).
- One governed query gateway shared by GraphQL, AI/text-to-SQL, and the headless CLI.
- Read-only SQL safety enforcement (`DefaultSqlSafetyChecker` preventing non-SELECT operations).
- Automatic `LIMIT` clause injection and input validation.
- Connection management enforcing read-only at the session level (`SET SESSION
  CHARACTERISTICS AS TRANSACTION READ ONLY`) plus `SET ROLE` into a dedicated,
  database-enforced SELECT-only role (`SQL_READONLY_ROLE`).

---

## Data Flow

### 1. Authentication Flow

```
User Browser
    │
    ├─→ GET /login (Web App)
    │
    ├─→ Redirects to Auth0
    │   │
    │   └─→ User authenticates
    │
    ├─→ Auth0 redirects to /auth callback
    │
    ├─→ POST /api/auth (Auth0 API)
    │   │
    │   ├─→ Exchange code for token
    │   ├─→ Validate token
    │   └─→ Set httpOnly cookie with JWT
    │
    └─→ Redirected to Dashboard (Web App)
```

### 2. Query Execution Flow

```
Dashboard
    │
    ├─→ User submits SQL query
    │
    ├─→ POST /graphql (SQL Query API)
    │   │
    │   ├─→ Validate SQL safety (SELECT-only check)
    │   ├─→ Apply automatic LIMIT
    │   ├─→ GovernedQueryGateway
    │   │   └─→ Execute on PostgreSQL (READ ONLY mode)
    │   └─→ Return results as JSON
    │
    └─→ Display results in UI table
```

### 3. Token Management & API Requests

```
API Request (Web App)
    │
    ├─→ Axios client (withCredentials: true)
    ├─→ Browser includes httpOnly JWT cookie automatically
    ├─→ Checks localStorage app_jwt_exists flag for UI session guard
    │
    └─→ API receives authenticated request
```

---

## Security Architecture

### Authentication & Authorization
- **OAuth2 with Auth0:** Enterprise authentication provider.
- **httpOnly Cookies:** Prevents token extraction via XSS; raw JWT is never stored in `localStorage`.
- **Session Flag:** `localStorage` stores only `app_jwt_exists` status indicator.

### API & Database Security
- **Strict Read-Only Enforcement:** Only `SELECT` statements are executed (governed pipeline `services/query_gateway.py`).
- **Connection Flags:** `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` + `SET ROLE sql_readonly_role` (see `SECURITY.md` least-privilege).
- **Query Safety:** AST validation (`sql_safety_checker.py`), parameterized queries via SQLAlchemy; auto-LIMIT, cost/timeout/byte guards.
- **CORS & Headers:** whitelisted `CORS_ORIGINS` (shared with CSRF `csrf.py`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy` (frame-ancestors none), `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-*`, `Strict-Transport-Security: preload` (nginx + FastAPI, authoritative at `nginx/nginx.conf:102`).

### Policy Enforcement (OPA Integration)

The gateway supports centralized policy enforcement via Open Policy Agent (OPA):

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  SQL Query  │──────│     OPA     │──────│   Rego      │
│    API      │      │   Sidecar   │      │  Policies   │
└─────────────┘      └─────────────┘      └─────────────┘
       │
       ▼
┌─────────────┐
│  PostgreSQL │
└─────────────┘
```

**Configuration:**
- `OPA_URL=http://opa:8181` — OPA sidecar endpoint
- `OPA_ENABLED=false` by default in `docker-compose.yml:92` and `.env.example:126`; `docker-compose.prod.yml` sets `true`. Set `OPA_ENABLED=true` to delegate to OPA
- Prod bundle: `BUNDLE_SERVICE_URL=http://opa-bundle-server:8080` + `sql_query_api/opa/config.yaml` (`bundles.gateway.resource: bundle.tar.gz`, polling 10-20s)

**Policy Evaluation:**
- OPA evaluates allow/deny decisions based on: principal (user_id, org_id, roles, `attributes`), `action` (`select` default), `database_id`, `tables`, and `referenced_columns`
- RBAC1 hierarchy `admin->viewer` via `role_hierarchy` + `expanded_roles`, static SoD `sod_constraints` (empty=disabled, deny-first in `evaluate` + `effective_access`)
- Deny policies always take precedence over allow policies
- When OPA is unreachable, the evaluator **fails closed** (denies all requests)

**Rego Policies:**
- Located in `sql_query_api/opa/policies/gateway.rego`, data in `sql_query_api/opa/data.json` (`{policies: [...]}`)
- Dev: loaded from disk on OPA startup (file mount `docker-compose.yml:112`)
- Prod: bundle `scripts/build-opa-bundle.sh` (`opa build -b sql_query_api/opa -o sql_query_api/bundle.tar.gz`) served via `opa-bundle-server` or S3/GCS/HTTP; hot-reload without restart
- Validate: `opa fmt --fail`, `opa test ./sql_query_api/opa/policies`, `opa build` in CI

**Fallback (deprecated):**
- When `OPA_ENABLED=false` or `OPA_URL` is not set, the in-process `PolicyEvaluator` (`services/policy_engine.py:259` `POLICY_POLICIES_JSON`) is used — dev/CI only, emits `DeprecationWarning`
- The `OpaPolicyEvaluator` (`services/opa_policy_engine.py:52`) implements the same interface as `PolicyEvaluator` for seamless switching

---

## Deployment Architecture

```
localhost:5173      → Web App (Vite dev server; http://localhost:5173 only, proxied via TLS edge)
localhost:8080      → Nginx Gateway (HTTP → HTTPS redirect; /nginx-health plaintext)
localhost:8443      → Nginx Gateway (TLS edge; serves SPA + /api/*, Mozilla intermediate, HSTS preload)
localhost:8001      → Auth0 API (via nginx, not host-exposed in prod compose)
localhost:8002      → SQL Query API (via auth0_api BFF, not host-exposed)
opa:8181            → OPA Sidecar (expose only, not host-mapped; docker-compose.yml:108)
127.0.0.1:3000      → Grafana UI (otel-lgtm, 127.0.0.1-only in compose)
localhost:4318      → OTLP HTTP receiver
host.docker.internal:5432 → PostgreSQL (external, not a compose service; see .env.example DATABASE_URL)
  + redis:6379      → Redis (session store, docker-compose.yml:7)
```

**Network segmentation** (`docker-compose.yml:172`):
- `frontend` (`gateway-frontend`) — `nginx`, `web_app`, `auth0_api`, `otel-lgtm` (ports exposed)
- `backend` (`gateway-backend`, `internal: true`) — `sql_query_api`, `opa`, `redis`, `auth0_api`, `otel-lgtm`
- `auth0_api` bridges both so `web_app` cannot reach `sql_query_api:8002`/`opa:8181`/`redis:6379` even if compromised; `nginx` no longer depends on `sql_query_api` directly (`sql_query_api` is private to BFF).

Containerized deployment is defined in `docker-compose.yml` and documented in `DOCKER_README.md`.

### Observability & APM

The project uses a self-hosted Grafana `otel-lgtm` stack instead of a paid SaaS telemetry provider. All application services emit OTLP traces and structured logs, and the request correlation IDs are attached to spans so slow/failed queries can be traced end-to-end in Grafana.

Operational settings are intentionally environment-driven:

- `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-lgtm:4318`
- `OTEL_SERVICE_NAME=<service-name>`
- `X-Correlation-ID` propagates through middleware and is attached to spans/logs

This keeps the stack vendor-neutral while still giving Grafana/Loki/Tempo visibility into errors, latency, and query failures.

### Async PostgreSQL Pooling & Backpressure

The SQL Query API configures the async SQLAlchemy engine with explicit pool limits so the app degrades predictably under concurrent load instead of opening an unbounded number of database connections.

Recommended defaults:

- `DB_POOL_SIZE=5`
- `DB_MAX_OVERFLOW=10`
- `DB_POOL_TIMEOUT_SECONDS=30`
- `DB_POOL_RECYCLE_SECONDS=1800`

These values keep the database connection footprint bounded while allowing short bursts of load as the pool backs up. The code sets `pool_pre_ping=True` to detect stale connections, and the application-level `SQL_QUERY_TIMEOUT_SECONDS` still acts as a safety valve if the database becomes slow or overloaded.

Connection accounting: the pool is **per engine**, and each tenant database gets its
own engine — with primary and read-replica targets as separate engines. The
steady-state connections a tenant holds to a cluster are therefore
`DB_POOL_SIZE + DB_MAX_OVERFLOW` per engine; the total across all engines must
fit under the gateway role's `CONNECTION LIMIT` (provisioned via
`gateway_connection_limit`). Pool settings are validated at startup
(`pool_settings_from_env`), so production fails fast on a non-positive pool
size/timeout instead of degrading at runtime.

### Database-side resource controls

The database itself enforces per-session budgets for the gateway role, so even a
direct login as `gateway_readonly_user` (bypassing the app entirely) is a
time- and memory-bounded read-only client. `setup_least_privilege_gateway_role.sql`
attaches these as role defaults (tunable via psql variables):

| Setting | Default | Purpose |
| --- | --- | --- |
| `default_transaction_read_only` | `on` | reject any write at the engine |
| `statement_timeout` | `30s` | kill runaway statements (keep `>= SQL_QUERY_TIMEOUT_SECONDS`) |
| `lock_timeout` | `5s` | bound lock-wait time (mirrors `SQL_LOCK_TIMEOUT_SECONDS`) |
| `idle_in_transaction_session_timeout` | `10s` | reclaim sessions parked in an open transaction |
| `work_mem` | `4MB` | bound per-sort/hash memory per operation |
| `max_parallel_workers_per_gather` | `0` | bound parallel-query memory for a predictable footprint |

The provisioning script also enforces a `CONNECTION LIMIT` on the role
(`gateway_connection_limit`, default 20), and its verification block re-checks
every GUC and the connection limit so a misprovisioned role is never left in
place.

Cluster-level review (operator-owned, not scripted because it affects all roles):
`max_connections`, `shared_buffers`, `effective_cache_size`,
`maintenance_work_mem`, and `huge_pages` should fit the host's RAM and the
expected tenant count. Every gateway connection consumes one backend, so
`max_connections` must exceed the sum of all role connection limits plus
headroom for admin/backup connections.

The operator probe (`scripts/probe-production.sh` P5) asserts, for every tenant,
that `statement_timeout`, `lock_timeout`, and
`idle_in_transaction_session_timeout` are non-zero and that the role carries a
connection limit — a target that has not been provisioned fails the probe.
