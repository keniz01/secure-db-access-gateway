# CLAUDe - Comprehensive Codebase Architecture & Documentation

## Project Overview

**Read-Only Database Explorer** is a secure, full-stack web application for safely exploring and querying databases with read-only access. The system is built with modern technologies and follows security best practices, implementing OWASP Top 10 protections.

**Version:** 1.1.0  
**Status:** Production Ready  
**Last Updated:** January 2026

---

## System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Web Application (React)                      │
│              React 19 + TypeScript + Vite + Tailwind           │
│                         Port: 5173                              │
│                                                                 │
│  • Login & Auth Callback                                       │
│  • Protected Dashboard with User Stats                         │
│  • SQL Query Interface                                         │
│  • Token Management & Session Persistence                      │
└────────────────────┬──────────────────────┬──────────────────────┘
                     │                      │
       ┌─────────────┘                      └─────────────┐
       │                                                   │
       ▼                                                   ▼
┌─────────────────────────────────────┐  ┌────────────────────────────┐
│      Auth0 API (FastAPI)            │  │   SQL Query API (FastAPI)   │
│      Port: 8001                     │  │   Port: 8002                │
│                                     │  │                             │
│  • OAuth2/Auth0 Integration         │  │  • GraphQL Endpoint         │
│  • Session Management               │  │  • SQL Query Execution     │
│  • User Information Endpoints       │  │  • Database Schema Access  │
│  • AI-Powered Greetings            │  │  • SQL Validation          │
└──────────┬──────────────────────────┘  └────────────┬───────────────┘
           │                                           │
           │                    ┌──────────────────────┘
           │                    │
           ▼                    ▼
       Auth0           PostgreSQL Database
       Service         (Read-Only Connection)
```

---

## Component Breakdown

### 1. Frontend: Web Application (`web-app/`)

**Technology Stack:**
- **React 19** - UI framework
- **TypeScript** - Type safety
- **Vite** - Build tool & dev server
- **Tailwind CSS 4.1** - Styling
- **TanStack React Query 5.90** - Data fetching & caching
- **React Router DOM 7.11** - Client-side routing
- **Axios 1.13** - HTTP client
- **Lucide React** - Icon library

**Project Structure:**
```
web-app/
├── src/
│   ├── components/
│   │   ├── auth-callback.tsx      # OAuth callback handler
│   │   ├── login-page.tsx         # Login interface
│   │   └── dashboard/
│   │       ├── DashboardHeader.tsx
│   │       ├── QueryInput.tsx
│   │       ├── QueryResults.tsx
│   │       ├── ResultsTable.tsx
│   │       └── UserInfoCard.tsx
│   ├── contexts/
│   │   ├── auth-context.ts        # Auth context definition
│   │   └── auth-provider.tsx      # Auth state provider
│   ├── hooks/
│   │   └── use-auth.tsx          # Custom auth hook
│   ├── services/
│   │   ├── api-client.ts         # Axios wrapper with token injection
│   │   ├── auth-api.tsx          # Auth0 API endpoints
│   │   ├── auth-service.tsx      # Local auth state management
│   │   ├── dashboard-api.tsx     # Dashboard data fetching
│   │   └── graphql-api.ts       # GraphQL client
│   ├── models/
│   │   └── user-profile.ts       # User type definitions
│   ├── configs/
│   │   └── url-config.ts        # API endpoint configuration
│   ├── App.tsx                  # Main app component
│   ├── main.tsx                # Application entry point
│   └── index.css               # Global styles
├── public/                     # Static assets
├── dist/                       # Build output
├── package.json
├── vite.config.ts
└── tsconfig.json
```

**Key Features:**
- **Authentication Flow:**
  - Login page with Auth0 integration
  - OAuth callback processing
  - Protected routes with route guards
  - Token management via httpOnly cookies

- **Dashboard:**
  - User profile display
  - AI-generated personalized greeting
  - SQL query input interface
  - Query results table display
  - Dashboard statistics

- **Security:**
  - httpOnly cookies for JWT tokens (XSS protection)
  - Automatic token inclusion in API requests
  - Input validation with error recovery
  - Protected route guards

**Entry Point:** `src/main.tsx` → `App.tsx`

---

### 2. Backend: Auth0 API (`auth0_api/`)

**Technology Stack:**
- **FastAPI 0.126** - Web framework
- **Auth0** - OAuth2/OIDC provider
- **Authlib 1.6.6** - OAuth2 client library
- **Python-JOSE 3.5.0** - JWT handling
- **OpenAI 2.14.0** - Azure OpenAI integration
- **Google GenAI 1.56.0** - Alternative AI service
- **Uvicorn 0.38.0** - ASGI server
- **Python 3.12+** - Runtime

**Project Structure:**
```
auth0_api/
├── app/
│   ├── __init__.py
│   ├── factory.py              # Application factory
│   ├── config/
│   │   ├── settings.py         # Environment-based settings
│   │   └── logging.py          # Logging configuration
│   ├── auth/
│   │   ├── oauth.py            # Auth0 OAuth2 setup
│   │   └── session.py          # User sessions & JWT
│   ├── routes/
│   │   ├── auth_routes.py      # Authentication endpoints
│   │   └── user_routes.py      # User info & dashboard
│   ├── services/
│   │   └── ai_service.py       # LLM/Azure OpenAI integration
│   ├── middleware/
│   │   └── setup.py            # CORS & session middleware
│   ├── schemas/
│   │   └── responses.py        # Request/response models
│   ├── exceptions/
│   │   └── handlers.py         # Custom exceptions
│   └── utils/
│       └── helpers.py           # Utility functions
├── main.py                     # Entry point
├── pyproject.toml              # Dependencies
└── Documentation files         # 8 comprehensive guides
```

**API Endpoints:**

**Authentication:**
- `GET /api/health` - Health check
- `GET /api/login` - Initiates Auth0 login flow
- `GET /api/auth` - OAuth2 callback handler
- `GET /api/logout` - Clears session

**User Information:**
- `GET /api/user` - Returns authenticated user profile
- `GET /api/dashboard` - Returns user info with AI-generated greeting

**Key Services:**

1. **OAuth2 Integration** (`app/auth/oauth.py`)
   - Configures Auth0 as OAuth2 provider
   - Handles authorization code flow
   - Token management and validation
   - User information retrieval

2. **AI Service** (`app/services/ai_service.py`)
   - Generates personalized greetings using Azure OpenAI
   - Implements retry logic for resilience
   - Singleton pattern for efficiency
   - Supports multiple AI providers (OpenAI, Google GenAI)

3. **Configuration** (`app/config/settings.py`)
   - Centralized environment variable management
   - Type-safe settings with defaults
   - Multi-environment support

**Security Features:**
- CORS with configurable origins
- Session management for OAuth state
- Security headers (X-Content-Type-Options, X-Frame-Options, etc.)
- httpOnly cookies for JWT tokens
- CSRF protection

**Port:** `8001`

**Entry Point:** `main.py`

---

### 3. Backend: SQL Query API (`sql_query_api/`)

**Technology Stack:**
- **FastAPI 0.121** - Web framework
- **Strawberry GraphQL 0.284** - GraphQL framework
- **SQLAlchemy 2.0.44** - Async ORM
- **AsyncPG 0.30.0** - PostgreSQL async driver
- **Loguru 0.7.3** - Logging
- **Punq 0.7.0** - Dependency injection
- **SQLParse 0.5.3** - SQL parsing
- **Python 3.12+** - Runtime

**Project Structure:**
```
sql_query_api/
├── config/
│   └── app_logger.py           # Loguru configuration
├── dependencies/
│   └── dependency_container.py # Dependency injection setup
├── exceptions/
│   ├── exception_handlers.py
│   ├── forbidden_sql_statement_exception.py
│   └── sql_statement_execution_exception.py
├── graphql_schema/
│   └── schema.py               # GraphQL schema definition
├── middlewares/
│   ├── correlation_middleware.py  # Request tracing
│   └── logging_middleware.py      # Request/response logging
├── repositories/
│   ├── abstract_music_query_repository.py  # Interface
│   ├── music_query_repository.py   # Implementation
│   └── sql_validators/
│       ├── sql_safety_checker.py
│       └── rules/
│           └── sql_rules.py
├── routes/
│   ├── music_query_controller.py
│   └── models/
│       └── sql_statement_request.py
├── services/
│   ├── abstract_music_query_service.py  # Interface
│   └── music_query_service.py   # Implementation
├── main.py                     # Entry point
├── app_factory.py              # Application factory
├── pyproject.toml              # Dependencies
└── docker-compose.yml          # PostgreSQL setup
```

**GraphQL Schema:**

```graphql
type Query {
  executeQuery(sql: String!, params: JSON): [QueryResult!]!
  getTableSchema(embeddings: [Float!]!): SchemaInfo!
}
```

**Core Components:**

1. **Repository Layer** (`repositories/`)
   - Abstract interface (`IMusicQueryRepository`) for testability
   - Concrete implementation with async database operations
   - SQL safety validation before execution
   - Automatic LIMIT enforcement to prevent runaway queries
   - Schema context management (music & meta schemas)

2. **SQL Safety Validation** (`repositories/sql_validators/`)
   - Prevents DDL (CREATE, ALTER, DROP) statements
   - Blocks DML (INSERT, UPDATE, DELETE) statements
   - Restricts subqueries and CTEs
   - Allows only simple SELECT statements
   - Validates query length (max 10,000 characters)

3. **Service Layer** (`services/`)
   - Abstract interface (`IMusicQueryService`) for dependency injection
   - Coordinates between repository and GraphQL endpoints
   - Business logic and error handling
   - Async/await patterns for non-blocking operations

4. **GraphQL Controller** (`routes/music_query_controller.py`)
   - Strawberry GraphQL resolver definitions
   - Input validation and parameter handling
   - Integration with service layer

**Middleware & Observability:**

1. **Correlation Middleware** (`middlewares/correlation_middleware.py`)
   - Generates unique request IDs for tracing
   - Tracks requests through the system
   - Enhances logging with correlation context

2. **Logging Middleware** (`middlewares/logging_middleware.py`)
   - Structured logging for all HTTP requests/responses
   - Performance metrics
   - Error tracking

3. **Logging Configuration** (`config/app_logger.py`)
   - Loguru-based centralized logging
   - Correlation ID injection in all logs
   - Development and production-ready formats

**Security Features:**
- Restrictive CORS origin whitelist
- Parameterized queries with SQLAlchemy
- SQL statement validation before execution
- Query type restrictions (SELECT only)
- Response headers (X-Content-Type-Options, X-Frame-Options, etc.)

**Port:** `8002`

**Entry Point:** `main.py`

---

## Data Flow

### Authentication Flow

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

### Query Execution Flow

```
Dashboard
    │
    ├─→ User submits SQL query
    │
    ├─→ POST /graphql (SQL Query API)
    │   │
    │   ├─→ Validate SQL safety
    │   ├─→ Apply automatic LIMIT
    │   ├─→ Execute on PostgreSQL
    │   └─→ Return results as JSON
    │
    └─→ Display results in table
```

### Token Management

```
API Request (Web App)
    │
    ├─→ Axios interceptor
    ├─→ Reads httpOnly cookie (automatic by browser)
    ├─→ Includes Authorization header
    │
    └─→ API receives authenticated request
```

---

## Security Architecture

### Authentication & Authorization
- **OAuth2 with Auth0** - Enterprise-grade authentication
- **JWT Tokens** - Secure, stateless authentication
- **httpOnly Cookies** - Prevents XSS token theft
- **Session Management** - Secure state handling during OAuth flow

### API Security
- **CORS** - Restrictive origin whitelisting
- **SQL Injection Prevention** - Parameterized queries + validation
- **Read-Only Access** - All queries limited to SELECT statements
- **Query Limits** - Automatic LIMIT clauses prevent DoS
- **Input Validation** - Length and type checks
- **Security Headers** - Comprehensive HTTP security headers

### Network Security
- **HTTPS/TLS** - Strict transport security headers
- **Secure Cookies** - HttpOnly, Secure, SameSite attributes
- **Correlation IDs** - Request tracing for audit logs

### OWASP Top 10 Coverage

| Vulnerability | Status | Implementation |
|---------------|--------|-----------------|
| A01: Broken Access Control | ✅ Mitigated | Token validation, session management |
| A02: Cryptographic Failures | ✅ Mitigated | HTTPS enforcement, secure headers |
| A05: Security Misconfiguration | ✅ Mitigated | Restrictive CORS, security headers |
| A07: Cross-Site Scripting (XSS) | ✅ Mitigated | httpOnly cookies, input validation |
| A08: Insecure Deserialization | ✅ Mitigated | JSON validation with error handling |
| A09: Using Components with Known Vulnerabilities | ✅ Monitored | Regular dependency updates |

---

## Technology Stack Summary

### Frontend
| Technology | Version | Purpose |
|-----------|---------|---------|
| React | 19.2.0 | UI framework |
| TypeScript | 5.9.3 | Type safety |
| Vite | 7.2.4 | Build tool & dev server |
| Tailwind CSS | 4.1.18 | Styling |
| TanStack Query | 5.90.12 | Data fetching & caching |
| React Router | 7.11.0 | Client-side routing |
| Axios | 1.13.2 | HTTP client |

### Backend (Auth API)
| Technology | Version | Purpose |
|-----------|---------|---------|
| FastAPI | 0.126.0 | Web framework |
| Auth0 | - | OAuth2 provider |
| Authlib | 1.6.6 | OAuth2 client |
| OpenAI | 2.14.0 | AI service |
| Google GenAI | 1.56.0 | Alternative AI service |
| Python-JOSE | 3.5.0 | JWT handling |
| Uvicorn | 0.38.0 | ASGI server |

### Backend (Query API)
| Technology | Version | Purpose |
|-----------|---------|---------|
| FastAPI | 0.121.1 | Web framework |
| Strawberry GraphQL | 0.284.2 | GraphQL framework |
| SQLAlchemy | 2.0.44 | ORM |
| AsyncPG | 0.30.0 | PostgreSQL async driver |
| Loguru | 0.7.3 | Logging |
| Punq | 0.7.0 | Dependency injection |
| SQLParse | 0.5.3 | SQL parsing |

### Infrastructure
| Technology | Purpose |
|-----------|---------|
| PostgreSQL | Primary database |
| Docker Compose | Local development |
| Docker | Containerization |

---

## Deployment Architecture

### Development Environment

```
localhost:5173      → Web App (Vite)
localhost:8001      → Auth0 API
localhost:8002      → SQL Query API
localhost:5432      → PostgreSQL (Docker)
```

### Docker Compose Services

The `docker-compose.yml` defines three services:

1. **auth0_api**
   - Build: `./auth0_api`
   - Port: `8001:8001`
   - Secrets: Auth0 credentials, API keys, frontend URLs
   - Environment: CORS origins, secret file paths

2. **sql_query_api**
   - Build: `./sql_query_api`
   - Port: `8002:8002`
   - Secrets: Database URL
   - Environment: CORS origins, database URL file path

3. **web_app**
   - Build: `./web-app`
   - Port: `5173:5173`
   - Environment: API base URLs
   - Depends on: auth0_api, sql_query_api

### Production Considerations

1. **Environment Configuration**
   - All services configured via environment variables
   - CORS origins configured per environment
   - Database URLs flexible for different providers

2. **Containerization**
   - Each service can be containerized independently
   - Docker Compose provided for local development
   - Kubernetes-ready architecture

3. **Logging & Monitoring**
   - Correlation IDs for request tracing
   - Structured logging with Loguru
   - Security headers for compliance

4. **Scaling Strategy**
   - Auth0 API: Stateless (horizontal scaling ready)
   - Query API: Read-only operations (cacheable, scalable)
   - Frontend: Static assets (CDN-ready)

---

## Key Architectural Decisions

### 1. Separation of Concerns
- **Auth Service:** Handles authentication only
- **Query Service:** Handles database queries only
- **Frontend:** UI and user interactions

**Benefit:** Independent scaling, testing, and deployment

### 2. Dependency Injection
- Used in SQL Query API for loose coupling
- Enables easy mocking for testing
- Abstract interfaces for service contracts

**Benefit:** Testability and maintainability

### 3. Repository Pattern
- Data access abstraction in SQL Query API
- SQL validation at repository layer
- Single responsibility principle

**Benefit:** Centralized data access control and validation

### 4. Async/Await Architecture
- All I/O operations are async
- Non-blocking database calls
- Efficient resource utilization

**Benefit:** High throughput and better resource efficiency

### 5. Read-Only Database Access
- All queries restricted to SELECT
- Automatic LIMIT enforcement
- SQL statement validation

**Benefit:** Data integrity and security

### 6. GraphQL over REST
- Strongly typed schema
- Precise data fetching
- Better developer experience

**Benefit:** Type safety and reduced data over-fetching

### 7. Modular Architecture (Auth0 API)
- Refactored from monolithic 338-line file
- 11 focused modules with clear responsibilities
- 94% reduction in main file size

**Benefit:** Maintainability and extensibility

---

## Development Workflow

### Starting All Services

1. **PostgreSQL** (if using Docker)
   ```bash
   cd sql_query_api
   docker-compose up -d
   ```

2. **Auth0 API**
   ```bash
   cd auth0_api
   python main.py  # Runs on :8001
   ```

3. **Query API**
   ```bash
   cd sql_query_api
   python main.py  # Runs on :8002
   ```

4. **Web App**
   ```bash
   cd web-app
   npm run dev  # Runs on :5173
   ```

### Using Docker Compose

```bash
# Start all services
docker-compose up --build

# Stop all services
docker-compose down
```

### Project Dependencies
- Python 3.12+ (Auth & Query APIs)
- Node.js 18+ (Web App)
- PostgreSQL 14+ (Database)
- Auth0 account (Authentication)
- Azure OpenAI API key (AI features)

---

## Configuration

### Environment Variables

**Auth0 API** (`.env`):
```bash
AUTH0_DOMAIN=your-domain.auth0.com
AUTH0_CLIENT_ID=your_client_id
AUTH0_CLIENT_SECRET=your_client_secret
APP_SECRET_KEY=generate-strong-random-key-min-32-chars
SESSION_SECRET_KEY=generate-strong-random-key-min-32-chars
GITHUB_TOKEN=your-azure-openai-token
REACT_APP_URL=http://localhost:5173
FRONTEND_URL=http://localhost:5173/dashboard
CORS_ORIGINS=http://localhost:5173
AUTH_LOG_LEVEL=INFO
```

**SQL Query API** (`.env`):
```bash
DATABASE_URL=postgresql+asyncpg://user:password@host:port/database
CORS_ORIGINS=http://localhost:5173
LOG_LEVEL=INFO
```

**Web Application** (`.env`):
```bash
VITE_AUTH0_API_URL=http://localhost:8001
VITE_SQL_QUERY_API_URL=http://localhost:8002
VITE_SQL_GRAPHQL_BASE_URL=http://localhost:8002/graphql
```

### Docker Secrets

Secrets are managed via Docker secrets in the `secrets/` directory:
- `auth0_client_id.txt`
- `auth0_client_secret.txt`
- `auth0_domain.txt`
- `database_url.txt`
- `frontend_url.txt`
- `gemini_api_key.txt`
- `github_token.txt`
- `google_client_id.txt`
- `google_client_secret.txt`
- `openai_api_key.txt`
- `react_app_url.txt`
- `secret_key.txt`
- `session_secret_key.txt`

---

## Testing Strategy

### Frontend Testing
- Component testing with React Testing Library
- Integration tests with Mock Service Worker
- E2E tests with Playwright (recommended)

### Backend Testing
- Unit tests with pytest (Auth API)
- Integration tests with test database
- GraphQL query validation tests

### Security Testing
- OWASP Top 10 compliance
- SQL injection testing
- XSS/CSRF mitigation verification

---

## Monitoring & Observability

### Logs
- **Correlation IDs:** Track requests across services
- **Structured Logging:** JSON format for analysis
- **Security Events:** Authentication and authorization logs

### Metrics (Recommended)
- Response times per endpoint
- Query execution times
- Error rates and types
- Database connection pool usage

### Health Checks
- `GET /api/health` (Auth API)
- Database connectivity tests
- Auth0 availability checks

---

## File Organization

### Root Directory
```
read_only_database_explorer/
├── auth0_api/              # Authentication service
├── sql_query_api/         # Query execution service
├── web-app/               # React frontend
├── secrets/               # Docker secrets (not in git)
├── docker-compose.yml     # Docker orchestration
├── README.md              # Main documentation
├── ARCHITECTURE.md        # Architecture details
├── SECURITY.md           # Security implementation
├── DOCKER_README.md      # Docker setup guide
└── CLAUDe.md            # This file
```

### Key Documentation Files

**Root Level:**
- `README.md` - Main project documentation
- `ARCHITECTURE.md` - System architecture overview
- `SECURITY.md` - Security implementation guide
- `DOCKER_README.md` - Docker setup instructions
- `CLAUDe.md` - Comprehensive codebase documentation (this file)

**Auth0 API:**
- `README.md` - Service overview
- `ARCHITECTURE.md` - Service architecture
- `QUICK_START.md` - Quick setup guide
- `DEVELOPMENT.md` - Development guide
- `INDEX.md` - Documentation index
- `STRUCTURE.md` - File organization
- `MIGRATION.md` - Refactoring details
- `REFACTORING_SUMMARY.md` - Refactoring summary
- `COMPLETION_CHECKLIST.md` - Verification checklist

**SQL Query API:**
- `README.md` - Service overview

**Web App:**
- `README.md` - Frontend overview

---

## Code Quality & Standards

### Python (Backend)
- **Type Hints:** Full type annotation throughout
- **Docstrings:** Google-style docstrings on all classes/methods
- **Linting:** Ruff configured with strict rules
- **Code Style:** PEP 8 compliant
- **Dependencies:** Managed via `pyproject.toml` with `uv`

### TypeScript (Frontend)
- **Type Safety:** Strict TypeScript configuration
- **Linting:** ESLint with React-specific rules
- **Code Style:** Consistent formatting
- **Dependencies:** Managed via `package.json` with npm

---

## Future Enhancements

### Planned Features
- [ ] Query history tracking
- [ ] Query result export (CSV, JSON)
- [ ] Database schema visualization
- [ ] Support for multiple database connections
- [ ] Query performance metrics
- [ ] Saved queries functionality
- [ ] Role-based access control
- [ ] Audit logging
- [ ] Rate limiting implementation

### Security Enhancements
- [ ] Rate limiting per IP/user
- [ ] WAF integration
- [ ] API key management
- [ ] Database encryption at rest
- [ ] Token refresh mechanism
- [ ] MFA support
- [ ] SQL query caching with encryption
- [ ] Penetration testing
- [ ] Security monitoring

---

## Troubleshooting

### Common Issues

**"Not authenticated" error**
- Check session middleware is configured
- Verify cookies are enabled in browser
- Ensure callback URL matches Auth0 configuration

**AI service unavailable**
- Check `GITHUB_TOKEN` is valid
- Verify network connectivity to Azure OpenAI
- Check logs for rate limiting

**CORS errors**
- Ensure frontend origin is in `ALLOWED_ORIGINS`
- Update `REACT_APP_URL` in `.env` to match frontend

**Database connection issues**
- Verify `DATABASE_URL` is correct
- Check PostgreSQL is running
- Verify network connectivity

**GraphQL query errors**
- Check SQL syntax is valid
- Verify query is SELECT only
- Check query length (max 10,000 characters)

---

## Contributing

### Guidelines
- Follow existing code style and conventions
- Add tests for new features
- Update documentation as needed
- Ensure all tests pass before submitting PR
- Keep commits focused and write clear commit messages

### Development Setup
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests and linting
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

---

## Support & Resources

### Documentation
- **Main README:** [README.md](README.md)
- **Architecture:** [ARCHITECTURE.md](ARCHITECTURE.md)
- **Security:** [SECURITY.md](SECURITY.md)
- **Docker Setup:** [DOCKER_README.md](DOCKER_README.md)

### External Resources
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [React Documentation](https://react.dev/)
- [Auth0 Documentation](https://auth0.com/docs)
- [GraphQL Documentation](https://graphql.org/)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)

### Reporting Issues
- **Issues:** [GitHub Issues](https://github.com/keniz01/read_only_database_explorer/issues)
- **Discussions:** [GitHub Discussions](https://github.com/keniz01/read_only_database_explorer/discussions)

### Security Issues
**IMPORTANT:** Do NOT create public GitHub issues for security vulnerabilities.

For security issues, please email: [Add your security contact email]

Allow 30 days for response before public disclosure.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Acknowledgments

- Auth0 for authentication infrastructure
- FastAPI for the excellent Python framework
- React team for the frontend framework
- The open-source community

---

**Last Updated:** January 2026  
**Version:** 1.1.0  
**Status:** Production Ready

---

*Made with ❤️ for secure database exploration*

