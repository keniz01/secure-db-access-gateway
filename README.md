# Read-Only Database Explorer

A secure, full-stack web application for safely exploring and querying databases with read-only access. Built with React, FastAPI, and Auth0 authentication.

## 🌟 Features

- **Read-only SQL execution** with SELECT-only validation, automatic LIMITs, and query timeout enforcement
- **Schema browser** and dynamic schema introspection for table/column discovery
- **Auth0-based auth** with session cookies and viewer/admin role enforcement
- **Operational controls**: rate limiting, audit logging, correlation IDs, and optional OTEL/Grafana metrics
- **Multi-tenant scoping** via `org_id`, tenant-aware rate limiting, and per-org usage metrics
- **Admin overview** for org usage and DB metadata visibility without local credential storage
- **Sensitive-column masking** and row-level filtering hooks for authorization boundaries

## ✅ Completed milestone status

All major project milestones are complete:

- schema decoupling
- authorization and row filtering
- operational readiness
- multi-tenancy

## 🏗️ Architecture

This project consists of three main components:

### 1. **Web Application** (`web-app/`)
- **Tech Stack**: React 19, TypeScript, Vite, Tailwind
- **Purpose**: UI for query execution, schema browsing, and auth flow

### 2. **SQL Query API** (`sql_query_api/`)
- **Tech Stack**: FastAPI, Strawberry GraphQL, SQLAlchemy, asyncpg
- **Purpose**: Executes validated read-only queries and exposes schema/usage metadata
- **Security**: query validation, allowlists, masking, row filtering, RBAC, rate limiting

### 3. **Auth0 API** (`auth0_api/`)
- **Tech Stack**: FastAPI, Auth0, Azure OpenAI optional greeting
- **Purpose**: auth flow, session management, org-aware user data, admin overview

## 🚀 Quick Start

### Option 1: Docker (Recommended)

```bash
docker-compose up --build
```

This starts:
- Auth0 API on `http://localhost:8001`
- SQL Query API on `http://localhost:8002`
- Web app on `http://localhost:5173`
- Grafana/OTel stack on `http://localhost:3000`

Secrets are read from `secrets/` and environment variables/files as needed.

### Option 2: Manual Setup

For development without Docker:

### Prerequisites

- **Python**: 3.11 or higher
- **Node.js**: 18.x or higher
- **PostgreSQL**: Database instance (or compatible SQL database)
- **Auth0 Account**: For authentication setup

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/keniz01/read_only_database_explorer.git
   cd read_only_database_explorer
   ```

2. **Set up the SQL Query API**
   ```bash
   cd sql_query_api
   
   # Create virtual environment
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   
   # Install dependencies
   pip install -r requirements.txt
   
   # Create .env file
   cp .env.example .env
   # Edit .env with your database credentials
   ```

3. **Set up the Auth0 API**
   ```bash
   cd ../auth0_api
   
   # Create virtual environment
   python -m venv venv
   source venv/bin/activate
   
   # Install dependencies
   pip install -r requirements.txt
   
   # Create .env file
   cp .env.example .env
   # Edit .env with your Auth0 credentials
   ```

4. **Set up the Web Application**
   ```bash
   cd ../web-app
   
   # Install dependencies
   npm install
   
   # Create .env file
   cp .env.example .env
   # Edit .env with API endpoints
   ```

### Configuration

#### Auth0 API Configuration (`.env`)
```env
AUTH0_DOMAIN=your-domain.auth0.com
AUTH0_CLIENT_ID=your_client_id
AUTH0_CLIENT_SECRET=your_client_secret
APP_SECRET_KEY=generate-strong-random-key-min-32-chars
SESSION_SECRET_KEY=generate-strong-random-key-min-32-chars
CORS_ORIGINS=http://localhost:5173
ENABLE_AI_GREETING=true
AUTH0_ORG_ID_CLAIM=org_id
GRAFANA_PROMETHEUS_URL=http://localhost:9090
```

#### SQL Query API Configuration (`.env`)
```env
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db
CORS_ORIGINS=http://localhost:5173
LOG_LEVEL=INFO
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_POOL_TIMEOUT_SECONDS=30
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-lgtm:4318
```

#### Web Application Configuration (`.env`)
```env
VITE_API_BASE_URL=http://localhost:8080
VITE_SQL_GRAPHQL_BASE_URL=http://localhost:8002/graphql
VITE_ADMIN_EMAILS=admin@example.com
```

### Running the Application

Start all three services in separate terminals:

1. **Start SQL Query API**
   ```bash
   cd sql_query_api
   source venv/bin/activate
   uvicorn main:app --reload --port 8001
   ```

2. **Start Auth0 API**
   ```bash
   cd auth0_api
   source venv/bin/activate
   uvicorn app.main:app --reload --port 8000
   ```

3. **Start Web Application**
   ```bash
   cd web-app
   npm run dev
   ```

Access the application at `http://localhost:5173`

## 🔒 Security Features

This application implements comprehensive security measures:

### Authentication & Authorization
- **Auth0 Integration** - Industry-standard authentication
- **httpOnly Cookies** - Protects JWT tokens from XSS attacks
- **CSRF Protection** - Custom headers for request validation
- **Session Management** - Secure session handling

### Input Validation
- **Query Length Limits** - Maximum 10,000 characters
- **Query Type Validation** - Only SELECT statements allowed
- **SQL Safety Checks** - Prevents subqueries, CTEs, DDL, DML
- **Empty Query Detection** - Validates non-empty input

### Network Security
- **Strict CORS** - Whitelisted origins only
- **Security Headers** - X-Frame-Options, X-Content-Type-Options, etc.
- **HTTPS Enforcement** - Strict-Transport-Security header
- **Rate Limiting Ready** - Architecture supports rate limiting

### Error Handling
- **Generic Error Messages** - Prevents information disclosure
- **Detailed Logging** - Server-side error tracking
- **Try-Catch Protection** - Graceful error recovery

For complete security details, see [SECURITY.md](SECURITY.md)

## 📚 API Documentation

### SQL Query API Endpoints

#### Execute Query
```http
POST /api/v1/query/execute
Content-Type: application/json

{
  "sql": "SELECT * FROM users LIMIT 10"
}
```

**Response:**
```json
{
  "columns": ["id", "name", "email"],
  "rows": [
    [1, "John Doe", "john@example.com"],
    [2, "Jane Smith", "jane@example.com"]
  ],
  "row_count": 2
}
```

### Auth0 API Endpoints

#### Login
```http
POST /api/auth/login
```

#### Logout
```http
POST /api/auth/logout
```

#### Verify Session
```http
GET /api/auth/verify
```

## 🛠️ Development

### Project Structure
```
read_only_database_explorer/
├── auth0_api/              # Authentication service
│   ├── app/
│   │   ├── config/         # Configuration settings
│   │   ├── middleware/     # CORS, security headers
│   │   └── routes/         # Auth endpoints
│   └── requirements.txt
├── sql_query_api/          # Query execution service
│   ├── routes/             # API endpoints
│   ├── services/           # Business logic
│   ├── models/             # Data models
│   └── requirements.txt
├── web-app/                # React frontend
│   ├── src/
│   │   ├── components/     # React components
│   │   ├── services/       # API clients
│   │   └── pages/          # Application pages
│   └── package.json
├── ARCHITECTURE.md         # Architecture documentation
├── SECURITY.md            # Security implementation guide
└── README.md              # This file
```

### Technology Stack

**Frontend:**
- React 18
- TypeScript
- Vite
- Axios
- React Router

**Backend:**
- FastAPI
- Python 3.11+
- PostgreSQL
- Auth0
- Pydantic

### Running Tests

```bash
# Backend tests (when available)
cd sql_query_api
pytest

cd ../auth0_api
pytest

# Frontend tests (when available)
cd ../web-app
npm test
```

### Code Quality

```bash
# Python linting
cd sql_query_api
flake8 .
black .

# TypeScript linting
cd web-app
npm run lint
```

## 📖 Documentation

- [Architecture Overview](ARCHITECTURE.md) - System design and component interaction
- [Security Guide](SECURITY.md) - Comprehensive security implementation details
- API Documentation - Available at `/docs` endpoint when running the APIs

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Contribution Guidelines

- Follow existing code style and conventions
- Add tests for new features
- Update documentation as needed
- Ensure all tests pass before submitting PR
- Keep commits focused and write clear commit messages

## 🐛 Reporting Security Issues

**IMPORTANT**: Do NOT create public GitHub issues for security vulnerabilities.

For security issues, please email: kenneth.kiiza@googlemail.com

Allow 30 days for response before public disclosure.

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Auth0 for authentication infrastructure
- FastAPI for the excellent Python framework
- React team for the frontend framework
- The open-source community

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/keniz01/read_only_database_explorer/issues)
- **Discussions**: [GitHub Discussions](https://github.com/keniz01/read_only_database_explorer/discussions)
- **Documentation**: See the `/docs` folder

## 🗺️ Roadmap

### Completed Features
- [x] Docker containerization (Docker Compose & Nginx gateway)

### Planned Enhancements
- [ ] Add query history tracking
- [ ] Implement query result export (CSV, JSON)
- [ ] Add database schema visualization
- [ ] Support for multiple database connections
- [ ] Query performance metrics
- [ ] Saved queries functionality
- [ ] Role-based access control
- [ ] Audit logging
- [ ] Rate limiting implementation

## 📊 Project Status

**Current Version**: 1.1.0  
**Status**: Development  
**Last Updated**: August 2026

---

**Made with ❤️ for secure database exploration**
