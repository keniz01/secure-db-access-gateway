# Production Readiness Roadmap

This roadmap tracks the work required to move Secure DB Access Gateway from a functional prototype/build to a production-ready system. Status last verified **2026-09-13**.

## Current status summary

The architecture, governed query pipeline, and core security controls are **implemented and green**, and a hardened deployment path exists. What remains is primarily **operational maturity** (runbooks, backups/DR, load validation, dashboards/alerts) and **documented sign-off**, plus the GitHub-tracked enterprise/AI feature backlog.

Verified facts as of today:

- **All backend tests pass**: SQL Query API `126 passed`, Auth0 API `60 passed`; CI runs SQL pytest + bandit + pip-audit, auth0 pytest, web lint/typecheck/build + Playwright e2e on every PR.
- **SQL safety check is fixed and pinned**: `sqlglot>=30.0.0,<31` (pyproject.toml), strict AST read-only analysis with bypass tests (`pg_read_file`, `dblink_connect`, `pg_write_file`, aliases/derived expressions).
- **Read-only is enforced at every layer**: `SET TRANSACTION READ ONLY` for PostgreSQL (`repositories/sql_query_repository.py:207`), SQLite forced to `mode=ro` (both in connection-string enforcement and startup validation in `dependencies/dependency_container.py:104`), and the governed pipeline applies safety/AST validation, tenant resolution, auto-LIMIT, masking, and audit.
- **Identity/tenant hardening shipped**: authenticated `Principal` is the sole identity source, `X-User-*`/`X-Org-Id`/`X-Tenant-Id` headers are cleared at the nginx edge and never trusted, tenant claim is required, cookie is HttpOnly/Secure/SameSite via env. Spoofing + cross-tenant regression suites pass.
- **Production deployment path shipped (PR #141)**: `docker-compose.prod.yml` (nginx 443-only TLS edge — TLS 1.2/1.3, HSTS, edge rate limiting, HTTP→HTTPS redirect), `/healthz` + `/readyz` on both backends, Docker healthchecks with `service_healthy` startup gating, fail-fast when required config/secrets are missing, multi-arch GHCR image pipeline (`docker.yml`), and tagged auto-deploy with image-tag rollback (`deploy.yml`).
- **Observability groundwork exists**: structured JSON audit stream (policy decisions, masking, tenant, query metadata, agent delegation), `/metrics` Prometheus endpoint on the SQL API (query count, rows, duration by org), and an otel-lgtm stack wired into compose.

Remaining gaps fall in two buckets:

1. **Production operations** (documented below, Phase 3–5): backup/restore + RTO/RPO, runbooks + on-call, load/capacity validation, Grafana dashboards + alerts, correlation IDs, a formal security review sign-off, and WAF/account-lockout/graceful-degradation hardening.
2. **Product/enterprise roadmap** (tracked in GitHub milestones 13–17): MCP server + agent identity (#62/#63/#66), JIT access + approval (#67/#68), enterprise SSO/SCIM/SoD (#69/#70/#71), AI evals + telemetry (#72/#73/#74), audit UI + retention (#59/#60), masking outcomes + classification (#54/#57), distributed rate limiting (#75), and the commercial onboarding/demo/positioning set (#77/#79/#80/#81/#82).

Both buckets are mirrored 1:1 in GitHub milestones — see the Milestone map on this page.

Production readiness is achieved only when all open items below are complete and verified by CI/CD, security review, and operational sign-off.

---

## Phase 0: Stabilize the baseline and unblock production — **DONE**

### 0.1 Fix the SQL safety validation breakage — DONE
- [x] Pin and validate the compatible `sqlglot` version in `sql_query_api` dependencies (`sqlglot>=30.0.0,<31`)
- [x] Replace deprecated AST checks that rely on removed `sqlglot` APIs (`ast_analyzer.py` → `is_strictly_read_only`)
- [x] Verify `DefaultSqlSafetyChecker` blocks DML/DDL and rejects unsafe functions/tables
- [x] Add regression tests for adversarial SQL bypass attempts (`test_prod_readiness_phase1.py`, `test_sql_safety_checker.py`)
- [x] Acceptance: all SQL Query API tests pass in CI (`126 passed`)

### 0.2 Restore the full backend quality gate — DONE
- [x] Fix failing tenant isolation and authorization tests
- [x] Fix arbitrary-schema query tests and GraphQL integration failures
- [x] Fix audit sanitization and query result resource limit checks
- [x] Run full backend unit/integration suites for auth and SQL API (SQL `126` / Auth0 `60`)
- [x] Acceptance: 100% pass for all Python service tests before release

### 0.3 Add production-grade test coverage — DONE
- [x] Add tests for multi-tenant DB routing and database-by-tenant enforcement
- [x] Add tests for timeout, query budget enforcement, and max-result limits
- [x] Add failure-mode tests for connection errors and upstream API outages
- [x] Add security regression tests for OAuth header spoofing and query injection bypasses
- [x] Acceptance: security and resilience tests are part of required CI

---

## Phase 1: Security hardening — **DONE (small carry-overs noted)**

### 1.1 Enforce stricter read-only database controls — DONE
- [x] Verify every database connection enforces `SET TRANSACTION READ ONLY` for PostgreSQL
- [x] Validate SQLite connections open in read-only mode (`?mode=ro`; enforced at connection-build and validated at startup)
- [x] Confirm there is no path to mutation via direct SQL, GraphQL, or CLI execution (single governed pipeline; `explore.py` re-execs the governed path and requires a validated token)
- [x] Audit all DB access layers for bypasses or helper APIs that can execute non-SELECT statements
- [x] Acceptance: code review + security review confirms no write path exists

### 1.2 Harden auth and session handling — DONE
- [x] Validate Auth0 JWT verification is strictly server-side and header-supplied roles are ignored (identity headers cleared at edge, never trusted)
- [x] Confirm tenant claims are required and cannot be spoofed
- [x] Harden cookie/session settings (`HttpOnly`, `Secure`, `SameSite`, expiry via env)
- [x] Add lockout or rate limiting for authentication failures at the gateway and API layers (edge `limit_req` 5r/m on `/api/(login|auth|logout)`)
- [ ] Carry-over: in-app account lockout / per-identity throttling (edge IP-based only)

### 1.3 Dependency and secret hygiene — DONE (one carry-over)
- [x] Run `pip-audit`/`npm audit` and remediate critical findings (pip-audit + bandit gated in CI for `sql_query_api`)
- [x] Ensure no secrets are committed to the repository or generated files (gitleaks + shared-secrets scans run in CI)
- [x] Move secret handling to environment/secret-manager best practice for every environment (`read_secret` loader + `*_FILE` injection; no `secrets/` dir, no encrypted files)
- [x] Add secret rotation procedure and emergency response guidance (`SECURITY.md`)
- [ ] Carry-over: npm audit is not yet a gating check in CI (Python side is; frontend lint/typecheck/build/e2e are)

---

## Phase 2: Production infrastructure and deployment hardening — **DONE (carry-overs noted)**

### 2.1 Replace dev deployment assumptions with production deployment — DONE
- [x] Add a production override for Docker Compose or move to managed deployment manifests (`docker-compose.prod.yml`)
- [x] Configure TLS/HTTPS termination at the edge (`nginx/nginx.conf`: TLS 1.2/1.3, HSTS, HTTP→HTTPS redirect)
- [x] Restrict public exposure to only required ports and endpoints (only `443` published; SPA served through the edge; backend ports unexposed)
- [x] Add explicit network segmentation and service isolation (edge isolates auth0_api vs sql_query_api vs web_app upstreams; identity headers stripped)
- [x] Acceptance: deployment can be brought up in a production-like environment without unsafe defaults (verified against prod compose)

### 2.2 Add health checks and startup guarantees — DONE
- [x] Add liveness/readiness endpoints to all services (`/healthz`, `/readyz` on both backends)
- [x] Add Docker health checks and dependency startup ordering checks (`service_healthy` gates)
- [x] Define fail-fast behavior for missing config or unreachable dependencies (`ENVIRONMENT=production` requires policy config; secrets `*_FILE` enforced at startup)
- [ ] Carry-over: graceful degradation for DB or Auth0 outage (readiness fails; no degraded-mode caching/circuit-breaker defined)
- [x] Acceptance: platform can self-diagnose unhealthy components and fail predictably

### 2.3 Add operational security controls — DONE (carry-over noted)
- [x] Restrict CORS to approved production origins only (`ALLOWED_ORIGINS` env, credentialed)
- [x] Require TLS for all cross-service communications in production (termination at edge; internal traffic never exposed publicly)
- [x] Add WAF/reverse-proxy hardening rules and request limits (edge `limit_req` zones for API and auth paths)
- [x] Review and document NGINX exposure policy for admin and API endpoints (`nginx/nginx.conf` + `DOCKER_README.md`)
- [ ] Carry-over: WAF-class filtering (mod_security / managed WAF) for L7 attacks
- [x] Acceptance: no insecure public endpoints remain in production config

---

## Phase 3: Data layer and resilience — **PARTIAL (hardest remaining ops gap)**

### 3.1 Production database strategy — IN PROGRESS (M18 · #143)
- [x] Define the production database topology (primary + read replica routing shipped; tenant resolver validated)
- [x] Define backup + restore procedures and DR objectives (documented in `BACKUP_DR.md`: RPO ≤24 h / RTO ≤30 min targets, retention policy, off-host copy requirement)
- [x] Automate the backup: `scripts/backup-databases.py` (verified `pg_dump` custom-format, rotating retention, per-database pruning) scheduled daily via `.github/workflows/backup.yml`, incident-tagged on failure
- [x] Provide a safe restore path: `scripts/drills/drill-restore.sh` — validates the archive, restores into a scratch STAGING database, drops it even on failure
- [ ] Execute the restore drill on a live stack, record measured RTO, and verify a restore from the off-host copy (#143)
- [x] Validate read-only access patterns against database-level permissions and least privilege (dedicated `gateway_readonly_user` role provisioned via `sql_query_api/scripts/setup_least_privilege_gateway_role.sql`, session-level read-only + `SET ROLE`, production fail-fast; documented in `SECURITY.md` and `BACKUP_DR.md`) (M18 · #148)
- [ ] Acceptance: DB administrators have a tested (drill-passed) restore plan and least-privilege configuration

### 3.2 Query safety and performance budgets — DONE
- [x] Enforce maximum query execution time, row limits, and byte limits in the gateway (30s timeout, 5 MB result cap, lock timeout, auto-LIMIT, cost budgeting)
- [x] Validate query budget behavior under concurrent traffic (unit/integration coverage)
- [x] Add resource limits for schema introspection, result serialization, and audit payload sizes (result-size cap enforced at serialization border)
- [ ] Carry-over: circuit-breaking or queue backpressure under database overload
- [ ] Carry-over: stress-validate the defined budgets under concurrent load (tie to 4.3)

### 3.3 Database and API observability at the data layer — PARTIAL
- [x] Log tenant, org, query hash, and execution metadata in a privacy-safe format (structured JSON audit stream, sanitized)
- [x] Ensure audit logs are sanitized (sanitization regression-tested)
- [ ] Add DB query metrics, connection pool metrics, and latency dashboards (query count/rows/duration metrics exist on `/metrics`; connection-pool metrics and dashboards do not)
- [ ] Enforce audit retention policies (see #60)
- [ ] Acceptance: data access can be traced and audited without exposing raw SQL or sensitive row data

---

## Phase 4: Monitoring, alerts, and operational maturity — **OPEN (largest remaining bucket)**

### 4.1 Observability stack — PARTIAL
- [x] Validate Grafana/Loki/Tempo/OTel stack in a real deployment (otel-lgtm wired into compose; `/metrics` + `/healthz` + `/readyz` endpoints live)
- [ ] Add dashboards for service availability, DB latency, auth failures, GraphQL failure rates, and error budgets (GitHub M18 · #76)
- [ ] Add alert thresholds for critical endpoints and infrastructure services (M18 · #76)
- [ ] Ensure correlation IDs and structured logs are emitted consistently across services (structured logs yes; correlation IDs no) (M18 · #147)
- [ ] Acceptance: operators can diagnose failures without manual log searching

### 4.2 Incident response and runbooks — IN PROGRESS (M18 · #142 → `RUNBOOKS.md`)
- [x] Write runbooks for Auth0 outage, DB outage, API error spike, and reverse-proxy failure (drafted in `RUNBOOKS.md`, R1–R4; deploy/secret failure as R5)
- [x] Define escalation paths and on-call ownership (SEV1–3 model + on-call → maintainers → vendor/security chain in `RUNBOOKS.md`)
- [x] Document log collection, support troubleshooting steps, and service dependencies (`RUNBOOKS.md` service map + access/commands)
- [x] Automate detection and incident wiring: 20-min host probe (`scripts/probe-production.sh` + `sql_query_api/probe/run.py`, P1–P6 incl. per·tenant DB connectivity) auto-opens/closes an `incident:probe` issue via `.github/workflows/probe.yml`; one-command SEV1/2/3 filing via `scripts/incident-start.sh` + `incident.md` template
- [x] Keep runbooks honest: `scripts/check-runbooks.py` fails CI on stale service/endpoint/file references; drills executable via `scripts/drills/drill-db-outage.sh` (R2) and `scripts/drills/drill-rollback.sh` (R5)
- [ ] Validate the runbooks by executing one on-call drill per rotation (drill scripts committed for R2/R5; R1/R3 harnesses still to be added)
- [ ] Acceptance: team can execute recovery procedures without developer tribal knowledge

### 4.3 Capacity and resilience testing — OPEN, priority (M18 · #144)
- [ ] Conduct load testing with realistic concurrent queries
- [ ] Test failover, restart, and partial-service outage recovery
- [ ] Validate rate limiting and request throttling behavior under attack traffic
- [ ] Accept a defined concurrency and resource envelope for production deployment
- [ ] Acceptance: service remains stable under expected production load

---

## Phase 5: Release management and compliance gate — **OPEN**

### 5.1 CI/CD pipeline hardening — DONE (carry-overs noted)
- [x] Require all backend tests, frontend build, lint, and security scans in PR checks (SQL tests + bandit + pip-audit, auth0 tests, web lint/typecheck/build/e2e)
- [x] Add deployment gate checks for production branch/tag (deploy workflow triggers on `v*` tags with image-tag rollback)
- [x] Validate rollback steps and version pinning for all infrastructure and application dependencies (image-tag pinned rollback over SSH)
- [ ] Carry-over: artifact provenance / signed release verification (not required for self-hosted)
- [ ] Acceptance: production deployment is versioned, traceable, and reversible

### 5.2 Security review and compliance sign-off — OPEN (M18 · #145)
- [ ] Complete a formal security review covering auth, DB access, secrets, and ingress
- [ ] Remediate all high/critical findings before production sign-off
- [ ] Document threat model and residual risks
- [ ] Define access control and approval process for tenant onboarding and database permissions
- [ ] Acceptance: security sign-off is recorded and reviewed by stakeholders

### 5.3 Production launch checklist — OPEN (M18 · #146)
- [ ] Production secrets populated and verified
- [ ] Production tenant mapping configured and validated
- [ ] TLS and network policy verified
- [ ] Monitoring/alerting tested and operational
- [ ] Backups validated
- [ ] Rollback procedure tested
- [ ] Launch communication and support escalation plan approved
- [ ] Acceptance: all launch checklist items are complete and signed off

---

## Gaps to production readiness (summary)

| # | Gap | Type | Blocker to go-live? | GitHub |
|---|-----|------|---------------------|--------|
| 1 | Backup/restore for tenant DBs + RTO/RPO + retention | Ops | Yes | M18 · #143 |
| 2 | Incident runbooks + escalation/on-call | Ops | Yes | M18 · #142 |
| 3 | Load/capacity validation + concurrency envelope | Ops | Yes | M18 · #144 |
| 4 | Dashboards + alert thresholds | Ops | Yes | M18 · #76 |
| 5 | Formal security review + threat model + sign-off | Security | Yes | M18 · #145 |
| 6 | Launch checklist completion | Process | Yes | M18 · #146 |
| 7 | DB-level least-privilege grants (gateway account) | Security | Yes | M18 · #148 |
| 8 | Correlation IDs across services | Ops | No | M18 · #147 |
| 9 | WAF, in-app lockout, graceful degradation | Security | No | M18 · #150/#151/#149 |
| 10 | npm audit gate in CI; artifact provenance | CI/CD | No | M18 · #152 (provenance waived: self-hosted registry) |
| 11 | Circuit breaker / backpressure under DB overload | Ops | No | M18 · #149 |
| 12 | MCP server + agent identity + regression suite | Product | No | M14 · #62/#63/#66 |
| 13 | JIT access + approval workflow | Product | No | M15 · #67/#68 |
| 14 | Enterprise SSO (OIDC/SAML), SCIM, SoD | Product | No | M15 · #69/#70/#71 |
| 15 | AI evals (NL2SQL, schema retrieval) + telemetry | Product | No | M16 · #72/#73/#74 |
| 16 | Audit UI/API + retention/export | Product | No | M13 · #59/#60 |
| 17 | Masking policy outcomes + data classification | Product | No | M13 · #54/#57 |
| 18 | Distributed rate limiting | Product | No | M16 · #75 |
| 19 | Commercial: demo, onboarding, positioning, PMF | Product | No | M17 · #77/#79/#80/#81/#82 |

### Milestone map (GitHub)

- **M13 — Audit & Governance** → #59/#60/#54/#57
- **M14 — AI Agent & MCP Access** → #62/#63/#64/#65/#66
- **M15 — JIT Access & Enterprise IAM** → #67/#68/#69/#70/#71
- **M16 — AI Evaluation & Observability** → #72/#73/#74/#75 (dashboards #76 moved to M18 as an ops go-live item)
- **M17 — Commercial Product & Documentation** → #77/#79/#80/#81/#82
- **M18 — Production Operations Readiness** → #76 + #142–#152 (this roadmap's Milestone C scope; created 2026-09-13)

Every open issue across all six milestones now maps 1:1 to the gap table and phased checklist above.

---

## What needs to be done next

### Tier 1 — Unblock production operations (no feature work; ~2–3 weeks)

Tracked in **GitHub milestone 18 — Production Operations Readiness** (#142–#152, #76).

1. **Write the runbook library (#142)** — Auth0 outage, DB outage, API error-spike, and reverse-proxy failure, each with detection, containment, and recovery steps; define escalation + on-call ownership. _(Phase 4.2)_
2. **Define and validate backup/restore (#143)** — document tenant-DB backup + restore procedures, perform a restore drill, set retention/snapshot policy and RTO/RPO, and document least-privilege DB grants for the gateway account (#148). _(Phase 3.1)_
3. **Load-test the defined budgets (#144)** — realistic concurrent query mix against the timeout/5 MB/auto-LIMIT/cost budget enforcement; record a concurrency + resource envelope; add circuit-breaking/backpressure only if load results require it (#149). _(Phase 4.3)_
4. **Stand up dashboards + alerts (#76)** — Grafana dashboards for service availability, DB latency, policy denials, masking events, tenant utilization, error budgets; alert thresholds; add correlation IDs to structured logs (#147). _(Phase 4.1)_
5. **Security sign-off (#145)** — run the formal security review, document threat model + residual risks, then execute the 5.3 launch checklist (#146). _(Phase 5.2/5.3)_

### Tier 2 — Governed AI + enterprise platform (GitHub-tracked; sequence by customer value)

6. **MCP server as a governed gateway client (#62, M14)** with first-class agent identity (#63) and the MCP security regression suite (#66) — this is the P0 that unlocks AI-tool access to governed data.
7. **Enterprise access controls (M15)** — JIT access requests (#67) + approval lifecycle (#68), then OIDC/SAML hardening (#69); SCIM (#70) and separation of duties (#71) follow.
8. **AI quality loop (M16)** — NL2SQL evaluation dataset/runner (#73), schema-retrieval evals (#74), and query-generation telemetry (#72).
9. **Audit and data governance (M13)** — searchable audit UI/API (#59) + retention/export (#60), then masking policy outcomes (#54) and data classification metadata (#57).
10. **Platform hardening and commercial (M17)** — distributed rate limiting (#75), then the commercial set: five-minute demo (#79), onboarding flow (#80), repositioning (#77), community/commercial plan (#81), PMF validation (#82).

### Tier 3 — Carry-over hardening to fold into the above

- Gate `npm audit` in CI (1.3) and decide on artifact provenance (5.1).
- WAF-class L7 filtering (2.3), in-app account lockout (1.2), graceful-degradation/circuit-breaker behavior (2.2/3.2).

---

## Milestones

### Milestone A: Stable and secure core — **ACHIEVED** (2026-09-13 verified)
- SQL safety checker fixed and pinned; all backend tests passing (SQL 126 / Auth0 60)
- tenant isolation validated (resolver + cross-tenant + spoofing regression suites)
- read-only enforcement, policy model, masking, audit pipeline, RBAC shipped
- identity headers eliminated; Principal is sole identity source

### Milestone B: Deployment-ready — **ACHIEVED** (PR #141, 2026-09-11)
- TLS edge (443-only, HTTP→HTTPS), hardened secrets (`read_secret` + `*_FILE`)
- `/healthz` + `/readyz`, Docker healthchecks, startup gating, fail-fast config
- GHCR image pipeline + tagged SSH deploy with rollback; prod compose manifest
- Observability groundwork (otel-lgtm, `/metrics`, structured audits)

### Milestone C: Production launch gate — **NOT STARTED** (current focus)
- Tier 1 operational items (runbooks, backups/DR, load validation, dashboards/alerts) complete — tracked in GitHub **M18 — Production Operations Readiness** (#76, #142–#152)
- all high/critical security findings resolved and formal sign-off recorded
- launch checklist (5.3) signed by engineering + security + operations
- optional: governed MCP + agent identity (#62/#63, M14) before GA exposure

> GitHub milestone map: M13 = Audit & Governance, M14 = AI Agent & MCP Access, M15 = JIT Access & Enterprise IAM, M16 = AI Evaluation & Observability, M17 = Commercial Product & Documentation, M18 = Production Operations Readiness (this milestone).

---

## Definition of done for production readiness

The system is production-ready only when all of the following are true:

- All required automated tests pass in CI (including npm audit gate)
- No critical or high-severity security findings remain open; security review sign-off recorded
- Database access is strictly read-only and tenant-isolated (app + DB grants)
- Production environment is encrypted, monitored (dashboards + alerts), and backed up (restore drill passed)
- Runbooks exist for the top failure modes; on-call/escalation defined
- Rollback and incident response procedures are documented and tested
- Launch checklist signed by engineering + security + operations
- Governed AI pipeline (MCP + agent identity) is GA-ready or explicitly deferred by product sign-off

This roadmap should be treated as the minimum required work; any environment-specific compliance or architecture requirements should be added before the final release decision.