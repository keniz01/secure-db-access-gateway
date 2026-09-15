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

- **Getting Started?** → [QUICK_START.md](QUICK_START.md)
- **Need Architecture Overview?** → [ARCHITECTURE.md](../ARCHITECTURE.md)
- **Setting Up Development?** → [DEVELOPMENT.md](DEVELOPMENT.md)
- **Documentation Index?** → [INDEX.md](INDEX.md)

## 🚀 Quick Start

### Prerequisites
- Python 3.12+
- Auth0 account with configured application
- Azure OpenAI API access (for AI greeting feature)

### Setup (5 minutes)

```bash
# Clone and navigate to directory
cd auth0_api

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .

# Configure environment
cp .env.example .env
# Edit .env with your Auth0 and API credentials

# Run application
python main.py
```

The API will be available at `http://localhost:8001`

## 📌 API Endpoints

### Authentication
- `GET /api/health` - Health check
- `GET /api/login` - Initiate Auth0 login flow
- `GET /api/auth` - OAuth callback handler
- `GET /api/logout` - Clear session and logout

### User
- `GET /api/user` - Get authenticated user information
- `GET /api/dashboard` - Get dashboard with AI-generated greeting

## 🏗️ Project Structure

```
auth0_api/
├── app/                      # Main application package
│   ├── config/              # Settings and logging configuration
│   ├── auth/                # Auth0 and OAuth setup
│   ├── services/            # Business logic (AI service)
│   ├── routes/              # API endpoints
│   ├── middleware/          # CORS and session middleware
│   ├── schemas/             # Request/response models
│   ├── exceptions/          # Custom exception definitions
│   └── utils/               # Helper functions
├── main.py                  # Application entry point
├── pyproject.toml           # Project dependencies
└── Documentation files      # 8 comprehensive guides
```

## 🔧 Environment Variables

Required configuration (see `.env.example`):

```bash
# Auth0 Configuration
AUTH0_DOMAIN=your-auth0-domain
AUTH0_CLIENT_ID=your-client-id
AUTH0_CLIENT_SECRET=your-client-secret

# AI API Keys
OPENROUTER_API_KEY=your-openrouter-api-key
GEMINI_API_KEY=your-gemini-api-key
EMBEDDING_DIMENSIONS=768

Model identifiers are loaded from `AI_MODEL_FILE` and `EMBEDDING_MODEL_FILE`.

# Session Management
APP_SECRET_KEY=your-secret-key

# Frontend Configuration
REACT_APP_URL=https://localhost:8443
FRONTEND_URL=https://localhost:8443/dashboard

# Optional
AUTH_LOG_LEVEL=INFO
```

## 📚 Documentation

This project includes comprehensive documentation:

| Document | Purpose | Read Time |
|----------|---------|-----------|
| [INDEX.md](INDEX.md) | Navigation guide for all docs | 5 min |
| [QUICK_START.md](QUICK_START.md) | Getting started quickly | 5 min |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | Design and architecture | 15 min |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Development guide and patterns | 20 min |
| [MIGRATION.md](MIGRATION.md) | How code was refactored | 15 min |
| [STRUCTURE.md](STRUCTURE.md) | File organization reference | 10 min |
| [REFACTORING_SUMMARY.md](REFACTORING_SUMMARY.md) | Refactoring details | 10 min |
| [COMPLETION_CHECKLIST.md](COMPLETION_CHECKLIST.md) | Verification checklist | 10 min |

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

**Browser session cookies** (`gateway_session` + `csrf_token`):
- `gateway_session` carries only an opaque session id, signed with
  `APP_SECRET_KEY`. It is **HttpOnly** (never readable by JS), `SameSite=lax`,
  and `Secure` when `SESSION_COOKIE_SECURE` is true (auto-forced in
  production). `SESSION_MAX_AGE` controls the lifetime of both cookies and the
  server-side session.
- `csrf_token` is the double-submit CSRF value, readable by JS on purpose so
  the SPA can echo it (see below).

**CSRF defense in depth** — every state-changing (POST) endpoint
(`/api/graphql`, `/api/text-to-sql`) must clear all of these layers:
1. `SameSite=lax` cookies — browsers already refuse to attach the session
   cookie to cross-site POST submissions.
2. **Origin/Referer validation** against the same allowlist CORS uses
   (`app/security/csrf.py`). Requests with the session cookie but no trusted
   Origin or Referer are rejected with `403 "CSRF protection failed"`.
3. The `X-Requested-With: XMLHttpRequest` browser marker, sent by the SPA.
4. A double-submit token: the server binds a random token to the session and
   stamps it in a `csrf_token` cookie at login; the SPA echoes it as
   `X-CSRF-Token`, and the server requires **cookie == header == session**.

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

The application is production-ready and supports:

- **Docker containerization** - Included in structure
- **Kubernetes deployment** - Health checks and graceful shutdown ready
- **Cloud platforms** - Environment variable configuration
- **Horizontal scaling** - Stateless design

See [DEVELOPMENT.md](DEVELOPMENT.md#deployment-considerations) for deployment guide.

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

### Adding a New Feature

1. Read [DEVELOPMENT.md](DEVELOPMENT.md) for patterns
2. Create new module in appropriate directory
3. Follow existing code style and structure
4. Update documentation as needed

### Common Tasks

- **Add endpoint**: [DEVELOPMENT.md](DEVELOPMENT.md#adding-a-new-endpoint)
- **Add service**: [DEVELOPMENT.md](DEVELOPMENT.md#adding-a-new-service)
- **Configure settings**: [DEVELOPMENT.md](DEVELOPMENT.md#extending-configuration)
- **Write tests**: [DEVELOPMENT.md](DEVELOPMENT.md#testing)

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
- Ensure frontend origin is in `ALLOWED_ORIGINS`
- Update `REACT_APP_URL` in `.env` to match frontend

For more help, see [DEVELOPMENT.md](DEVELOPMENT.md#troubleshooting).

## 📞 Support

- **Architecture questions**: See [ARCHITECTURE.md](../ARCHITECTURE.md)
- **Setup issues**: See [DEVELOPMENT.md](DEVELOPMENT.md)
- **Finding files**: See [STRUCTURE.md](STRUCTURE.md)
- **General info**: See [INDEX.md](INDEX.md) for navigation

---

**Start here**: Read [QUICK_START.md](QUICK_START.md) for a quick orientation, then dive into [ARCHITECTURE.md](../ARCHITECTURE.md) for the full picture.