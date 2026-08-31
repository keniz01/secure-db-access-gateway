# Solution Architecture

This document provides a detailed overview of the system architecture, component interactions, and data flow for the Read-Only Database Explorer application.

## Overview

The Read-Only Database Explorer is designed with a microservices-inspired architecture that separates concern across distinct service layers:
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
- Read-only SQL safety enforcement (`DefaultSqlSafetyChecker` preventing non-SELECT operations).
- Automatic `LIMIT` clause injection and input validation.
- Connection management enforcing read-only driver flags (`SET TRANSACTION READ ONLY`).

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
    │   ├─→ Execute on PostgreSQL (READ ONLY mode)
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
- **Strict Read-Only Enforcement:** Only `SELECT` statements are executed.
- **Connection Flags:** `SET TRANSACTION READ ONLY` on PostgreSQL connections.
- **Query Safety:** Parameterized queries via SQLAlchemy; automatic limit clauses prevent DoS.
- **CORS & Headers:** Whitelisted origins, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`.

---

## Deployment Architecture

```
localhost:5173      → Web App (Vite)
localhost:8080      → Nginx Gateway / Reverse Proxy
localhost:8001      → Auth0 API
localhost:8002      → SQL Query API
localhost:3000      → Grafana UI (otel-lgtm telemetry stack)
localhost:4318      → OTLP HTTP receiver for traces/logs/metrics
localhost:5432      → PostgreSQL (Docker Container)
```

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
