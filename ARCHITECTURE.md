# Read-Only Database Explorer - System Architecture

## Overview

The Read-Only Database Explorer is a full-stack application designed to provide secure, read-only access to databases through a modern web interface. The system is composed of three interconnected services that work together to deliver authentication, query execution, and a responsive user interface.

```
┌─────────────────────────────────────────────────────────────────┐
│                      Web Application (React)                     │
│              React 19 + TypeScript + Vite + Tailwind             │
│                                                                   │
│  • Login & Auth Callback                                         │
│  • Protected Dashboard with User Stats                           │
│  • Token Management & Session Persistence                        │
└────────────────────┬──────────────────────┬──────────────────────┘
                     │                      │
       ┌─────────────┘                      └─────────────┐
       │                                                   │
       ▼                                                   ▼
┌─────────────────────────────────────┐  ┌────────────────────────────┐
│      Auth0 API (FastAPI)            │  │   SQL Query API (FastAPI)   │
│      Port: 8001                     │  │   Port: 8002                │
│                                     │  │                             │
│  • OAuth2/Auth0 Integration         │  │  • GraphQL Endpoint        │
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

## Architecture Components

### 1. Frontend: Web Application

**Location:** `/web-app`

**Technology Stack:**
- React 19 with TypeScript
- Vite (build tool)
- Tailwind CSS (styling)
- TanStack React Query (data fetching & caching)
- React Router DOM (routing)
- Axios (HTTP client)

**Key Features:**

#### Authentication Flow
- **Login Page:** Beautiful gradient UI with Auth0 login integration
- **Auth Callback Handler:** Processes OAuth2 callback from Auth0
- **Token Management:** Stores authentication state in localStorage with httpOnly cookies
- **Protected Routes:** Dashboard accessible only when authenticated

#### Components
- `LoginPage` - Initial login interface with Auth0 button
- `AuthCallback` - Handles OAuth2 redirect and token processing
- `Dashboard` - Protected main application interface with:
  - User profile information
  - AI-generated personalized greeting
  - Dashboard statistics

#### Services & Utilities
- **api-client.ts** - Axios wrapper with automatic token injection via httpOnly cookies
- **auth-api.tsx** - Auth0-specific API endpoints
- **use-auth.tsx** - Custom hook for authentication state management
- **AuthProvider** - Context provider for authentication state

#### Security Measures
- ✅ httpOnly cookies for JWT tokens (prevents XSS attacks)
- ✅ Automatic token inclusion in API requests
- ✅ Input validation with error recovery
- ✅ Protected route guards

**Port:** `5173` (Vite dev server)

**Entry Point:** [index.html](index.html) → [main.tsx](web-app/src/main.tsx)

---

### 2. Backend: Auth0 API

**Location:** `/auth0_api`

**Technology Stack:**
- FastAPI (Python web framework)
- Auth0 (OAuth2/OIDC provider)
- Azure OpenAI (for AI-powered greetings)
- Authlib (OAuth2 client library)
- Python-JOSE (JWT handling)

**Architecture Pattern:** Modular service architecture with clear separation of concerns

#### Project Structure

```
auth0_api/
├── app/
│   ├── config/              # Configuration management
│   │   ├── settings.py      # Environment-based settings
│   │   └── logging.py       # Logging configuration
│   ├── auth/                # Authentication logic
│   │   ├── oauth.py         # Auth0 OAuth2 setup
│   │   └── session.py       # User sessions & JWT
│   ├── routes/              # API endpoints
│   │   ├── auth_routes.py   # Authentication endpoints
│   │   └── user_routes.py   # User info & dashboard
│   ├── services/            # Business logic
│   │   └── ai_service.py    # LLM/Azure OpenAI integration
│   ├── middleware/          # Cross-cutting concerns
│   │   └── setup.py         # CORS & session middleware
│   ├── schemas/             # Request/response models
│   │   └── responses.py     # Data validation
│   ├── exceptions/          # Custom exceptions
│   │   └── handlers.py      # Exception definitions
│   └── utils/               # Helper functions
│       └── helpers.py       # Utility functions
├── factory.py               # Application factory
└── main.py                  # Entry point
```

#### Key Endpoints

**Health & Authentication:**
- `GET /api/health` - Health check
- `GET /api/login` - Initiates Auth0 login flow
- `GET /api/auth` - OAuth2 callback handler
- `GET /api/logout` - Clears session

**User Information:**
- `GET /api/user` - Returns authenticated user profile
- `GET /api/dashboard` - Returns user info with AI-generated greeting

#### Core Services

**OAuth2 Integration (`auth/oauth.py`)**
- Configures Auth0 as OAuth2 provider
- Handles authorization code flow
- Token management and validation
- User information retrieval

**AI Service (`services/ai_service.py`)**
- Generates personalized greetings using Azure OpenAI
- Implements retry logic for resilience
- Singleton pattern for efficiency

**Configuration (`config/settings.py`)**
- Centralized environment variable management
- Type-safe settings with defaults
- Multi-environment support

#### Middleware & Error Handling

**Security Middleware (`middleware/setup.py`)**
- ✅ CORS with configurable origins (production-ready)
- ✅ Session management for OAuth state
- ✅ Security headers (X-Content-Type-Options, X-Frame-Options, etc.)

**Exception Handling (`exceptions/handlers.py`)**
- Custom exceptions for specific error scenarios
- Proper HTTP status codes
- Comprehensive error logging

**Port:** `8001`

**Entry Point:** [main.py](auth0_api/main.py)

---

### 3. Backend: SQL Query API

**Location:** `/sql_query_api`

**Technology Stack:**
- FastAPI (Python web framework)
- GraphQL (via Strawberry GraphQL)
- SQLAlchemy (async ORM)
- PostgreSQL (database driver: asyncpg)
- Loguru (logging)

**Architecture Pattern:** Dependency injection with abstract service layer and repository pattern

#### Project Structure

```
sql_query_api/
├── config/
│   └── app_logger.py        # Loguru configuration
├── dependencies/
│   └── dependency_container.py  # Dependency injection setup
├── exceptions/              # Custom exceptions
│   ├── exception_handlers.py
│   ├── forbidden_sql_statement_exception.py
│   └── sql_statement_execution_exception.py
├── graphql_schema/
│   └── schema.py            # GraphQL schema definition
├── middlewares/             # HTTP middlewares
│   ├── correlation_middleware.py  # Request tracing
│   └── logging_middleware.py      # Request/response logging
├── repositories/            # Data access layer
│   ├── abstract_music_query_repository.py  # Interface
│   ├── music_query_repository.py   # Implementation
│   └── sql_validators/      # SQL validation rules
│       └── sql_safety_checker.py
├── routes/                  # API controllers
│   └── music_query_controller.py
├── services/                # Business logic
│   ├── abstract_music_query_service.py  # Interface
│   └── music_query_service.py   # Implementation
├── main.py                  # Entry point
└── docker-compose.yml       # PostgreSQL setup
```

#### GraphQL Schema

**Root Query Operations:**

```graphql
type Query {
  executeQuery(sql: String!, params: JSON): [QueryResult!]!
  getTableSchema(embeddings: [Float!]!): SchemaInfo!
}
```

#### Core Components

**Repository Layer (`repositories/`)**
- Abstract interface (`IMusicQueryRepository`) for testability
- Concrete implementation with async database operations
- SQL safety validation before execution
- Automatic LIMIT enforcement to prevent runaway queries
- Schema context management (music & meta schemas)

**SQL Safety Validation (`repositories/sql_validators/`)**
- Prevents DDL (CREATE, ALTER, DROP) statements
- Blocks DML (INSERT, UPDATE, DELETE) statements
- Restricts subqueries and CTEs
- Allows only simple SELECT statements
- Validates query length (max 10,000 characters)

**Service Layer (`services/`)**
- Abstract interface (`IMusicQueryService`) for dependency injection
- Coordinates between repository and GraphQL endpoints
- Business logic and error handling
- Async/await patterns for non-blocking operations

**GraphQL Controller (`routes/music_query_controller.py`)**
- Strawberry GraphQL resolver definitions
- Input validation and parameter handling
- Integration with service layer

#### Middleware & Observability

**Correlation Middleware (`middlewares/correlation_middleware.py`)**
- Generates unique request IDs for tracing
- Tracks requests through the system
- Enhances logging with correlation context

**Logging Middleware (`middlewares/logging_middleware.py`)**
- Structured logging for all HTTP requests/responses
- Performance metrics
- Error tracking

**Logging Configuration (`config/app_logger.py`)**
- Loguru-based centralized logging
- Correlation ID injection in all logs
- Development and production-ready formats

#### Security

**CORS Configuration**
- ✅ Restrictive origin whitelist
- ✅ Limited HTTP methods (GET, POST, OPTIONS)
- ✅ Preflight caching for performance

**SQL Injection Prevention**
- ✅ Parameterized queries with SQLAlchemy
- ✅ SQL statement validation before execution
- ✅ Query type restrictions (SELECT only)

**Response Headers**
- ✅ X-Content-Type-Options: nosniff
- ✅ X-Frame-Options: DENY
- ✅ X-XSS-Protection: 1; mode=block
- ✅ Strict-Transport-Security

**Port:** `8002`

**Entry Point:** [main.py](sql_query_api/main.py)

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
    │   ├─→ Validate SQL safety
    │   ├─→ Apply automatic LIMIT
    │   ├─→ Execute on PostgreSQL
    │   └─→ Return results as JSON
    │
    └─→ Display results in table
```

### 3. Token Management

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
- **OAuth2 with Auth0:** Enterprise-grade authentication
- **JWT Tokens:** Secure, stateless authentication
- **httpOnly Cookies:** Prevents XSS token theft
- **Session Management:** Secure state handling during OAuth flow

### API Security
- **CORS:** Restrictive origin whitelisting
- **SQL Injection Prevention:** Parameterized queries + validation
- **Read-Only Access:** All queries limited to SELECT statements
- **Query Limits:** Automatic LIMIT clauses prevent DoS
- **Input Validation:** Length and type checks
- **Security Headers:** Comprehensive HTTP security headers

### Network Security
- **HTTPS/TLS:** Strict transport security headers
- **Secure Cookies:** HttpOnly, Secure, SameSite attributes
- **Correlation IDs:** Request tracing for audit logs

---

## Technology Stack Summary

### Frontend
| Technology | Purpose |
|-----------|---------|
| React 19 | UI framework |
| TypeScript | Type safety |
| Vite | Build tool & dev server |
| Tailwind CSS | Styling |
| TanStack Query | Data fetching & caching |
| React Router | Client-side routing |
| Axios | HTTP client |

### Backend (Auth API)
| Technology | Purpose |
|-----------|---------|
| FastAPI | Web framework |
| Auth0 | OAuth2 provider |
| Authlib | OAuth2 client |
| Azure OpenAI | AI service |
| Python-JOSE | JWT handling |

### Backend (Query API)
| Technology | Purpose |
|-----------|---------|
| FastAPI | Web framework |
| Strawberry GraphQL | GraphQL framework |
| SQLAlchemy | ORM |
| AsyncPG | PostgreSQL async driver |
| Loguru | Logging |

### Infrastructure
| Technology | Purpose |
|-----------|---------|
| PostgreSQL | Primary database |
| Docker Compose | Local development |

---

## Deployment Architecture

### Development Environment

```
localhost:5173      → Web App (Vite)
localhost:8001      → Auth0 API
localhost:8002      → SQL Query API
localhost:5432      → PostgreSQL (Docker)
```

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

---

## Development Workflow

### Starting All Services

1. **PostgreSQL**
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

### Project Dependencies
- Python 3.12+ (Auth & Query APIs)
- Node.js 18+ (Web App)
- PostgreSQL 14+ (Database)
- Auth0 account (Authentication)
- Azure OpenAI API key (AI features)

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

## Conclusion

The Read-Only Database Explorer follows modern architectural principles with clear separation between authentication, data access, and presentation layers. The system prioritizes security, scalability, and maintainability through modular design, dependency injection, and comprehensive logging.
