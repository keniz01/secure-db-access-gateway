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
├── secrets/
├── docker-compose.yml
├── setup-secrets.sh
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
./setup-secrets.sh
# edit files in ./secrets with real credentials

docker compose up --build
```

This starts:
- Auth0 API: http://localhost:8001
- SQL Query API: http://localhost:8002
- Web app: http://localhost:5173
- Nginx gateway: http://localhost:8080
- OTEL/LGTM stack: http://localhost:3000

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

Secret values are expected to be provided via environment variables or Docker secrets files instead of being hardcoded.

Common examples:

```bash
# SQL Query API
export DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/appdb
export AUTH0_AUDIENCE=https://your-api-audience
export AUTH0_DOMAIN=your-domain.auth0.com

# Production: configure only server-side tenant mappings. The client receives
# logical IDs, never these connection strings.
export ENVIRONMENT=production
export TENANT_DATABASES_FILE=/run/secrets/tenant_databases.json

# Auth0 API
export AUTH0_CLIENT_ID=...
export AUTH0_CLIENT_SECRET=...
export SECRET_KEY=...
export SESSION_SECRET_KEY=...
export FRONTEND_URL=http://localhost:5173

# AI services
export OPENROUTER_API_KEY=...
export GEMINI_API_KEY=...
export EMBEDDING_DIMENSIONS=768

# Configure model identifiers through secret files:
# secrets/ai_model.txt
# secrets/embedding_model.txt
```

Docker uses `secrets/*.txt` files and populates `*_FILE` environment variables; see `docker-compose.yml` and `setup-secrets.sh` for the expected secret names.

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
