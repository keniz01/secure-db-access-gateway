# Infrastructure Requirements (Deferred)

> These are documented for when external services are commissioned. Services currently run with self-contained databases (sqlite/testcontainers) and no external dependencies for CI/local dev.

---

## 1. Audit Log Immutable Storage

**Gap:** Audit logs written to stdout only — no tamper-proof retention.

**Required:**
- **Target:** S3 Object Lock (compliance mode) / CloudWatch Logs (retention + KMS) / Grafana Loki + WORM / Elasticsearch with snapshot policy
- **Pipeline:** `sql_query_api` → Vector/Fluent Bit → sink
- **Credentials:** Separate IAM role / service account for write-only; read-only for SIEM
- **Hash-chain:** Application-layer `event.hash = SHA256(prev_hash || payload)` stored with event
- **Alerts:** Pipeline lag >5min, write failures, hash mismatch on read

**Code Interface (already implemented):**
- `log_audit_event()` emits `audit_hash` + `prev_audit_hash` (SHA256 chain)
- `AUDIT_LOG_RAW_SQL=false` default excludes raw SQL from audit

---

## 2. Centralized Log Aggregation & Monitoring

**Gap:** No centralized logging, alerting, or anomaly detection.

**Required:**
- **Stack:** Grafana Loki + Prometheus + Alertmanager, or CloudWatch / Elasticsearch / Splunk
- **Retention:** 1 year hot, 7 years cold (compliance)
- **Dashboards:** Request volume, error rates, auth failures, policy denies, quota hits, latency
- **Alerting Rules (Prometheus):**
  - `rate(auth_failed_total[5m]) > 10` → Critical
  - `rate(policy_denied_total[5m]) > 50` → Warning
  - `rate(query_rejected_overload_total[5m]) > 5` → Warning
  - `histogram_quantile(0.99, rate(sql_query_duration_seconds_bucket[5m])) > 10` → Warning
  - `up{job="sql-query-api"} == 0` → Critical
- **Notifications:** PagerDuty / Slack / Email via Alertmanager

---

## 3. mTLS Between Services

**Gap:** Service-to-service communication unencrypted.

**Required:**
- **Option A (Service Mesh):** Linkerd / Istio / Cilium — automatic mTLS, rotation, SPIFFE identities
- **Option B (Manual):** cert-manager + step-ca / Vault PKI — issue certs per service, rotate daily
- **Services:** nginx ↔ auth0_api, nginx ↔ sql_query_api, auth0_api ↔ sql_query_api, sql_query_api ↔ OPA, sql_query_api ↔ Redis, sql_query_api ↔ PostgreSQL
- **Client Config:** HTTP clients (httpx, redis-py, asyncpg) configured with client certs + CA verification

---

## 4. Backup DR Test Automation

**Gap:** DR documented but not tested automatically.

**Required:**
- **Schedule:** Weekly CI run of `scripts/drills/drill-restore.sh` against staging PostgreSQL
- **Metrics:** Measure RTO (target ≤30min), RPO (target ≤24h), verify data integrity
- **Staging DB:** Dedicated PostgreSQL instance restored from latest backup
- **Validation:** Row counts, checksums, schema integrity, tenant binding re-application

---

## 5. Secret Management (Production)

**Gap:** Manual `.env` file on host.

**Required:**
- **Platform:** HashiCorp Vault / AWS Secrets Manager / GCP Secret Manager / Azure Key Vault
- **Injection:** Init containers / CSI driver / ENV injection at pod start
- **Rotation:** Automated rotation for DB passwords, API keys, JWT signing keys
- **Audit:** Access logging on all secret reads

---

## 6. WAF / L7 Protection

**Gap:** Only nginx rate limiting, no L7 inspection.

**Required:**
- **Option A (Managed):** Cloudflare / AWS WAF / Azure Front Door / GCP Cloud Armor
- **Option B (Self-hosted):** ModSecurity + OWASP CRS on nginx
- **Rules:** SQLi, XSS, RCE, protocol anomalies, bad bots, geo-blocking
- **Mode:** Blocking in production; detection-only in staging

---

## 7. Runtime Integrity Monitoring (FIM)

**Gap:** No file integrity monitoring on hosts/containers.

**Required:**
- **Option A (Host):** AIDE / Tripwire on host OS
- **Option B (Container):** Falco / Tracee for runtime security (exec, file, network anomalies)
- **Option C (Cloud):** AWS Inspector / GCP Security Command Center / Azure Defender
- **Alerts:** Unexpected binary execution, config file changes, privileged container spawn

---

## 8. Network Policies (Zero Trust)

**Gap:** Docker networks only; no egress control.

**Required:**
- **Platform:** Kubernetes NetworkPolicies / Cilium / Calico, or cloud security groups / NACLs
- **Rules:**
  - `sql_query_api` → egress allowlist: PostgreSQL hosts (from tenant config), OPA, Redis, OpenRouter AI, OTel
  - `auth0_api` → egress allowlist: Auth0 domain, Redis, OTel, sql_query_api
  - `nginx` → egress allowlist: auth0_api, web_app, OTel
  - `web_app` → egress: none (served by nginx)
  - `opa` → egress: bundle URL (S3/GCS/HTTP)
  - All → deny by default

---

## 9. Admin Access (MFA, Bastion, Audit)

**Gap:** No dedicated admin access controls.

**Required:**
- **Access:** Teleport / Tailscale / AWS SSO / GCP IAP / Azure Bastion
- **MFA:** Required for all admin access (hardware keys preferred)
- **Audit:** Session recording, command logging, just-in-time access
- **Separation:** Dedicated admin roles (not shared `postgres` superuser)

---

## 10. Change Management (GitOps)

**Gap:** Manual `docker compose up` on host.

**Required:**
- **Platform:** ArgoCD / Flux (GitOps) or GitHub Environments + required approvals
- **Process:** PR → CI → Staging deploy (auto) → Manual approval → Prod deploy
- **Rollback:** `git revert` + auto-deploy previous image
- **Drift Detection:** ArgoCD/Flux continuous sync + alert on drift

---

## 10. Vulnerability Management & SLSA

**Gap:** No automated dependency updates.

**Required:**
- **Dependabot/Renovate:** `.github/dependabot.yml` — weekly updates, auto-merge patches, group minor/major
- **Container Scanning:** Trivy/Grype in GHCR on push; block critical vulns
- **Container provenance:** `.github/workflows/docker.yml` calls `.github/workflows/docker-builder.yml`. The reusable workflow pushes images by digest from a permission-limited build job with BuildKit `mode=max` provenance (only public VITE URLs are passed as build args), then uses a separate runner to create a signed GitHub artifact attestation and verify that exact bundle against the image digest, repository, reusable-workflow identity, predicate, source ref, and source revision. A final job promotes public tags only after verification succeeds. Every action in the reusable builder is pinned to a released commit SHA.
- **Patch SLA:** Critical 24h, High 7d, Medium 30d, Low 90d (documented)

---

## 11. Bug Bounty / Vulnerability Disclosure

**Gap:** No formal program.

**Required:**
- **Platform:** HackerOne / Bugcrowd / Intigriti, or internal `SECURITY.md` with disclosure email
- **Scope:** All public endpoints, OAuth flows, GraphQL API
- **Rewards:** Defined tiers (Critical $5k+, High $2k+, Medium $500+)

---

## 12. Data Encryption at Rest (Infra)

**Gap:** Audit logs, tenant config, rotated credentials unencrypted on disk.

**Required:**
- **KMS:** AWS KMS / GCP KMS / Azure Key Vault / HashiCorp Vault Transit
- **Envelope Encryption:** App encrypts with DEK, DEK wrapped by KEK in KMS
- **Targets:**
  - Audit log files / Loki chunks
  - Tenant config (`/config/tenant_databases.json`)
  - Rotated credentials (`/creds/`)
  - PostgreSQL data directory (RDS encryption / LUKS)
  - Redis persistence (if AOF/RDB enabled)

---

## 13. DLP / Egress Content Inspection

**Gap:** No inspection of outbound data.

**Required:**
- **Option A (Proxy):** Squid / Envoy with DLP rules (regex for PII, keys, SQL results)
- **Option B (Sidecar):** Custom egress proxy scanning response bodies
- **Rules:** Credit cards, SSN, API keys, JWTs, large result sets (>5MB)
- **Action:** Block + alert on match

---

## 14. CSP Reporting Endpoint

**Gap:** CSP enforced but no violation reporting.

**Required:**
- **Endpoint:** `POST /csp-report` in `sql_query_api` and `auth0_api`
- **Storage:** Loki / Elasticsearch / CloudWatch
- **Alert:** Spike in CSP violations → potential XSS attempt

---

## 15. Bug Bounty / Vulnerability Disclosure

**Gap:** No formal program.

**Required:**
- **Platform:** HackerOne / Bugcrowd / Intigriti, or internal `SECURITY.md` with disclosure email
- **Scope:** All public endpoints, OAuth flows, GraphQL API
- **Rewards:** Defined tiers (Critical $5k+, High $2k+, Medium $500+)

---

## 16. Data Classification & Privacy

**Gap:** No data classification labels, no DPIA/GDPR artifacts.

**Required:**
- **Classification:** Label audit events, tenant config, query results (PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED)
- **DPIA:** Document for EU tenant data (GDPR Art. 35)
- **DPA:** Data Processing Addendum for subprocessors (Auth0, OpenRouter, cloud provider)
- **Privacy Notice:** End-user facing notice for data subjects
- **Data Subject Rights:** Automated export/delete for tenant data

---

## Summary: Infra Provisioning Checklist

| # | Component | Cloud-Native (AWS) | Cloud-Native (GCP) | Self-Hosted |
|---|-----------|-------------------|-------------------|-------------|
| 1 | Audit Storage | S3 Object Lock + CloudWatch | Cloud Logging + GCS | Loki + MinIO + WORM |
| 2 | Log Aggregation | CloudWatch / OpenSearch | Cloud Logging / Elasticsearch | Loki + Promtail |
| 3 | Alerting | CloudWatch Alarms + SNS | Cloud Monitoring + Alerting | Alertmanager + Grafana |
| 4 | mTLS | ACM + ALB / App Mesh | Certificate Manager + Mesh | cert-manager + step-ca |
| 5 | Backup DR | RDS Snapshots + Cross-region | Cloud SQL + DR replica | pgBackRest + MinIO |
| 6 | Secrets | Secrets Manager | Secret Manager | Vault |
| 7 | WAF | AWS WAF + CloudFront | Cloud Armor + Cloud CDN | ModSecurity + nginx |
| 8 | FIM | Inspector + GuardDuty | Security Command Center | Falco + AIDE |
| 9 | Network Policies | Security Groups + VPC | VPC Service Controls | Cilium / Calico |
| 10 | Admin Access | IAM Identity Center + MFA | IAP + MFA | Teleport / Tailscale |
| 11 | GitOps | CodeDeploy / ECS | Cloud Deploy / GKE | ArgoCD / Flux |
| 12 | Vuln Mgmt | Inspector + ECR Scan | Artifact Analysis | Trivy + Dependabot |
| 13 | SLSA Provenance | GitHub Actions artifact attestations | GitHub Actions artifact attestations | GitHub Actions artifact attestations + reusable workflow |
| 14 | Encryption at Rest | KMS + EBS/RDS/S3 encryption | CMEK + Cloud KMS | LUKS + Vault Transit |
| 15 | DLP | Macie + Custom | DLP API + Custom | Custom egress proxy |
| 16 | CSP Reporting | CloudWatch Logs | Cloud Logging | Loki |

---

**Next:** Code gaps are addressed in the repository. Infra items require platform decisions and provisioning.
