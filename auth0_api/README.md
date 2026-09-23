# Auth0 API - Production Ready Authentication Service

A modular, production-ready FastAPI authentication service for the SQL Query Executor platform. Built with Auth0 integration, AI-powered greetings, and comprehensive logging.

## 🎯 Overview

The Auth0 API provides secure authentication and user management for the SQL Query Executor platform. It features:

- **Auth0 Integration** - Enterprise-grade OAuth2 authentication
- **Session Management** - Secure session handling with user context
- **AI-Powered Greetings** - Dynamic dashboard messages using Azure OpenAI
- **Modular Architecture** - Clean separation of concerns for easy maintenance
- **Production Ready** - Comprehensive logging, error handling, and type safety
- **Well Documented** - 8 detailed guides covering architecture to deployment

## 📋 Quick Links

- **Architecture Overview** → [ARCHITECTURE.md](../ARCHITECTURE.md)
- **Security Model** → [SECURITY.md](../SECURITY.md)
- **OAuth 2.1 Compliance** → [docs/OAUTH_COMPLIANCE.md](../docs/OAUTH_COMPLIANCE.md)
- **Runbooks** → [RUNBOOKS.md](../RUNBOOKS.md)

## 🚀 Quick Start

### Prerequisites
- Python 3.12+
- Auth0 account with configured application
- Azure OpenAI API access (for AI greeting feature)

### Setup (5 minutes)

```bash
# From repo root — shared secrets must be installed first
pip install -e ./shared

# Auth0 API (Python 3.12, uv)
cd auth0_api && uv sync && uv run uvicorn main:app --reload --port 8001
# Or: pip install -e . && python main.py (requires ENVIRONMENT=dev)

# Or via Docker (recommended): see ../README.md and docker-compose.yml
#   ./scripts/bootstrap-dev.sh && docker compose up --build
```

The API will be available at `http://localhost:8001` (direct) or `https://localhost:8443/api/*` via nginx.

## 📌 API Endpoints

### Authentication
- `GET /api/health` - Health check
- `GET /api/login` - Initiate Auth0 login (Authorization Code + PKCE S256, static `OAUTH_REDIRECT_URI`)
- `GET /api/auth` - OAuth callback handler (verifies PKCE verifier + nonce)
- `POST /api/logout` - Clear session and logout (CSRF-protected; `GET /api/logout` returns 405)

### User
- `GET /api/user` - Get authenticated user information
- `GET /api/dashboard` - Get dashboard with AI-generated greeting

## 🏗️ Project Structure

```
auth0_api/
├── app/
│   ├── config/              # settings.py, logging.py
│   ├── auth/                # oauth.py, session_store.py (Redis)
│   ├── security/            # csrf.py (__Host- + Sec-Fetch-Site)
│   ├── middleware/          # setup.py (CORS, session, security headers, CSP)
│   ├── routes/              # auth_routes.py, graphql_routes.py, user_routes.py, health_routes.py
│   ├── services/            # ai_service.py, text_to_sql_service.py
│   └── utils/               # helpers.py
├── main.py
└── pyproject.toml
```

## 🔧 Environment Variables

Required configuration (see `.env.example`):

```bash
# Auth0 (required in production)
AUTH0_DOMAIN=your-tenant.auth0.com
AUTH0_CLIENT_ID=...
AUTH0_CLIENT_SECRET=...
AUTH0_AUDIENCE=https://secure-db-access-gateway-api

# Session (required)
APP_SECRET_KEY=  # openssl rand -hex 32
SESSION_MAX_AGE=3600
# OAUTH_REDIRECT_URI defaults to FRONTEND_URL + /auth (must be in Auth0 Allowed Callbacks)
# REDIS_URL is required in production for shared sessions (fail-closed); e.g. redis://redis:6379/0

# Frontend / CORS
FRONTEND_URL=https://localhost:8443
REACT_APP_URL=https://localhost:8443
CORS_ORIGINS=https://localhost:8443,http://localhost:5173
SQL_QUERY_API_URL=http://sql_query_api:8002/graphql

# AI (optional)
OPENROUTER_API_KEY=...
GEMINI_API_KEY=...
EMBEDDING_DIMENSIONS=768
AI_MODEL=nvidia/nemotron-3-super-120b-a12b:free

# Env: ENVIRONMENT=production (fail-closed) or dev
```
See `../.env.example` for full list.

## 📚 Documentation

- [ARCHITECTURE.md](../ARCHITECTURE.md) — design and multi-tenant data flow
- [SECURITY.md](../SECURITY.md) — security headers, CORS, CSP, CSRF, DB least-privilege
- [docs/OAUTH_COMPLIANCE.md](../docs/OAUTH_COMPLIANCE.md) — OAuth 2.1 / BCP 9700 matrix
- [RUNBOOKS.md](../RUNBOOKS.md) — operator runbooks (linted via `scripts/check-runbooks.py`)
- [AGENTS.md](../AGENTS.md) — service commands, env gotchas, OPA, nginx

## ✨ Key Features

### Modular Architecture
- **11 focused modules** instead of monolithic code
- **Clear separation of concerns** for maintainability
- **Dependency injection** for testability

### Production Ready
- **Type hints** throughout codebase
- **Comprehensive logging** with module-specific loggers
- **Robust error handling** with custom exceptions
- **Configuration management** with environment variables

### Developer Friendly
- **~1400 lines of documentation** across 8 guides
- **Docstrings** on all classes and methods
- **Code examples** for common tasks
- **Clear patterns** for extending functionality

### Security
- **Secure session management** for OAuth flow
- **CORS properly configured** for frontend integration
- **Environment-based secrets** (never hardcoded)
- **Input validation** with Pydantic models

## 🧪 Testing

Services are designed to be easily testable:

```python
# Unit test example
def test_ai_service():
    service = AIService()
    result = await service.get_greeting(
        system="Be helpful",
        user="Say hello"
    )
    assert isinstance(result, str)
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for complete testing guide.

## 🛡️ CSRF, Cookie & CORS Protection

The Auth0 API is the browser-facing backend-for-frontend: it holds the Auth0
access token server-side and proxies governed queries to `sql_query_api` (which
is never browser-reachable and authenticates with a bearer token only).

**Browser session cookies** (`__Host-gateway_session` / `__Host-csrf_token` in production, `gateway_session`/`csrf_token` in dev):
- `__Host-gateway_session` carries only an opaque session id, signed with
  `APP_SECRET_KEY`. It is **HttpOnly** (never readable by JS), `SameSite=lax`,
  `Secure`, `Path=/` (no Domain) via `__Host-` prefix in production. `SESSION_MAX_AGE` controls lifetime.
- `__Host-csrf_token` is the double-submit CSRF value, readable by JS on purpose so
  the SPA can echo it; also `__Host-` in production.

**CSRF defense in depth** — every state-changing (POST) endpoint
(`/api/graphql`, `/api/text-to-sql`, `POST /api/logout`) must clear all of these layers:
1. `SameSite=lax` cookies (with `__Host-` prefix in prod) — browsers refuse to attach session to cross-site POST.
2. **Origin/Referer validation** against the same allowlist CORS uses (`app/security/csrf.py` `get_allowed_origins()`); rejected with `403`.
3. **Fetch Metadata** — `Sec-Fetch-Site` must be `same-origin` or `same-site` when present.
4. The `X-Requested-With: XMLHttpRequest` browser marker, sent by the SPA.
5. A double-submit token: server binds random token to Redis session and stamps `__Host-csrf_token` cookie at login; SPA echoes `X-CSRF-Token`, server requires **cookie == header == session** via `secrets.compare_digest`.

**CORS review outcome** — `allow_origins` is an explicit allowlist shared with
the Origin check (never `*`), `allow_credentials=True` is required for cookie
auth (only paired with concrete origins), and only the minimal methods/headers
the app uses are allowed (`GET`/`POST`/`OPTIONS`; `Content-Type`,
`Authorization`, `X-Requested-With`, `X-CSRF-Token`). In production the SPA and
API share one origin behind nginx, so no preflights occur; the allowlist
matters for local dev (Vite on :5173 → https://localhost:8443). Add any new SPA
origin to `CORS_ORIGINS` — it automatically becomes valid for Origin checks too.

## 🔐 Authentication Flow

1. User clicks login on frontend
2. Frontend redirects to `/api/login`
3. Server redirects to Auth0 hosted login page
4. User authenticates with Auth0
5. Auth0 redirects back to `/api/auth` with authorization code
6. Server exchanges code for access token and user info
7. User info stored in session
8. User now authenticated for `/api/dashboard` and `/api/user` endpoints

## 🚀 Deployment

- **Docker Compose** — `docker-compose.yml` (dev) and `docker-compose.prod.yml` (GHCR images, `REDIS_URL=redis://redis:6379/0`, TLS via nginx). See `../DOCKER_README.md`.
- **Horizontal scaling** — stateless only with `REDIS_URL` (Redis mandatory in prod, `session_store.py` fail-closed); without Redis, in-memory store is single-process only.
- **Health checks** — `/api/health`, `/readyz`, nginx `/nginx-health`.

## 📊 Refactoring Highlights

This project was recently refactored from a monolithic 338-line file to a modular, maintainable architecture:

- ✅ **94% reduction** in main file size
- ✅ **11 focused modules** with clear responsibilities
- ✅ **8 comprehensive guides** for documentation
- ✅ **100% functionality preserved** with backward compatibility
- ✅ **Type safety** with Pydantic throughout
- ✅ **Production-ready** code structure

See [REFACTORING_SUMMARY.md](REFACTORING_SUMMARY.md) for details.

## 🔗 Dependencies

Key dependencies:
- **FastAPI** - Modern web framework
- **Authlib** - OAuth2 and Auth0 integration
- **Pydantic** - Data validation and type hints
- **OpenAI** - Azure OpenAI for AI greetings
- **Uvicorn** - ASGI server

Full list: See `pyproject.toml`

## 📝 Development Workflow

See `../AGENTS.md` for service commands (`uv sync`, `pytest`, `npm run lint`), env gotchas, and OPA/nginx notes. Use `../scripts/bootstrap-dev.sh` for local setup.

## 🤝 Contributing

When contributing to this project:

1. Follow the patterns established in existing code
2. Maintain type hints and docstrings
3. Keep modules focused and small
4. Update documentation for new features
5. Write tests for new functionality

## 📄 License

Part of the SQL Query Executor platform.

## 🆘 Troubleshooting

### "Not authenticated" error
- Check session middleware is configured
- Ensure cookies are enabled in browser
- Verify callback URL matches Auth0 configuration

### AI service unavailable
- Check `OPENROUTER_API_KEY` and `GEMINI_API_KEY` are valid
- Verify network connectivity to OpenRouter and Gemini
- Check logs for rate limiting

### CORS errors
- Ensure frontend origin is in `CORS_ORIGINS` (not `ALLOWED_ORIGINS` — internal allowlist)
- Update `REACT_APP_URL`/`FRONTEND_URL` in `.env` to match frontend and ensure `OAUTH_REDIRECT_URI` is in Auth0 Allowed Callbacks

Check `app/security/csrf.py` shared allowlist.

## 📞 Support

- **Architecture**: [ARCHITECTURE.md](../ARCHITECTURE.md)
- **Security**: [SECURITY.md](../SECURITY.md)
- **OAuth 2.1**: [docs/OAUTH_COMPLIANCE.md](../docs/OAUTH_COMPLIANCE.md)