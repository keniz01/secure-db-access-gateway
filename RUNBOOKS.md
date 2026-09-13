# Incident Response Runbooks

Operational runbooks for Secure DB Access Gateway. Covers detection, containment,
recovery, and escalation for the top production failure modes. **Tracked by
GitHub issue #142 (M18).**

Any engineer should be able to follow these runbooks without tribal knowledge.
If a step here doesn't match observed reality, file an issue and update this
document — the document is expected to drift with the system.

---

## Service inventory and dependency map

```
                      ┌──────────────────────────────────────────────┐
                      │  nginx (TLS edge, :443 / :80→443 redirect)   │
                      │  / → web_app │ /api → auth0_api │ /api/graphql │
                      └──────┬───────────────┬────────────┬───────────┘
                             │               │            (proxied by auth0_api)
                             ▼               ▼            ▼
                        web_app        auth0_api     sql_query_api
                        (SPA :5173)    (FastAPI :8001)(FastAPI+GraphQL :8002)
                             │               │            │
                             │               │            ├──> tenant DBs
                             │               ├──> Auth0   │     (Postgres / SQLite,
                             │               ├──> AI prov.│      TENANT_DATABASES_JSON)
                             │               └──> sql_query_api (server-to-server)
                             ▼
                       otel-lgtm (Grafana/Loki/Prometheus/Tempo Observability)
```

- **External dependencies** (owned by third parties): Auth0 (identity),
  AI provider (OpenRouter/Gemini — used for the optional AI greeting and
  text-to-SQL), tenant databases (customer DBs).
- **Health endpoints**: `/healthz` (liveness, touches nothing) and `/readyz`
  (readiness) on both APIs. `auth0_api` `/readyz` checks only process/startup;
  `sql_query_api` `/readyz` validates that the tenant-DB config is loadable —
  it does **not** ping databases. See [Known monitoring gaps](#known-monitoring-gaps).
- **Metrics**: `sql_query_api` exposes Prometheus `/metrics` (query total, rows
  returned, duration, by org). Instrumented stats also flow to otel-lgtm.
- **Audit**: structured JSON events stream to the `sql_query_api` container
  stdout (docker logs) — policy decisions, masking, tenant, query metadata.

## Access and commands (production host)

All commands run on the production host from the repo checkout
(e.g. `/opt/secure-db-access-gateway`) as the `deploy` user:

```bash
# Compose prefix used throughout (env file + prod overlay):
C="docker compose --env-file /etc/gateway/gateway.env -f docker-compose.yml -f docker-compose.prod.yml"

# State / health
$C ps
curl -fsS https://<host>/healthz
curl -fsS https://<host>/readyz

# Logs (per-service, follow)
$C logs --tail=200 -f <nginx|auth0_api|sql_query_api|web_app>

# Restart a service
$C restart <service>

# Re-deploy / roll back
# GitHub Actions → "Deploy to Production" → image_tag = published GHCR tag.
# Rollback = re-run with the previous vX.Y.Z (or edge) tag. See DOCKER_README.
```

Time-correlation trick: all logs go to `docker logs`; `otel-lgtm` (Grafana)
aggregates traces/metrics/logs when reachable. Start every investigation with
the **start timestamp of the first symptom** so you can line up nginx access
logs, API logs, and audit events at the same instant.

## Automation (issue #142 / M18)

| Piece | What it does | Where |
| --- | --- | --- |
| **Health probe** | Runs on the host; checks P1 edge/SPA, P2 TLS expiry, P3 `auth0_api` ready, P4 `sql_query_api` ready, P5 **per-tenant DB connectivity**, P6 Auth0 provider reachability. Exit 0 = healthy. | `scripts/probe-production.sh` + `sql_query_api/probe/run.py` |
| **Auto-incident** | Every 20 min SSHes to the host and runs the probe; on failure opens/updates an `incident:probe` issue pointing to the right runbook; closes it on recovery. | `.github/workflows/probe.yml` |
| **Incident filing** | One-command SEV1/2/3 issue from the incident template. | `.github/ISSUE_TEMPLATE/incident.md`, `scripts/incident-start.sh` |
| **Drill: DB outage (R2)** | Injects an unreachable tenant DB via a scratch compose override, asserts P5 detects it, restores, asserts recovery. Requires `STAGING=1`. | `scripts/drills/drill-db-outage.sh` |
| **Drill: rollback (R5)** | Dispatches the deploy workflow at a provisional tag, then re-pins the previous good tag and verifies health. Optional `--fault-tag` asserts a broken tag is caught. | `scripts/drills/drill-rollback.sh` |
| **Tenant DB backup (R6)** | Daily (`17 2 * * *`) host-side `pg_dump` custom-format backup of every tenant DB, archive-verified, rotating retention; opens/closes an `incident:backup` issue. | `scripts/backup-databases.py` + `.github/workflows/backup.yml` |
| **Drill: restore (R6)** | Restores the newest backup into a scratch STAGING database, verifies data, drops it. Requires `STAGING=1` + `RESTORE_ADMIN_URL`. | `scripts/drills/drill-restore.sh` |
| **Freshness lint** | Fails CI if this file references a compose service, path, or endpoint that no longer exists. | `scripts/check-runbooks.py` + CI job |

How to use them: `bash -n scripts/probe-production.sh` to check syntax; run `bash scripts/probe-production.sh` on the host to probe on demand; read the incident issue to know which runbook to start with.

---

## Severity and escalation model

| Severity | Definition | Target response | Examples |
| --- | --- | --- | --- |
| **SEV1** | Full outage: no tenant can log in or run queries; data access unavailable | Immediate, 24x7, page the on-call engineer | nginx down, all APIs down, tenant DB inaccessible |
| **SEV2** | Significant degradation: queries failing for a subset, auth flaky, error-rate spike, or data inaccessible but site usable | Within 30 min (business hours), 24x7 pages if >1h | One tenant DB down, Auth0 intermittent, TLS cert near expiry |
| **SEV3** | No user impact: config drift, missing alert, dashboard gap, slow query | Next working day | Missing healthcheck coverage, stale cert in 30+ days |

**Escalation chain:**

1. **Level 1 — On-call engineer** (holder of the deploy credentials; owner of
   the prod host). Handles containment and initial recovery.
2. **Level 2 — Maintainers** (repo owners / `CODEOWNERS`). Called when the fix
   requires design decisions, a deploy is already broken, or containment holds.
3. **Level 3 — Vendor / external**: Auth0 support (`support.auth0.com`), the
   DB provider, the AI provider, and — for suspected security incidents —
   the security contact in `SECURITY.md` (`kenneth.kiiza@googlemail.com`).

**Communication:** announce the incident (SEV1/2) in the team incident channel
with severity + runbook + current actions; post a GitHub issue linking this
runbook as the live tracking thread. Update the thread at every state change
(triage → contained → recovered).

**Post-incident policy:** every SEV1 and SEV2 gets a 5-business-day postmortem:
timeline, root cause, what worked/didn't, and tracked remediation issues. Link
the postmortem from the tracking issue.

---

## R1 — Auth0 outage

**Symptoms:** login flow fails — redirect to Auth0 errors/times out; token
exchange or callback returns 500s from auth0_api; users report "can't log in";
`GET /api/dashboard` and query endpoints still fail with 401 even for valid
sessions. Existing httpOnly-cookie sessions that are still valid continue to
work (Auth0 is only consulted at login/logout).

**Note:** `auth0_api /readyz` does not probe Auth0, so health checks stay
green during an Auth0 outage — do not trust green health to mean "auth works".
Confirm by hitting the login flow or the callback endpoint.

**Detection:**
```bash
$C logs --tail=200 -f auth0_api          # token exchange / callback 5xx, timeouts
$C logs --tail=100 nginx                 # 5xx/502 on /api/auth* , 429s on /api/login
curl -fsS https://<host>/api/healthz && curl -fsS https://<host>/api/readyz   # still 200 — expected
# Check Auth0 status page / tenant dashboard for upstream incident.
```

**Triage (confirm):**
- Is it global or per-tenant? (Auth0 is global — this is usually all tenants.)
- Reproduce login manually; capture the callback error.
- Check `status.auth0.com` and the Auth0 dashboard event log.

**Containment:**
- If Auth0 is fully down: no in-gateway fallback exists by design (auth is
  Auth0-only; adding a local bypass would be a security regression). Do NOT
  weaken JWT verification or disable tenant-claim enforcement to "get users in".
- Optionally rate-limit at the edge is already active (`/api/(login|auth|logout)`
  5r/m) — leave it; it protects Auth0 from retry storms during an outage.

**Recovery:**
- Await Auth0 recovery or run the documented Auth0-side remediation
  (check application config, secrets/keys rotation if applicable).
- After recovery, verify: login → callback → cookie set → `/api/dashboard` 200
  → one governed SELECT returns rows.

**Verify / exit criteria:**
- Fresh login completes; existing sessions unaffected; audit log shows
  successful token validation events after recovery.

**Escalate to L3 when:** outage exceeds 1h, or remediation requires Auth0
account/config changes (L2).

---

## R2 — Tenant database outage

**Symptoms:** governed SELECT queries fail or 500/503 on `/api/graphql`; one
tenant (or all) affected; `GET /api/dashboard` (auth) still works; **health
checks remain green** — `sql_query_api /readyz` validates config, not DB
reachability.

**Detection:**
```bash
$C logs --tail=200 -f sql_query_api      # connection errors, timeouts, "tenant database ..."
$C logs --tail=100 web_app               # GraphQL client errors surfaced in the UI
# Confirm which tenant(s):
#   query logs show org_id/database_id for failed executions
#   /metrics: SQL_QUERY_TOTAL / SQL_QUERY_DURATION_SECONDS by org
```

**Triage:**
- Which tenant(s)? (Check `TENANT_DATABASES_JSON` mappings and the failing
  org IDs in logs.)
- Is SQLite or Postgres affected? (SQLite is file/`mode=ro`; Postgres is an
  external service `host.docker.internal` or remote connection string.)
- Read-only failure or reachability? A DB that is up but unprivileged fails
  differently than an unreachable DB — check the exact error in logs.

**Containment:**
- There is no read-through cache by design (governed path queries live DBs);
  the failure is per-database. Isolate to the affected tenant(s) rather than
  restarting the whole gateway.
- If the DB host is overloaded, the gateway's own limits (30 s timeout, 5 MB
  result cap, lock timeout) already shed load — do not raise them mid-incident.

**Recovery:**
- Restore DB connectivity (provider-side for Postgres; check
  `host.docker.internal` / host firewall for local Postgres).
- If a DB is corrupted or lost, follow the **backup/restore procedure (R6 →
  `BACKUP_DR.md`)** and restore from the last verified dump — un-validated data
  loss is a SEV1; escalate to L2 immediately.

**Verify / exit criteria:**
- A governed SELECT for the affected tenant returns rows; metrics show
  successful executions; audit log shows the restored tenant's queries.

**Escalate to L2/L3 when:** data loss suspected (SEV1), DB host unreachable
for >15 min, or the restore path is untested/unavailable.

---

## R3 — API error-spike (5xx surge)

**Symptoms:** elevated 5xx across auth0_api and/or sql_query_api; users report
intermittent failures; dashboards/metrics show error-rate climb. Can be caused
by a bad deploy (most common), a DB slowdown, or an AI-provider issue.

**Detection:**
```bash
$C logs --tail=300 -f auth0_api sql_query_api nginx   # look for 5xx + correlated messages
# Metrics: query total vs duration; error rate is visible in audit/log stream.
# Bad-deploy suspicion: when did containers change image / restart?
$C ps   # compare "Up X minutes" with the incident start
```

**Triage:**
- **Recent deploy?** If the spike started after a rollout → **R5 (deploy
  failure)** — roll back first, debug second.
- **Recent DB perf change?** → see R2 (timeout/backpressure symptoms).
- **New dependency?** AI-provider latency/5xx can make `/api/graphql` slow on
  the text-to-SQL path while plain SELECTs are fine — check query kind.

**Containment:**
- For a bad deploy: **roll back immediately** (R5) — do not debug the new
  version under load.
- For DB- or AI-induced spikes: the built-in limits (timeout, result cap,
  cost threshold, edge rate limits) cap damage; consider temporarily tightening
  cost threshold (`SQL_QUERY_COST_THRESHOLD`) if a specific workload is abusive.

**Recovery:**
- After rollback/limit tuning, confirm error rate returns to baseline.
- Capture stack traces and a log window for postmortem before the surge rolls
  off the log tail.

**Verify / exit criteria:** sustained baseline error rate (near 0) for 15 min;
no timeouts; dashboards/health green.

**Escalate to L2 when:** root cause is not a deploy/DB/AI change, or 5xx
persists >15 min after rollback.

---

## R4 — Reverse-proxy / TLS failure

**Symptoms:** site unreachable — TCP connect fails or TLS errors in the
browser; `https://<host>` times out; nginx `502/503` for some or all paths;
certificate warnings.

**Detection:**
```bash
$C ps                              # is nginx up? others up?
$C logs --tail=100 nginx           # listen errors, upstream connect failures, SSL errors
# Edge health:  /nginx-health (plaintext, for LB) — returns 200 only if nginx serves HTTP.
curl -k https://<host>/nginx-health ; curl -s http://<host>:80/ -o /dev/null -w "%{http_code}"
# Cert check:
echo | openssl s_client -connect <host>:443 -servername <host> 2>/dev/null | openssl x509 -noout -dates
```

**Triage:**
- nginx down vs upstreams down? (`502` = upstream unhealthy, nginx is fine; a
  refusal = nginx or network.)
- Certificate expired or wrong host? (`openssl s_client` shows dates/CN; certs
  live in `certs/` on the host, bind-mounted into nginx.)
- Check the host firewall/LB only forwards 443/80.

**Containment:**
- If the cert is expired but the key is valid: renew per the CA's process (or
  re-run `scripts/bootstrap-dev.sh` only for the mkcert lab setup; production
  uses trusted CA/ACME), then `$C restart nginx`.
- If nginx config changed recently and broke it: `git diff` the nginx.conf,
  fix, and restart — nginx exits fast on a bad config at startup.

**Recovery:**
- Correct the root cause (cert, `certs/` mount, upstream down via R2/R3),
  restart nginx, verify edge health + TLS dates.

**Verify / exit criteria:** `/nginx-health` 200; `curl` over TLS returns the
SPA; TLS dates valid; all three upstreams reachable through nginx.

**Escalate to L2 when:** host networking/LB misconfiguration beyond nginx, or
a valid CA re-issue is needed and not available to on-call.

---

## R5 — Failed deploy / secret misconfiguration

**Symptoms:** after a rollout, containers crash-loop, `up --wait` fails, or
services come up the new version behaves badly; "Required secret ... is not
set" in logs.

**Detection:**
```bash
$C ps                    # health status per container, restart counts
$C logs --tail=100 <service>
# Fail-fast guard: ENVIRONMENT=production aborts startup if a required secret
# or policy/tenant config is missing (read_secret loader + config validation).
```

**Triage:**
- Is a container crash-looping at startup (missing secret / bad JSON in
  `TENANT_DATABASES_JSON`/`POLICY_POLICIES_JSON`) or unhealthy after boot
  (dependency down)? READ the startup error before touching anything.
- Secret issues never require code changes — fix `/etc/gateway/gateway.env`
  (`chmod 600`, owned by `deploy`) and restart.

**Containment:**
- **Bad behavior after a deploy = roll back now**: GitHub Actions →
  *Deploy to Production* → `image_tag` = previous `vX.Y.Z` (see DOCKER_README
  "Deploy and roll back"). Compose recreates containers before health is
  confirmed, so roll back promptly.
- Do not edit code on the host; images are built in CI, the host only pulls.

**Recovery:**
- Stable again on the previous tag → investigate the failing image locally,
  fix in CI, ship a new `vX.Y.Z`.
- For secrets: set correct value in the env file, `chmod 600`, then
  `$C up -d --no-build --wait --wait-timeout 300`.

**Verify / exit criteria:** `up --wait` succeeds (all containers healthy),
login + one governed SELECT work, metrics/audit flowing, `image: <tag>`
matches the intended version in `$C ps`.

**Escalate to L2 when:** a rollback does not restore service, or the prod host
itself (disk, docker daemon, deploy SSH) is failing.

---

## R6 — Data loss / tenant database restore

**Symptoms:** tenant tables missing/empty or schema corrupted; audit log for a
tenant has gaps; a failed deploy or disk issue has made tenant data unavailable.
Confirmed data loss or corruption = **SEV1**.

**Detection:**
```bash
# BACKUP_DIR holds the daily dumps (default backups/ under the deploy dir):
ls -lt "$DEPLOY_DIR/backups" 2>/dev/null | head
# A backup incident issue tagged incident:backup means the scheduled backup failed:
gh issue list --label incident:backup
```

**Triage / containment:**
- Isolate the affected tenant(s); the gateway has no read-through cache, so a
  failed restore only affects tenants pointed at it.
- Do **not** delete or overwrite the existing dumps while investigating —
  copy the dump you plan to restore from elsewhere first.
- If the backup set itself is empty or unverified, the data-loss is
  unrecoverable from this pipeline — escalate to L2/L3 (DB provider / object
  storage) immediately.

**Recovery:**
- Full procedure in **`BACKUP_DR.md`** (issue #143). The identical, tested path
  is the drill:
  ```bash
  STAGING=1 RESTORE_ADMIN_URL='postgresql://admin:****@staging-db:5432/postgres' \
    DRILL_FORCE=0 ./scripts/drills/drill-restore.sh
  # prod restore: rerun the same steps manually — validate, restore to a scratch
  # name, then re-point the tenant, never pg_restore directly onto the live DB.
  ```
- Restore order: validate archive (`pg_restore --list`) → restore to a **new**
  database name → grant the gateway's least-privilege role → point the tenant
  binding at the restored database → verify with a governed SELECT + the audit
  log → only then drop the broken database.

**Verify / exit criteria:** the tenant's governed SELECTs return the expected
rows; audit events for the tenant resume; `scripts/probe-production.sh` P5 is
green for that tenant.

**Escalate to L2/L3 when:** the restore path is untested/unavailable, the
latest dumps are missing or unverified, or you need the DB provider to recover
point-in-time state.

---

## Post-incident

For every SEV1/SEV2:

1. Keep the incident thread current until recovery is verified.
2. Arrange the postmortem (5 business days): timeline, root cause, what
   worked/didn't, remediation issues with owners and due dates.
3. Update this runbook and the production readiness roadmap with any new
   failure mode or step that would have accelerated recovery.
4. If the incident exposed a monitoring gap, file it (see below) — untestable
   runbooks are worse than none, so add a drill entry.

## Suggested drills

- Rotate on-call monthly; run one drill per rotation using the executable
  harnesses:
  - **DB outage (R2)**: `STAGING=1 ./scripts/drills/drill-db-outage.sh`
    — injects an unreachable tenant DB, asserts the probe detects it (P5),
    then restores and verifies recovery.
  - **Rollback (R5)**: `./scripts/drills/drill-rollback.sh <previous-tag>`
    — re-pins a provisional `image_tag`, then rolls back to the previous
    release and verifies health; `--fault-tag` makes it assert a broken tag
    is caught first.
  - **Restore (R6)**: `STAGING=1 RESTORE_ADMIN_URL=… ./scripts/drills/drill-restore.sh`
    — restores the newest backup into a scratch database, verifies data,
    drops it. Run at least once before launch and after any restore-path
    change so the restore path is never "untested".
- Drills are exercises of the runbooks first and the system second — the goal
  is that a fresh on-call can execute each runbook end-to-end without help.
  Run drills in a staging stack (`STAGING=1` / a staging `DEPLOY_DIR`), never
  against production.

## Known monitoring gaps

Remaining gaps, tracked in M18. The probe pipeline closes the readiness blind
spots listed here, but the follow-up items still need building:

- **DB outage is invisible to `/readyz`**, so the probe adds a per-database
  connectivity check (P5, `sql_query_api/probe/run.py`). The probe only runs
  when the host is reachable and the pipeline is green — dashboards/alerts with
  per-DB latency remain tracked (#76, #144).
- **Auth0 outage is invisible to `/readyz`**; the probe's P6 hits the Auth0
  JWKS/authorization-server endpoint. Until in-app alerting is built, an Auth0
  outage shows up as an `incident:probe` issue (see R1).
- **No correlation IDs across services yet** (issue #147) — time-correlate via
  wall-clock timestamps in logs until then.
- **Backup/restore (#143)**: dumps are documented + automated + drill-shaped
  (`BACKUP_DR.md`, `scripts/backup-databases.py`, `drill-restore.sh`), but the
  restore drill has not been executed on a live stack, no off-host copy is
  verified yet (`BACKUP_OFFLOAD_CMD`), and RTO/RPO are targets, not yet
  measured. Until an off-host copy + a passed restore drill exist, host-level
  data loss still risks SEV1.
- **Auth0 outage drill (R1)** has no executable harness yet — simulate by
  pointing `AUTH0_*` at a dead URL in a staging stack until one is added.