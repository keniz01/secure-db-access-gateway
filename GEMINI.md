# GEMINI.md - Secure DB Access Gateway & AI Guardrails

This document serves as the primary system prompt and guardrail for future Gemini/Antigravity development sessions in this codebase.

## 1. Project Global Context
The Secure DB Access Gateway is a secure, full-stack application designed to safely inspect, query, and document relational databases with **read-only access**. The architecture separates user sessions, SQL validation, and user interface layers to ensure zero-risk database exploration.

### Core Technology Stack
- **Frontend:** React 19, TypeScript, Vite, Tailwind CSS 4, TanStack Query.
- **Auth0 API (Backend):** FastAPI (Python 3.12+), Auth0 OAuth2, Azure OpenAI (for greetings).
- **SQL Query API (Backend):** FastAPI (Python 3.12+), Strawberry GraphQL, SQLAlchemy (asyncpg), PostgreSQL.
- **Headless CLI Utility:** Python 3, SQLite/PostgreSQL drivers, Gemini API.
- **Infrastructure:** Nginx (reverse proxy gateway), Docker Compose.

---

## 2. Standard Dev Setup, Run, and Test Commands

### 🐳 Docker Compose (Recommended)
1. Bootstrap the env file and (dev) TLS certificate:
   ```bash
   ./scripts/bootstrap-dev.sh
   # Fill in real credentials in .env (copied from .env.example).
   ```
2. Build and start the stack:
   ```bash
   docker compose up --build
   ```
   - Web App: browse via the TLS edge at `https://localhost:8443` (Vite dev server on `http://localhost:5173` is proxied)
   - Nginx Gateway: `https://localhost:8443` (TLS; `http://localhost:8080` redirects to HTTPS)
   - Auth0 API: `http://localhost:8001`
   - SQL Query API: `http://localhost:8002`

### 🛠️ Manual Setup (Development)

#### 1. SQL Query API & CLI Runner
```bash
cd sql_query_api && uv sync && uv run uvicorn main:app --reload --port 8002
# or: pip install -e ../shared && pip install -e . && ENVIRONMENT=dev python main.py
```

#### 2. Auth0 API
```bash
cd auth0_api && uv sync && uv run uvicorn main:app --reload --port 8001
# or: pip install -e ../shared && pip install -e . && ENVIRONMENT=dev python main.py
```

#### 3. Web Application
```bash
cd web-app
npm install
npm run dev  # Runs UI on port 5173
```

---

## 3. Headless Mode CLI & AI Documentation Utilities

The CLI runner `explore.py` is located at the root of the repository. It auto-detects and re-executes itself in the `.venv` of the SQL Query API to ensure dependencies are resolved.
It is an intentionally separate local operator utility and does not pass
through the Auth0-governed GraphQL tenant resolver. It must not be presented
as a governed multi-tenant access path.

### Direct Terminal Queries (Headless Mode)
Output results cleanly to standard output (stdout) as JSON or CSV:
```bash
DATABASE_URL="$DATABASE_URL" ./explore.py --table artist --access-token-file "$CLI_TOKEN_FILE" --format json --limit 10
DATABASE_URL="$DATABASE_URL" ./explore.py --sql "SELECT title, release_year FROM album" --access-token-file "$CLI_TOKEN_FILE" --format csv
```

The CLI requires a validated OIDC/Auth0 access token for database queries. Set
`CLI_ACCESS_TOKEN` or `CLI_ACCESS_TOKEN_FILE`; roles, tenant identity, subject
attributes, issuer, audience, signature, and expiry are derived from the
validated token. The CLI does not accept locally configured roles or tenant
identity as authorization inputs. Use a short-lived user token for interactive
access and a workload/service-account token for automation.

### Autonomous AI Schema-to-Wiki Generator
Crawl the active database schema, map relations, and generate a cross-linked Markdown documentation wiki:
```bash
DATABASE_URL="$DATABASE_URL" ./explore.py --generate-wiki docs/wiki
```

### Intelligent Data Audit/Diagnostic Mode
Pass a query result profile or logs into the tool (via query or stdin) to return a 3-line expert diagnostic summary pointing out anomalies, NULL clusters, or trends:
```bash
# Analyze query results directly:
DATABASE_URL="$DATABASE_URL" ./explore.py --table track --limit 10 --analyze

# Analyze piped logs or data stream:
cat db_logs.log | ./explore.py --analyze
```

---

## 4. System Guardrails and Constraints for AI Agents

When modifying this repository or writing code, AI agents **MUST** strictly adhere to the following guardrails:

### 🔒 1. Absolute Enforcement of Read-Only Database Driver Flags
To prevent any possibility of database modification, read-only constraints must be enforced at the driver/connection level:
- **SQLite:** Connection URIs must always append `?mode=ro` (e.g. `file:data.db?mode=ro`) and open the connection in URI mode.
- **PostgreSQL:** Every connection/transaction block must execute `SET TRANSACTION READ ONLY;` immediately upon opening.

### 🚫 2. Zero Permission Vectors for State-Altering Queries
Under no circumstances should any query other than simple `SELECT` statements be executed:
- All SQL strings must pass through the `DefaultSqlSafetyChecker` or equivalent safety validation rules.
- State-altering keywords (such as `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`, `COMMIT`, `ROLLBACK`) are strictly prohibited and must fail validation.
- Avoid exposing raw database execution errors to clients; log errors server-side and return generic, safe error messages.

### 🔑 3. Mandatory Credentials Extraction
- Hardcoding passwords, secrets, or API keys in code or configuration files is strictly forbidden.
- Extract all credentials, database URLs, and API keys via environment variables or the shared `shared_secrets.read_secret` loader (which reads `NAME` env vars and falls back to an injected `NAME_FILE` path).

### ⚙️ 4. Non-Interactive CLI Layers
- CLI tools must support non-interactive parameter overrides (`--headless`, `--json`) and read standard input safely without blocking (using `select.select` on stdin).
- Output must stream cleanly to standard output, making it easily parseable by external automation scripts.
