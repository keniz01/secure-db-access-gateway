# GEMINI.md - Read-Only Database Explorer

## Project Overview
A secure, full-stack web application for safe database exploration and querying with read-only access. It employs a multi-service architecture to separate authentication, data access, and the user interface.

### Main Technologies
- **Frontend:** React 19, TypeScript, Vite, Tailwind CSS 4, TanStack Query.
- **Auth0 API (Backend):** FastAPI (Python 3.12+), Auth0 OAuth2, Azure OpenAI (for personalized greetings).
- **SQL Query API (Backend):** FastAPI (Python 3.12+), Strawberry GraphQL, SQLAlchemy (asyncpg), PostgreSQL.
- **Infrastructure:** Nginx (reverse proxy), Docker Compose.

### Architecture
1.  **Web Application (`web-app/`):** React-based UI on port `5173`.
2.  **Auth0 API (`auth0_api/`):** Handles authentication and user sessions on port `8001`.
3.  **SQL Query API (`sql_query_api/`):** Executes validated SQL queries via GraphQL on port `8002`.
4.  **Nginx (`nginx/`):** Acts as a gateway on port `8080`, proxying to the Auth0 API.

---

## Building and Running

### 🐳 Docker (Recommended)
1.  **Setup Secrets:**
    ```bash
    ./setup-secrets.sh
    # Edit the files in the `secrets/` directory with actual credentials.
    ```
2.  **Start the Stack:**
    ```bash
    docker-compose up --build
    ```
    - Web App: `http://localhost:5173`
    - Nginx Gateway: `http://localhost:8080`
    - Auth0 API: `http://localhost:8001`
    - SQL Query API: `http://localhost:8002`

### 🛠️ Manual Setup (Development)

#### 1. SQL Query API
```bash
cd sql_query_api
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py  # Runs on port 8002
```

#### 2. Auth0 API
```bash
cd auth0_api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py  # Runs on port 8001
```

#### 3. Web Application
```bash
cd web-app
npm install
npm run dev  # Runs on port 5173
```

---

## Development Conventions

### Python (Backend Services)
- **Framework:** FastAPI with asynchronous programming (`async/await`).
- **Dependency Management:** `pyproject.toml` and `uv.lock`.
- **Linting & Formatting:** `ruff` is used for linting in `sql_query_api`.
- **Architecture:** Modular service architecture with dependency injection and the repository pattern (especially in `sql_query_api`).
- **SQL Security:** Strict validation in `sql_query_api/repositories/sql_validators/`. Only `SELECT` statements are permitted.

### TypeScript/React (Frontend)
- **Framework:** React 19 with Vite and Tailwind 4.
- **State Management:** TanStack Query for server state; React Context for authentication.
- **API Communication:** Axios with interceptors for `httpOnly` cookie handling.
- **Styling:** Tailwind CSS 4 with `@tailwindcss/vite` plugin.

### Security Standards
- **Authentication:** Auth0 OAuth2 integration.
- **Token Storage:** JWT tokens stored in `httpOnly` cookies to mitigate XSS.
- **SQL Safety:** Automatic `LIMIT` enforcement and blocking of DDL/DML operations.
- **CORS:** Restrictive origin whitelisting across all APIs.
- **Logging:** Structured logging with correlation IDs for request tracing.

### Testing
- **Backend:** `pytest` is the preferred testing framework.
- **Frontend:** `npm test` (intended, but verify configuration in `package.json`).
