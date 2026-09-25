# Security Implementation Guide

## Overview
This document outlines the security measures implemented in the Secure DB Access Gateway application to protect against common vulnerabilities and attacks.

## Security Measures Implemented

### 1. Authentication & Token Management ✅

#### Trusted Principal Boundary
- The SQL Query API creates one immutable `Principal` only after Auth0 JWT signature,
  issuer, and audience validation succeeds.
- A principal contains the Auth0 subject, email, organisation claim, and normalized roles.
  Requests without a usable subject, email, or organisation claim are rejected.
- Authorization-sensitive handlers consume `request.state.principal` exclusively; request
  headers and query parameters cannot replace any principal attribute.
- The principal is also the sole source for SQL audit user and organisation metadata.

#### JWT Token Protection
- **Issue**: Returning or storing JWT tokens in browser JavaScript exposes them to XSS.
- **Solution**: The Auth0 callback returns user information only. It stores the access token
  server-side under an opaque session identifier, while the browser receives a signed,
  `HttpOnly`, `SameSite=Lax` session cookie. Production defaults this cookie to `Secure`.
- GraphQL requests go through `/api/graphql`; the backend-for-frontend reads the server-side
  token and forwards it internally. The browser never receives the token.

**Files Modified:**
- `web-app/src/services/auth-service.tsx`
- `web-app/src/services/api-client.ts`

**Implementation:**
```typescript
// Before: Vulnerable
localStorage.setItem('app_jwt', token);

// After: Secure
localStorage.setItem('app_jwt_exists', token ? 'true' : '');
// Token handled by httpOnly cookie automatically
```

#### Input Validation
- **Issue**: Invalid data in localStorage could cause errors.
- **Solution**: Add try-catch error handling with automatic recovery
  - JSON parsing wrapped in try-catch
  - Corrupted data automatically removed
  - User object validation before storage

### 2. CORS (Cross-Origin Resource Sharing) ✅

#### Strict CORS Configuration
- **Issue**: Wildcard CORS allows any origin to access APIs.
- **Solution**: Implement restrictive CORS with environment configuration.

**Features:**
- Allow only specific HTTP methods: `GET`, `POST`, `OPTIONS`
- Whitelist only necessary headers: `Content-Type`, `Authorization`, `X-Requested-With`
- Cache preflight requests for 1 hour (`max_age=3600`) to reduce overhead
- Environment-based origin configuration for multi-environment support

**Environment Variables:**
```bash
# Development (default)
CORS_ORIGINS=""  # Uses localhost defaults

# Production
CORS_ORIGINS="https://yourdomain.com,https://www.yourdomain.com"
```

**Files Modified:**
- `sql_query_api/app_factory.py`
- `auth0_api/app/middleware/setup.py`
- `auth0_api/app/config/settings.py`
- `auth0_api/app/security/csrf.py` (shared allowlist `get_allowed_origins()`)

### 3. HTTP Security Headers ✅

#### Response Headers (authoritative at `nginx/nginx.conf:102`, mirrored in FastAPI for dev)
- **X-Content-Type-Options: nosniff** - Prevents MIME type sniffing
- **X-Frame-Options: DENY** - Prevents clickjacking (supplemented by `Content-Security-Policy: frame-ancestors 'none'`)
- **Content-Security-Policy** - BFF: `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; connect-src 'self' https://localhost:8443 ...; upgrade-insecure-requests`; API: `default-src 'none'; frame-ancestors 'none'` (`auth0_api/app/middleware/setup.py:68`, `sql_query_api/app_factory.py:82`)
- **Strict-Transport-Security: max-age=31536000; includeSubDomains; preload** - Enforces HTTPS (1 year, preload)
- **Referrer-Policy: strict-origin-when-cross-origin**, **Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()**, **Cross-Origin-Opener-Policy: same-origin**, **Cross-Origin-Embedder-Policy: require-corp**, **Cross-Origin-Resource-Policy: same-site**

**Implementation:**
```python
@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; ..."
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    return response
```
`X-XSS-Protection` is intentionally not set (deprecated, replaced by CSP).

### 4. Input Validation & DoS Prevention ✅

#### SQL Query Validation
- **Issue**: Unvalidated input can cause DoS and injection attacks.
- **Solution**: Implement strict input validation.

**Measures:**
- Empty query check: `if not sql`
- Length limit: 10,000 characters max (prevents memory exhaustion)
- Query type validation: Only `SELECT` statements allowed
- Existing SQL safety checker: Prevents subqueries, CTEs, DDL, DML

**Files Modified:**
- `sql_query_api/routes/sql_query_controller.py`
- `sql_query_api/repositories/sql_validators/sql_safety_checker.py`
- `sql_query_api/services/query_gateway.py`

**Implementation:**
```python
# Validation checks
if not sql:
    raise ValueError("SQL statement cannot be empty.")

if len(sql) > 10000:
    raise ValueError("SQL statement is too long (max 10000 characters).")

if not sql.lower().startswith("select"):
    raise ValueError("Only SELECT statements are allowed.")
```

### 5. Error Handling ✅

#### Sensitive Information Protection
- **Issue**: Detailed error messages expose internal implementation details.
- **Solution**: Hide technical details from client responses.

**Before (Vulnerable):**
```python
raise Exception(f"Error executing SQL: {str(e)}")  # Exposes internal error
```

**After (Secure):**
```python
# Log detailed error for debugging
logger.exception("Error executing SQL")
# Return generic message to client
raise Exception("Failed to execute SQL statement. Please verify your query syntax.")
```

### 6. Request Security ✅

#### CSRF Protection
- Add `X-Requested-With: XMLHttpRequest` header to all API requests
- Used by framework to identify legitimate AJAX requests
- Prevents cross-site request forgery attacks

**Implementation:**
```typescript
apiClient.interceptors.request.use((config) => {
  config.headers['X-Requested-With'] = 'XMLHttpRequest';
  return config;
});
```

## OWASP Top 10 Coverage

| Vulnerability | Status | Implementation |
|---------------|--------|-----------------|
| A01: Broken Access Control | ✅ Mitigated | Token validation, session management |
| A02: Cryptographic Failures | ✅ Mitigated | HTTPS enforcement, secure headers |
| A05: Security Misconfiguration | ✅ Mitigated | Restrictive CORS, security headers |
| A07: Cross-Site Scripting (XSS) | ✅ Mitigated | httpOnly cookies, input validation |
| A08: Insecure Deserialization | ✅ Mitigated | JSON validation with error handling |
| A09: Using Components with Known Vulnerabilities | ✅ Monitored | Regular dependency updates via pyproject.toml/package.json |

## Environment Configuration

### Secrets storage model

No secret files live in this repository. Real credentials are injected as
environment variables from a single gitignored env file (`.env` for local
development, `/etc/gateway/gateway.env` on a production host) that is
provisioned manually. The shared `shared_secrets` loader resolves each value
in precedence order:

1. `NAME` environment variable (injected by Compose `env_file`, a secret
   manager, or an orchestrator such as Kubernetes).
2. `NAME_FILE` path on disk (for orchestrators that mount secrets as files;
   content is plaintext).
3. A configured default (development only).

Required secrets are validated at startup. `ENVIRONMENT` defaults to `production` (fail-closed) - dev must set `ENVIRONMENT=dev`; in `production` (and non-`CI`) the services **fail fast** when a required secret is absent, so a misconfigured deployment can never start with empty credentials. `shared_secrets` warns on missing `*_FILE` mounts and validates `..` traversal.

### Required for Production

Bootstrap a local env file and fill it in manually:

```bash
# Development: copy .env.example -> .env and edit values.
./scripts/bootstrap-dev.sh

# Production: copy .env.example to the host and fill in real values.
sudo install -m 600 -o deploy -g deploy gateway.env /etc/gateway/gateway.env
docker compose --env-file /etc/gateway/gateway.env up -d --build
```

The env file supplies every value the services need — Auth0 credentials, the
session-signing key (`APP_SECRET_KEY`), AI keys, the tenant database mapping
(`TENANT_DATABASES_JSON`), the dedicated database read-only role
(`SQL_READONLY_ROLE`), and the data-access policy (`POLICY_POLICIES_JSON`). See
`.env.example` for the full list.

### Database least privilege (defense-in-depth)

The app-level SQL safety layer (SELECT-only validation, auto-LIMIT, read-only
driver flags) is **not** the last line of defense. Even if it were bypassed, a
compromised gateway must not be able to write to tenant databases. Production
enforces this at the database engine itself:

- **Dedicated read-only role.** Provision `gateway_readonly_user` (or an
  equivalent) per tenant database with
  `sql_query_api/scripts/setup_least_privilege_gateway_role.sql`. The role holds
  only `CONNECT` on the database, `USAGE` on the data/metadata schemas, and
  `SELECT` on their tables/views — no ownership (`NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT`), no `CREATE`,
  no `TEMPORARY`, and explicit revocation of dangerous function execution.
- **Fail-closed provisioning.** The script has no placeholder password: it
  aborts if `gateway_password` is missing, verifies all invariants at the end
  (role flags, schema/table privileges, no ownership, read-only default), and
  raises on any violation so a misprovisioned role is never left behind. It is
  idempotent and safe to re-run.
- **Session enforcement.** Every gateway connection sets
  `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` and then
  `SET ROLE <SQL_READONLY_ROLE>`, so the engine enforces read-only for every
  transaction, including introspection. The role name is validated
  (`^[A-Za-z_][A-Za-z0-9_$]*$`) before it reaches `SET ROLE`.
- **Fail fast in production.** `sql_query_api` refuses to start a PostgreSQL
  tenant connection without `SQL_READONLY_ROLE` configured when
  `ENVIRONMENT=production` (skipped under `CI`), so production can never serve
  PostgreSQL tenants with a write-capable login.
- **Tenant credentials.** Connection strings in `TENANT_DATABASES_JSON` must use
  the read-only role, never an owner or superuser account. Clients only ever
  send a logical `database_id`.

### Database-side resource controls

Resource exhaustion is a DoS vector; production bounds it at the database
engine, not just in the app. The provisioning script
(`sql_query_api/scripts/setup_least_privilege_gateway_role.sql`) attaches role
defaults so even a direct login as the gateway role is time- and memory-bounded,
and its verification block re-checks them on every run:

- **`statement_timeout` (30s default)** — kills runaway statements server-side.
- **`lock_timeout` (5s default)** — bounds lock-wait time (mirrors
  `SQL_LOCK_TIMEOUT_SECONDS`).
- **`idle_in_transaction_session_timeout` (10s default)** — reclaims sessions
  parked inside an open transaction.
- **`work_mem` (4MB default)** and **`max_parallel_workers_per_gather`
  (0 default)** — bound per-operation and parallel-query memory for a
  predictable footprint.
- **`CONNECTION LIMIT` on the role (20 default)** — caps how many pooled
  connections the gateway can hold; the app-side pool
  (`DB_POOL_SIZE` + `DB_MAX_OVERFLOW` per engine) is validated to be positive
  and finite at startup and must fit under this limit.
- The operator probe (P5) fails any tenant whose role has a disabled
  statement/lock/idle timeout or no connection limit, so an unprovisioned target
  is flagged before rollout.

The gateway also pushes its per-query budgets (`SQL_QUERY_TIMEOUT_SECONDS`,
`SQL_LOCK_TIMEOUT_SECONDS`) at session start; the app budget should never be
looser than the DB budget (keep `SQL_QUERY_TIMEOUT_SECONDS <=
gateway_statement_timeout`).

### Key Management Best Practices

1. **Many copies, one authority.** The env file on each host is a *copy*. Keep
   the source of truth in a password manager (or a secret manager) and restore
   the host file from it. If `/etc/gateway/gateway.env` is the only copy and
   the disk dies, the secrets are lost.
2. **Never commit real values** — `.env`, `gateway.env`, and TLS key material
   are gitignored; commit only `.env.example` (names, no values).
3. **Use strong random values** - Min 32 characters for secrets
   (`openssl rand -hex 32` for `APP_SECRET_KEY`).
4. **Different secrets per environment** - Dev, staging, prod.
5. **Protect the host** - `chmod 600` on the env file; anyone in the `docker`
   group can read container env via `docker inspect` (docker access is
   root-level access). Avoid `docker compose config` output in shared logs.
6. **Rotation** - Update the source of truth, rewrite the host env file, and
   `docker compose up -d` to recreate containers with the new values.
7. **Scaled deployments** - The loader accepts any injected secret source, so
   you can swap the manual env file for an init container or SDK from AWS
   Secrets Manager / SSM, HashiCorp Vault, or Azure Key Vault / Google Secret
   Manager without code changes: inject the values as `NAME` env vars.

## Testing Security

### Manual Testing Checklist

- [ ] Test CORS with different origins
- [ ] Verify httpOnly cookies are set
- [ ] Check security headers in responses
- [ ] Test SQL query length limits
- [ ] Verify error messages don't leak details
- [ ] Test invalid token handling
- [ ] Verify HTTPS redirection (production)

### Automated Testing

```bash
# Check for hardcoded secrets in code
grep -r "password\|secret\|token\|key" --include="*.py" --include="*.ts" --exclude-dir=node_modules

# Validate CORS configuration
curl -H "Origin: https://attacker.com" http://localhost:8001 -v

# Test security headers
curl -I http://localhost:8001/api/health
```

## Deployment Security Checklist

- [ ] All environment variables configured
- [ ] Strong secrets generated and stored securely
- [ ] CORS origins restricted to production domains
- [ ] HTTPS/TLS enabled for all endpoints
- [ ] Database credentials in secure vault
- [ ] Logging configured (INFO level, not DEBUG)
- [ ] Rate limiting enabled (if applicable)
- [ ] Firewall rules configured
- [ ] Regular security updates scheduled
- [ ] Backup and recovery plan documented

## Future Security Enhancements

1. **WAF Integration** - CloudFront, AWS WAF, or similar
2. **API Key Management** - For third-party integrations
3. **Database Encryption** - At-rest encryption for sensitive data
4. **Token Expiration** - JWT token refresh mechanism (silent renewal via `offline_access`)
5. **MFA Support** - Multi-factor authentication option
6. **SQL Query Caching** - With result encryption
7. **Penetration Testing** - Regular security audits
8. **Security Monitoring** - Real-time threat detection

Already implemented (not future): edge rate limiting (`nginx/nginx.conf:11 nginx: api_limit 60r/m burst 20, auth_limit 5r/m burst 3`), comprehensive audit logging (`sql_query_api/services/audit`), RBAC + ABAC policy engine.

## Security Contact

For security issues, please report responsibly:
1. Do NOT create public GitHub issues for security vulnerabilities
2. Email security concerns to: kenneth.kiiza@googlemail.com
3. Allow 30 days for response before public disclosure

---

**Last Updated:** September 2026  
**Security Patch Version:** 1.2.0  
**Status:** Development
