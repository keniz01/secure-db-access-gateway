# Threat Model — Secure DB Access Gateway

**Version:** 1.2
**Date:** 2026-10-04
**Methodology:** PASTA (high-level workflow) → STRIDE (per-component threat enumeration) → DREAD (scoring & prioritization)
**Scope:** All services in `docker-compose.yml` plus the nginx TLS edge and external dependencies (Auth0, PostgreSQL tenants, OpenRouter AI, OPA sidecar)

---

## 1. PASTA — Business Workflow & Attack Surface

### 1.1 Core Business Process

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐     ┌───────────────┐     ┌──────────────────┐
│   User      │────▶│   Auth0      │────▶│  Web App (SPA)  │────▶│  SQL Query    │────▶│  Tenant DBs      │
│  (Browser)  │     │  (IdP)       │     │  (React/Vite)   │     │  API (GraphQL)│     │  (PostgreSQL)    │
└─────────────┘     └──────────────┘     └─────────────────┘     └───────────────┘     └──────────────────┘
                          │                    │                       │                      │
                          │                    │                       │                      │
                          ▼                    ▼                       ▼                      ▼
                   ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌─────────────────┐
                   │  Session     │     │  AI / Text-  │     │   OPA        │     │  Credential     │
                   │  Store       │     │  to-SQL      │     │  (Policy)    │     │  Rotator        │
                   │  (Redis)     │     │  (OpenRouter)│     │              │     │                 │
                   └──────────────┘     └──────────────┘     └──────────────┘     └─────────────────┘
```

**Trust Boundaries (TB):**
- **TB-1:** Internet → Nginx (TLS termination, rate limiting, header sanitization)
- **TB-2:** Nginx → Backend services (Auth0 API, SQL Query API, Web App) — spoofable headers stripped
- **TB-3:** Backend → OPA / Redis / Credential Rotator / Tenant DBs — mutual TLS not used; network segmentation only
- **TB-4:** SQL Query API → External AI (OpenRouter) — API key in env, no mTLS

### 1.2 Critical Assets

| Asset | Description | Classification |
|-------|-------------|----------------|
| Tenant database credentials | PostgreSQL connection strings per org/database | **CROWN JEWEL** |
| Auth0 client secret / JWT signing keys | Session issuance & validation | **CROWN JEWEL** |
| Audit logs | Tamper-evident record of all governed queries | High |
| OPA policy bundles | Authorization logic (allow/deny, row scope, masking) | High |
| Query results / result sets | Tenant data returned to authenticated users | High |
| Redis session tokens | HttpOnly cookie session identifiers | High |
| AI API keys | OpenRouter credentials for Text-to-SQL | Medium |

### 1.3 Key Data Flows

| Flow | Protocol | Trust Boundary | Controls |
|------|----------|----------------|----------|
| User login | HTTPS → Auth0 OIDC | TB-1, TB-2 | PKCE, httpOnly Secure cookies, CSRF tokens |
| GraphQL query | HTTPS → Nginx → SQL API | TB-1, TB-2 | JWT Bearer, RBAC middleware, policy eval |
| SQL execution | PostgreSQL wire protocol | TB-3 | `SET ROLE` readonly, auto-LIMIT, cost/row/byte caps |
| Policy eval | HTTP → OPA sidecar | TB-3 | Fail-closed, 5s timeout |
| Session store | Redis protocol | TB-3 | `SETEX` TTL, password-protected |
| Credential rotation | PostgreSQL wire protocol | TB-3 | Daily rotation, shared volume (ro for SQL API) |
| AI completion | HTTPS → OpenRouter | TB-4 | API key, prompt sanitization |

---

## 2. STRIDE — Per-Component Threat Enumeration

### 2.1 Nginx TLS Edge (`nginx/`)

| ID | Threat | STRIDE | Description | Existing Controls | Gap |
|----|--------|--------|-------------|-------------------|-----|
| N-1 | TLS downgrade / cert spoofing | **S**poofing, **T**ampering | Attacker presents invalid cert or strips TLS | mkcert dev certs; prod uses real CA; HSTS preload | None |
| N-2 | Header injection / log forging | **T**ampering | Malicious `X-Correlation-ID`, `X-Forwarded-For` | Charset/length validation (`^[A-Za-z0-9._-]{1,128}$`); fallback to `$request_id` | None |
| N-3 | Spoofed identity headers | **S**poofing | Client sends `X-User-*`, `X-Org-*`, `X-Tenant-*` | Stripped at nginx (`proxy_set_header X-User-Email ""` etc.) | None |
| N-4 | Rate limit bypass | **D**oS | Distributed requests evade IP-based limits | `api_limit` 60r/m burst 20; `auth_limit` 5r/m burst 3 at nginx; app-layer principal/tenant/database budgets (`rate_limit_middleware.py`) for authenticated traffic | Edge limit is IP-keyed only — a distributed unauthenticated flood is bounded solely by `api_limit` |
| N-5 | Request smuggling / desync | **T**ampering | Malformed `Content-Length`/`Transfer-Encoding` | `client_max_body_size 1m`; strict proxy buffering | None |
| N-6 | Certificate key theft | **I**nformation disclosure | `web_tls_key.pem` read from container FS | Read-only root FS; `cap_drop ALL`; volume `ro` | None |

### 2.2 Auth0 API (`auth0_api/`)

| ID | Threat | STRIDE | Description | Existing Controls | Gap |
|----|--------|--------|-------------|-------------------|-----|
| A-1 | Auth0 client secret theft | **I**nformation disclosure | `AUTH0_CLIENT_SECRET` leaked from env / CI | `shared_secrets.read_secret()` from env/*_FILE; no hardcoding | None |
| A-2 | Session fixation / hijacking | **S**poofing, **E**levation | Attacker sets known session ID | `secrets.token_urlsafe(32)`; httpOnly Secure cookies; `SameSite=lax` | CSRF token in session but not validated on all state-changing endpoints |
| A-3 | Redis session store compromise | **I**, **T**, **E** | Attacker reads/writes session tokens | Password-protected Redis; `SETEX` TTL; in-memory fallback only in dev | No encryption at rest; Redis not TLS in compose |
| A-4 | JWT claim manipulation | **T**ampering, **E**levation | Forged `tenant_id`, `org_id`, `roles` in token | RS256 validation via Auth0 JWKS; `build_principal_from_claims` trusts only validated claims | No token binding to client cert / device |
| A-5 | CSRF on login/callback | **S**poofing | Cross-site request forgery on `/auth/callback` | `csrf_token` in session; `state` param in OAuth flow | CSRF not enforced on `/logout` (GET) |
| A-6 | Open redirect via `redirect_uri` | **T**ampering | Attacker crafts malicious redirect | `CORS_ORIGINS` allowlist; Auth0 dashboard allowlist | None |
| A-7 | AI prompt injection (Text-to-SQL) | **T**ampering, **E**levation | Malicious user input steers LLM to generate unsafe SQL | `clean_sql()` strips markdown; `SqlSafetyChecker` validates; governed pipeline re-validates | Prompt template not versioned; no input sanitization before LLM |
| A-8 | Auth0 account takeover | **E**levation | Compromised Auth0 admin console | MFA on Auth0 tenant (assumed); not in our control | None (external) |

### 2.3 SQL Query API (`sql_query_api/`)

| ID | Threat | STRIDE | Description | Existing Controls | Gap |
|----|--------|--------|-------------|-------------------|-----|
| S-1 | SQL parser bypass (mutation) | **T**ampering, **E**levation | `INSERT`/`UPDATE`/`DELETE` sneaks past `clean_sql()` | `MustBeSelectRule`, `NoForbiddenKeywordsRule`, `AstSqlAnalyzer.is_strictly_read_only()`, sqlglot AST parse; `clean_sql()` never synthesizes SQL (invalid queries are rejected, not repaired) | None — the AST node-type allowlist is deliberately off, so depth/complexity rather than structure is what the cost/timeout limits bound |
| S-2 | SQL injection via parameters | **T**ampering | Parameterized query bypass | `params` dict passed to asyncpg `execute()` — native parameterization | None |
| S-3 | Tenant breakout (cross-org data access) | **I**, **E**levation | User queries another org's database | `TENANT_DATABASES_JSON` maps `org_id`+`database_id` → conn string; RBAC middleware extracts `org_id` from JWT claim `https://app.secure-db-access-gateway.org/tenant_id`; `TenantDatabaseResolver` enforces mapping | If JWT claim forged (see A-4) or `TENANT_DATABASES_JSON` misconfigured |
| S-4 | OPA policy poisoning | **T**ampering, **E**levation | Attacker modifies OPA policies to allow unauthorized access | Policies mounted `ro` from `sql_query_api/opa/policies/`; bundle mode for prod; OPA sidecar `read_only: true` | If container escape (C-1) or CI/CD compromise (C-3) |
| S-5 | OPA fail-open | **E**levation | OPA unreachable → policy allows all | **Fail-closed**: `OpaPolicyEvaluator` returns `PolicyDecision(False, "OPA policy evaluation failed")` when `None` result | None — correct |
| S-6 | GraphQL DoS (depth/alias) | **D**oS | Deeply nested queries / alias explosion | `QueryDepthLimiter(max_depth=6)`, `MaxAliasesLimiter(max_alias_count=100)` | No per-query cost limit at GraphQL layer (only at SQL layer) |
| S-7 | Schema introspection leakage | **I**nformation disclosure | `get_table_schema` / `introspect_schema` reveals full schema | `effective_access()` filters by policy; `DisableIntrospection` in prod | Authenticated users still see allowed schema — acceptable |
| S-8 | Result-set exfiltration (large data) | **I**, **D**oS | `SELECT * FROM large_table` returns GBs | `SQL_QUERY_MAX_ROW_LIMIT=5000`, `SQL_QUERY_MAX_RESULT_BYTES=5MB`, `SQL_QUERY_TIMEOUT_SECONDS=30s`, `SQL_QUERY_COST_THRESHOLD=16` | Cost estimator may underestimate; no streaming/chunked response |
| S-9 | Audit log tampering | **T**ampering, **R**epudiation | Attacker modifies/deletes audit records | Structured JSON to stdout; SHA256 hash chaining (`audit_hash`/`prev_audit_hash`) so in-place edits break the chain; OTel collector aggregates; no local deletion API | Chain state lives with the log sink — no WORM/remote immutable storage, so an attacker who controls the sink can still rewrite history (detectable only by verifying the chain) |
| S-10 | `AUDIT_LOG_RAW_SQL=true` leaks queries | **I**nformation disclosure | Raw SQL (with PII) written to logs | Default `false`; only enabled by operator | If enabled in prod, sensitive data in logs |
| S-11 | Credential exposure via `creds-rotator` | **I** | Rotated passwords written to shared volume | Volume `creds_data` mounted `ro` to SQL API; `cap_drop ALL`; `read_only` rootfs | Volume not encrypted at rest; the `creds-rotator` container runs as root (no `user:` in compose, image is `postgres:16-alpine` without a `USER`) |
| S-12 | `EXPOSE_DB_ERROR_DETAIL=1` leaks schema | **I** | Detailed PG error names/columns to client | Default `0` (generic messages) | If enabled, aids reconnaissance |
| S-14 | Missing `SET ROLE` enforcement | **E**levation | Connection uses superuser instead of readonly role | `SQL_READONLY_ROLE` env var; production PostgreSQL tenants **fail fast at startup** without it (`sql_query_repository.py`); role name validated before `SET ROLE` | Under `CI` the fail-fast check is skipped |

### 2.4 Web App (`web-app/`)

| ID | Threat | STRIDE | Description | Existing Controls | Gap |
|----|--------|--------|-------------|-------------------|-----|
| W-1 | XSS via query results | **T**ampering, **E**levation | Malicious data in DB rendered in React | React auto-escapes; `ResultsTable`/`ChartRenderer` use safe renderers | `ParagraphRenderer`/`ListRenderer` use `dangerouslySetInnerHTML`? (check) |
| W-2 | JWT in localStorage | **I** | Token stolen via XSS | **HttpOnly cookie only**; `localStorage` holds only the `app_jwt_exists` flag and non-sensitive `user` profile metadata (no token material) | None |
| W-3 | CSP bypass | **T**ampering | Inline script / style injection | `Content-Security-Policy` at nginx + FastAPI dev mirror; `script-src 'self'`; edge/app value parity for every mirrored header asserted in CI | `'unsafe-inline'` for styles — acceptable |
| W-4 | Clickjacking | **S**poofing | App framed in malicious site | `X-Frame-Options: DENY`; `frame-ancestors 'none'` | None |
| W-5 | Mixed content / downgrade | **T**ampering | HTTP resources on HTTPS page | `upgrade-insecure-requests`; HSTS preload | None |

### 2.5 OPA Sidecar (`opa/`)

| ID | Threat | STRIDE | Description | Existing Controls | Gap |
|----|--------|--------|-------------|-------------------|-----|
| O-1 | Policy bundle tampering | **T**ampering, **E**levation | Modified bundle loaded from S3/GCS | Bundles built in CI by a reviewed immutable workflow; fetched over the internal Docker network; policies/data mounted `ro`; OPA sidecar `read_only` + `cap_drop ALL` | No bundle signature verification (`sql_query_api/opa/config.yaml` has no signing/authentication config) and none is enforced in CI |
| O-2 | OPA sidecar compromise | **E**, **I** | Container escape / RCE in OPA | `read_only: true`; `cap_drop ALL`; `no-new-privileges`; non-root | None |
| O-3 | Network eavesdrop on OPA API | **I** | HTTP (not HTTPS) between SQL API → OPA | Internal Docker network `gateway-backend` only | No mTLS; plaintext policy decisions on wire |
| O-4 | Denial via OPA resource exhaustion | **D**oS | Complex Rego policies consume CPU/memory | 5s HTTP client timeout on every decision (`OpaConfig.timeout`); fail-closed on timeout; hardened container (`read_only`, `cap_drop ALL`) | No CPU/memory limits on the OPA container in compose |

### 2.6 Redis Session Store (`redis/`)

| ID | Threat | STRIDE | Description | Existing Controls | Gap |
|----|--------|--------|-------------|-------------------|-----|
| R-1 | Unauthenticated access | **S**, **I** | No password in dev | `REDIS_PASSWORD` env; `requirepass` in command | Dev compose may run without password |
| R-2 | Session token theft | **I** | Sniff Redis traffic | Internal network only | No TLS; `redis:7.4-alpine` supports TLS but not configured |
| R-3 | Memory exhaustion | **D**oS | Unbounded session keys | `SETEX` TTL; `maxmemory-policy` not set | No `maxmemory` limit; could OOM |

### 2.7 Credential Rotator (`creds-rotator/`)

| ID | Threat | STRIDE | Description | Existing Controls | Gap |
|----|--------|--------|-------------|-------------------|-----|
| C-1 | Rotation script injection | **T**, **E** | Malicious `rotate_creds.sh` | Script mounted `ro` from `sql_query_api/scripts/` | If source repo compromised (C-3) |
| C-2 | Admin credential theft | **I** | `pg_rotator_admin_pass` secret | Docker secret `pg_rotator_admin_pass`; file mount | Secret file on host FS; not rotated |
| C-3 | Rotated credential leakage | **I** | Passwords written to `creds_data` volume | Volume `ro` for SQL API; `creds-rotator` writes only | Volume not encrypted; `docker volume inspect` reveals path |

### 2.8 PostgreSQL Tenant Databases (`postgres/`)

| ID | Threat | STRIDE | Description | Existing Controls | Gap |
|----|--------|--------|-------------|-------------------|-----|
| P-1 | Superuser credential reuse | **E** | Same creds for app + admin | `creds-rotator` uses separate admin secret | Application uses rotated creds; but `POSTGRES_PASSWORD` is static superuser |
| P-2 | Missing `SET ROLE` enforcement | **E** | Connection bypasses readonly role | `SQL_READONLY_ROLE` configured per tenant; production refuses to start a PG tenant connection without it (`sql_query_repository.py`) | Under `CI` the fail-fast check is skipped |
| P-3 | Direct DB access bypassing gateway | **E**, **I** | Network path to PostgreSQL from backend network | PostgreSQL is on the private `gateway-backend` network; host port is loopback-only (`127.0.0.1:55432`) | Compromised backend container can connect directly to PostgreSQL; database role remains read-only and schema-scoped |
| P-4 | SQL injection in tenant DB | **T** | Malicious query executes | Read-only role; governed pipeline; parameterized queries | None |

### 2.9 CI/CD & Supply Chain (`.github/workflows/`)

| ID | Threat | STRIDE | Description | Existing Controls | Gap |
|----|--------|--------|-------------|-------------------|-----|
| CI-1 | Malicious dependency injection | **T**, **E** | Compromised PyPI/npm package | `pip-audit` (Python), `npm audit` (Node); `uv.lock` pinned; Docker image provenance is verified with `gh attestation verify` against the pushed digest | Provenance verification is scoped to GitHub’s attestation model; a consumer-side bundle validation step is still recommended for downstream distribution |
| CI-2 | Secret leakage in logs | **I** | `GITHUB_TOKEN`, env vars in CI logs | `gitleaks` secret scan; env vars not echoed | Secrets in `GATEWAY_ENV_FILE` not scanned if file not in repo |
| CI-3 | Build compromise (poisoned image) | **T**, **E** | Attacker modifies Dockerfile / build args | Multi-arch build in a reviewed immutable reusable workflow; full BuildKit provenance (`mode=max`, only public build args) and image SBOM; separate signing job creates and verifies the exact signed bundle for the pushed digest, repository, signer workflow, predicate, source ref and source digest; public tags are promoted only after verification; active default-branch rules require PRs and all CI checks, block deletion/non-fast-forward updates, and resolve review threads; active `v*` tag rules restrict creation to repository admins and prohibit updates/deletion | Repository admins can still change workflows, rulesets, or protected refs through privileged settings; the immutable builder pin must be deliberately reviewed and updated |
| CI-4 | Pre-commit bypass | **T** | Developer skips hooks | `opencode` code-review gate blocks commit; fails closed when the review cannot run | `BLOCK=false` disables; hook can be uninstalled; commits are blocked entirely if `pre-commit` is not on `PATH` |
| CI-5 | Dynamic application security testing | **T**, **I**, **E** | Runtime-only flaw (XSS, path traversal, authz bypass) in the served web surface | `zap-dast` boots the real stack, waits on the TLS edge answering over HTTPS, runs a ZAP baseline scan, publishes JSON/MD/HTML reports, fails if it crawled nothing, and fails on any alert not suppressed with a written justification in `.github/zap/baseline-rules.tsv` (`fail_action: true`); the one header defect the scan found, `Cross-Origin-Resource-Policy: same-site`, is fixed in nginx, auth0_api and sql_query_api | Baseline is passive and only crawls the unauthenticated surface, so the GraphQL API, the governed SQL path and authenticated flows are not exercised dynamically. Six of the seven alerts triaged so far are suppressed rather than fixed (`10015`, `10016`, `10049`, `10055`, `10109`, `10116`), and one suppression is broad: `10055` covers every CSP sub-alert, so a widened `style-src` would never reach the build. The dangerous half is covered elsewhere - CI asserts that every served `script-src` is exactly `'self'` or `'none'`, and that the edge's value for every mirrored header equals the apps' own (the edge hides the apps' copies, so a value edited in only one place would otherwise serve silently differently on direct runs) - and `90004` is deliberately left unsuppressed so a site-isolation regression blocks |

### 2.10 Infrastructure / Container Runtime

| ID | Threat | STRIDE | Description | Existing Controls | Gap |
|----|--------|--------|-------------|-------------------|-----|
| I-1 | Container escape | **E** | Breakout from SQL API / Auth0 / OPA | `read_only: true`; `cap_drop ALL`; `no-new-privileges`; non-root users | `NET_BIND_SERVICE` cap for nginx; backend network reaches PostgreSQL and external services |
| I-2 | Network lateral movement | **E**, **I** | Compromised service attacks peers | Two networks: `frontend` (web+nginx) and `backend` (apis+redis+opa+pg); web cannot reach backend | Backend services can reach each other freely; no zero-trust mesh |
| I-3 | Host kernel exploit | **E** | Container → host | Docker default seccomp; `no-new-privileges` | No gVisor/Kata; host kernel shared |
| I-4 | Image vulnerability | **E** | Base image CVE | `nginx:1.27-alpine`, `redis:7.4-alpine`, `pgvector/pgvector:0.8.0-pg16` (digest-pinned), `python:3.12-slim`; blocking `pip-audit`, `bandit`, blocking Grype and Trivy filesystem scans | No daily base image rebuild; filesystem scans do not cover the published image contents |

### 2.11 External Dependencies

| ID | Threat | STRIDE | Description | Existing Controls | Gap |
|----|--------|--------|-------------|-------------------|-----|
| X-1 | Auth0 compromise | **E**, **I**, **T** | Attacker controls IdP | MFA, breach monitoring (Auth0 responsibility) | None (external) |
| X-2 | OpenRouter API key theft | **I**, **E** | AI credentials leaked | `AI_BASE_URL`, `AI_API_KEY` via `shared_secrets` | Key rotation not automated |
| X-3 | OPA bundle service compromise | **T**, **E** | Malicious policy bundle | Bundle signing (OPA native) | Not enforced in dev; signing key management not documented |

---

## 3. DREAD — Risk Scoring & Prioritization

**DREAD Scale (1–10 each):**
- **Damage** — How bad if exploited? (1=low, 10=catastrophic)
- **Reproducibility** — How easy to reproduce? (1=hard, 10=trivial)
- **Exploitability** — Skill/tools needed? (1=expert, 10=script kiddie)
- **Affected Users** — % of user base impacted? (1=single, 10=all)
- **Discoverability** — How easy to find? (1=obscure, 10=public)

**Risk Score = (D + R + E + A + D) / 5** → **Critical ≥ 8.0, High 6.0–7.9, Medium 4.0–5.9, Low < 4.0**

| ID | Threat | Damage | Reprod. | Exploit. | Affected | Discover. | Score | Priority |
|----|--------|--------|---------|----------|----------|-----------|-------|----------|
| S-3 | Tenant breakout (cross-org) | 10 | 7 | 6 | 10 | 6 | **7.8** | **High** |
| S-1 | SQL parser bypass (mutation) | 10 | 5 | 5 | 10 | 5 | **7.0** | **High** |
| S-4 | OPA policy poisoning | 10 | 4 | 6 | 10 | 4 | **6.8** | **High** |
| A-4 | JWT claim manipulation | 9 | 6 | 7 | 9 | 6 | **7.4** | **High** |
| A-3 | Redis session compromise | 8 | 5 | 6 | 8 | 5 | **6.4** | **High** |
| S-8 | Result-set exfiltration | 7 | 6 | 5 | 7 | 6 | **6.2** | **High** |
| S-9 | Audit log tampering | 8 | 4 | 5 | 10 | 4 | **6.2** | **High** |
| CI-1 | Malicious dependency | 9 | 3 | 4 | 10 | 3 | **5.8** | **Medium** |
| S-7 | Schema introspection leakage | 6 | 7 | 6 | 6 | 7 | **6.4** | **High** |
| O-1 | OPA bundle tampering | 9 | 4 | 5 | 10 | 4 | **6.4** | **High** |
| C-2 | Rotator admin cred theft | 8 | 3 | 4 | 6 | 3 | **4.8** | **Medium** |
| P-3 | Direct DB access bypass | 8 | 4 | 5 | 6 | 4 | **5.4** | **Medium** |
| N-4 | Rate limit bypass | 5 | 8 | 7 | 8 | 8 | **7.2** | **High** |
| I-2 | Network lateral movement | 7 | 5 | 5 | 8 | 5 | **6.0** | **High** |
| S-10 | `AUDIT_LOG_RAW_SQL` leak | 6 | 3 | 3 | 8 | 3 | **4.6** | **Medium** |
| R-2 | Redis traffic sniffing | 5 | 4 | 4 | 6 | 4 | **4.6** | **Medium** |
| W-1 | XSS via query results | 6 | 4 | 5 | 7 | 4 | **5.2** | **Medium** |
| X-2 | OpenRouter key theft | 5 | 3 | 4 | 5 | 3 | **4.0** | **Medium** |
| CI-3 | Build compromise | 8 | 2 | 3 | 10 | 2 | **5.0** | **Medium** |
| A-7 | AI prompt injection | 5 | 5 | 6 | 6 | 5 | **5.4** | **Medium** |
| S-6 | GraphQL DoS | 4 | 7 | 6 | 7 | 7 | **6.2** | **High** |
| I-1 | Container escape | 8 | 3 | 4 | 8 | 3 | **5.2** | **Medium** |

---

## 4. Mitigation Mapping — Controls → Threats

| Control | Threats Mitigated | Implementation Location |
|---------|-------------------|------------------------|
| TLS termination + HSTS + cert pinning | N-1, N-5, W-4, W-5 | `nginx/nginx.conf` |
| Header validation & spoofable header stripping | N-2, N-3, A-4 (partial) | `nginx/nginx.conf:31-49`, `rbac_middleware.py:19-31` |
| IP rate limiting (api_limit, auth_limit) | N-4, S-6 (partial) | `nginx/nginx.conf:11-18` |
| JWT RS256 validation + trusted tenant claim | A-4, S-3 | `auth.py:validate_access_token`, `build_principal_from_claims` |
| HttpOnly Secure cookies + CSRF tokens | A-2, W-2 | `session_store.py`, `auth_routes.py` |
| `TENANT_DATABASES_JSON` enforced mapping | S-3 | `tenant_database_resolver.py` |
| `SqlSafetyChecker` (AST + sqlglot + rules) | S-1, S-2 | `sql_safety_checker.py`, `sql_cleaner.py` |
| Governed pipeline (validate → policy → execute → mask) | S-1, S-3, S-4, S-8, S-12 | `query_gateway.py` |
| OPA fail-closed evaluator | S-5 | `opa_policy_engine.py` (`evaluate()`) |
| GraphQL depth/alias limiters | S-6 | `sql_query_controller.py:81-82` |
| `SET ROLE` readonly + cost/row/byte/time limits | S-8, S-14, P-2 | `sql_query_repository.py`, `query_gateway.py` |
| `effective_access()` filters schema/introspection | S-7 | `policy_engine.py`, `sql_query_controller.py:493, 527` |
| Structured audit logging + correlation IDs | S-9, S-10 | `app_logger.py`, `query_gateway.py:191` |
| Read-only root FS + dropped caps + no-new-privs | I-1, O-2, C-1 | `docker-compose.yml` (all services) |
| Network segmentation (frontend/backend) | I-2, P-3 | `docker-compose.yml:303` |
| `gitleaks` + `pip-audit` + `bandit` + `npm audit` | CI-1, CI-2, CI-3 | `.github/workflows/ci.yml` |
| OPA bundle fetch over internal network (no signing yet) | O-1, O-3 | `opa/config.yaml` (production) |
| Credential rotation + ro volume mount | C-3, S-11 | `docker-compose.yml:220-251`, `rotate_creds.sh` |
| `shared_secrets.read_secret()` (env → *_FILE → default) | A-1, X-2, S-11, C-2 | `shared/shared_secrets/secrets.py` |

---

## 5. Residual Risk & Recommended Hardening

### 5.1 Critical Gaps (Do First)

| Gap | Threat(s) | Recommended Action |
|-----|-----------|---------------------|
| Edge rate limiting is IP-keyed only | N-4, S-6 | App-layer principal/tenant/database budgets exist; add Redis-backed IP-bucket sync for distributed floods at the edge |
| Audit log has no immutable sink | S-9 | Hash chaining exists in-process; ship the chain to a WORM store (CloudWatch, Loki with retention) so the sink itself cannot be rewritten |
| OPA bundle signing not enforced in CI | O-1 | Add `opa build --signing-key` verification in CI; fail if unsigned |
| Redis no TLS / no maxmemory | R-1, R-2, R-3 | Enable TLS in compose; set `maxmemory 256mb` + `maxmemory-policy allkeys-lru` |
| No mTLS between backend services | O-3, I-2 | Add `istio`/`linkerd` or manual mTLS with `step-ca` for backend network |
| Auth0 token binding absent | A-4 | Evaluate DPoP (RFC 9449) or mTLS sender-constrained tokens |

### 5.2 High-Value Improvements

| Area | Action |
|------|--------|
| **Observability** | Add Prometheus alerts: `opa_evaluation_failures_total > 0`, `audit_log_write_failures > 0`, `sql_query_cost_threshold_exceeded` |
| **Supply chain** | Provenance verification runs in CI with `gh attestation verify` (SBOM generation and scanner Action SHA pinning are in place); pin the remaining tag-pinned GitHub Actions (`codeql-action`, `hadolint-action`, `semgrep-action`, `upload-artifact`) to SHA |
| **Credential hygiene** | Rotate `pg_rotator_admin_pass` quarterly; automate OpenRouter key rotation |
| **Testing** | Add contract tests for OPA policy bundle; fuzz `clean_sql()` + `SqlSafetyChecker` (already have `test_sql_fuzzing.py`) |
| **Incident response** | Document runbook for: OPA fail-closed activation, credential rotation emergency, audit log tampering detection |

### 5.3 Acceptable Risks (Monitor Only)

| Threat | Rationale |
|--------|-----------|
| X-1 Auth0 compromise | External SaaS; mitigate via MFA, breach monitoring, short token TTL |
| I-3 Host kernel exploit | Mitigated by cloud provider (AWS Nitro / GCP Shielded VMs); accept |
| CI-4 Pre-commit bypass | Social control; `BLOCK=false` only for emergencies |
| CI-5 DAST | Baseline (passive) scanning only, and only of the unauthenticated surface. Authenticated flows, the GraphQL API and the governed SQL path are not exercised dynamically. Treat SAST and edge-config review as the controls for those |

---

## 6. Threat Model Maintenance

- **Review cadence:** Quarterly, or on any architecture change (new service, new trust boundary, new external dependency)
- **Trigger events:** Major release, security incident, new compliance requirement, OPA policy schema change
- **Owner:** Platform Security team (or designated maintainer)
- **Artifacts to update:** This document, `SECURITY.md`, runbooks in `docs/`, OPA policy regression tests

---

## 7. Appendix — Component Attack Surface Summary

| Component | Public Attack Surface | Internal Attack Surface | Crown Jewels |
|-----------|----------------------|------------------------|--------------|
| Nginx | 8080/8443 dev, 80/443 prod (HTTP→HTTPS redirect) | Backend service IPs | TLS private key |
| Auth0 API | `/api/*` via nginx | Redis, SQL API, OpenRouter | Auth0 client secret, session tokens |
| SQL Query API | `/graphql` (reached as `/api/graphql` through the Auth0 API BFF) | OPA, Redis (indirect), PostgreSQL, OpenRouter | Tenant DB credentials, OPA policies, audit logs |
| Web App | `/` via nginx (SPA) | Auth0 API, SQL API | None (static assets) |
| OPA | None (internal only) | SQL API | Policy bundles |
| Redis | None (internal only) | Auth0 API | Session tokens |
| Creds Rotator | None | PostgreSQL, creds volume | Admin PG password, rotated creds |
| PostgreSQL | None (internal only) | SQL API, Creds Rotator | Tenant data |

---

*Generated using PASTA/STRIDE/DREAD methodology. This model reflects the architecture as of the codebase state on 2026-10-04.*
