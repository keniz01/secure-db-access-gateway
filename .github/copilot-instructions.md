# Copilot Instructions for Read-Only Database Explorer

## Project Overview

**Read-Only Database Explorer** is a full-stack secure database query application with three microservices:
- **Web App** (React/TypeScript/Vite) - UI on port 5173
- **Auth0 API** (FastAPI) - Authentication & user info on port 8001
- **SQL Query API** (FastAPI) - Query execution & validation on port 8002

All services enforce **read-only database access** with strict security controls.

## Architecture Principles

### Three-Service Pattern
- **Frontend**: React with Auth0 integration, manages UI state via React Query + Context
- **Auth Service**: Handles OAuth2 callback, JWT tokens (stored in httpOnly cookies), user dashboard
- **Query Service**: Executes validated SELECT queries, applies SQL safety rules, GraphQL support

### Critical Data Flows
1. **Auth Flow**: Browser → Auth0 → Auth API callback → JWT in httpOnly cookie → token verified in requests
2. **Query Flow**: React component → SQL Query API → SQL safety checker (rules-based) → AsyncPG to database
3. **Cross-service**: Query API requires auth token header; Auth API provides user context for audit logs

## Developer Workflows

### Setup & Execution
```bash
# Auth0 API (Python 3.11+)
cd auth0_api && python -m venv venv && source venv/bin/activate
pip install -e . && python main.py  # runs on 8001

# SQL Query API (Python 3.11+)
cd sql_query_api && python -m venv venv && source venv/bin/activate
# Set DATABASE_URL (export DATABASE_URL="postgresql+asyncpg://user:pass@host/db")
uv uvicorn main:app --reload --port 8002

# Web App (Node 18+)
cd web-app && npm install && npm run dev  # runs on 5173
```

### Key Environment Variables
- **Auth0 API**: `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AZURE_OPENAI_KEY`
- **SQL Query API**: `DATABASE_URL` (async PostgreSQL URL), `CORS_ORIGINS`
- **Web App**: `.env` file with `VITE_AUTH0_DOMAIN`, `VITE_AUTH0_CLIENT_ID`, API endpoint URLs

## Project-Specific Patterns

### Application Factory Pattern (Auth0 API)
- **File**: [auth0_api/app/factory.py](../auth0_api/app/factory.py)
- All middleware, routes, and settings initialized in `create_app()` - called from [main.py](../auth0_api/main.py)
- Enables clean separation: settings → factory → main entry point
- **When adding endpoints**: Create route file in `app/routes/`, include router in factory

### Dependency Injection (SQL Query API)
- **Container**: [sql_query_api/dependencies/dependency_container.py](../sql_query_api/dependencies/dependency_container.py)
- Uses **punq** library for IoC; setup on app startup
- **Abstract interfaces** for services (e.g., `IMusicQueryService`) with concrete implementations
- Repository pattern for data access with async SQLAlchemy
- **When adding features**: Define abstract interface, register in container, inject via function params

### SQL Safety Validation (SQL Query API)
- **File**: [sql_query_api/repositories/sql_validators/sql_safety_checker.py](../sql_query_api/repositories/sql_validators/sql_safety_checker.py)
- **Rule-based system**: Each validation rule extends `SqlSafetyRule` protocol
- Rules enforce: single statement, SELECT-only, no comments, no CTEs, no forbidden keywords (DELETE, INSERT, UPDATE, etc.)
- **Critical**: Always validate before query execution; rules are the security boundary
- **To add rules**: Create new rule class in `sql_validators/rules/`, add to `DefaultSqlSafetyChecker.rules` list

### Security: httpOnly Cookies for JWT
- **Problem**: localStorage XSS vulnerability
- **Solution**: JWT token stored in httpOnly Secure cookie (backend-set), frontend stores only existence flag
- **Implementation**: [web-app/src/services/auth-service.tsx](../web-app/src/services/auth-service.tsx) + [api-client.ts](../web-app/src/services/api-client.ts)
- **When adding auth**: Use `withCredentials: true` in axios calls; token auto-included by browser

### CORS Environment Configuration
- **Auth0 API**: [auth0_api/app/middleware/setup.py](../auth0_api/app/middleware/setup.py) reads `CORS_ORIGINS` env var
- **SQL Query API**: [sql_query_api/main.py](../sql_query_api/main.py) defaults to `localhost:*` if empty
- **Production rule**: Always set `CORS_ORIGINS` to exact frontend domain; no wildcards

## Integration Points & External Dependencies

### Auth0 Integration
- **Flow**: OAuth2 authorization code (backend validates with Auth0 servers)
- **Token Endpoint**: Auth0 returns JWT; backend validates signature via Auth0 public keys
- **User Info**: Fetched from Auth0 `/userinfo` endpoint, cached in user session

### Azure OpenAI Integration (Auth0 API)
- **Service**: [auth0_api/app/services/ai_service.py](../auth0_api/app/services/ai_service.py)
- **Singleton**: Instantiated once per app; reused for performance
- **Retry Logic**: Implements exponential backoff for resilience
- **Endpoint**: `/api/dashboard` includes AI-generated personalized greeting

### Database Connection (SQL Query API)
- **Driver**: asyncpg (async PostgreSQL)
- **ORM**: SQLAlchemy async (v2.0+)
- **Pool**: Configured for concurrent async operations
- **Read-Only**: Connection credentials should have SELECT-only permissions

### GraphQL Schema (SQL Query API)
- **Framework**: Strawberry GraphQL
- **File**: [sql_query_api/graphql_schema/schema.py](../sql_query_api/graphql_schema/schema.py)
- **Endpoint**: `/graphql` with standard query capabilities
- **Note**: Respects SQL safety rules before query execution

## Common Coding Scenarios

### Adding a Protected User Endpoint (Auth0 API)
1. Create handler in [auth0_api/app/routes/user_routes.py](../auth0_api/app/routes/user_routes.py)
2. Require `Request` param, check `request.session.get("user_profile")`
3. Use logger from `app.config.logging.get_logger(__name__)`
4. Return `JSONResponse` with appropriate status code
5. No need to update factory; router already included

### Executing a Query (SQL Query API)
1. Request validated by SQL safety checker (automatic via repository)
2. Repository converts to SQLAlchemy statement
3. Async engine executes; results streamed to avoid memory issues
4. Caught exceptions: `ForbiddenSqlStatement` (safety failed), `SqlStatementExecutionException` (DB error)

### Frontend Data Fetching
- Use React Query hooks for caching & background refetch
- API calls via [web-app/src/services/api-client.ts](../web-app/src/services/api-client.ts) (includes auth token)
- Error handling: Display user-friendly messages; log errors for debugging
- Example: [web-app/src/services/dashboard-api.tsx](../web-app/src/services/dashboard-api.tsx)

## Testing & Validation

### Manual Testing
- Auth0 API health: `curl http://localhost:8001/api/health`
- SQL Query API GraphQL: POST to `http://localhost:8002/graphql` with query
- Web app: Navigate to `http://localhost:5173/login`

### SQL Validation Testing
- Test queries against safety checker: Call `DefaultSqlSafetyChecker().is_safe_select_query(sql)`
- Invalid examples: `DELETE FROM users`, `SELECT * WITH (TABLOCK)`, etc.

## Key Files Reference

| File | Purpose |
|------|---------|
| [ARCHITECTURE.md](../ARCHITECTURE.md) | System design & component overview |
| [SECURITY.md](../SECURITY.md) | Security implementation details (OAuth, CORS, headers) |
| [auth0_api/DEVELOPMENT.md](../auth0_api/DEVELOPMENT.md) | Auth0 API development guide |
| [sql_query_api/main.py](../sql_query_api/main.py) | SQL Query API entry point & middleware setup |
| [web-app/src/App.tsx](../web-app/src/App.tsx) | Frontend routing & auth protection |

---

**Last Updated**: January 2026
**Focus**: Microservices pattern, security-first design, async/await patterns, dependency injection
