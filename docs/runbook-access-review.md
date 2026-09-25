# Access Review Runbook — RBAC1 Quarterly Certification

**Cadence:** Quarterly (or on role/policy change). Owner: Security / Org admin.

## 1. Export current state

```bash
# Policies (bundle source of truth)
cat sql_query_api/opa/data.json | jq '.policies'
# OPA effective access per org (via simulate - admin only)
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  https://localhost:8443/api/graphql -d '{"query":"query{simulatePolicy(request:{sqlStatement:\"SELECT 1\", databaseId:\"default\"}){allowed reason policyIds}}"}' | jq
```

## 2. Enumerate principals vs policies

* Auth0 dashboard: export users + `roles` + `https://app.secure-db-access-gateway.org/tenant_id`.
* For each `(org_id, database_id)` run `effective_access` (`sql_query_api/services/policy_engine.py:379` / OPA `gateway.rego:96` `effective_access`) to list `accessible_tables`/`allowed_columns`/`masked_columns`.
* GraphQL `introspect_schema` `sql_query_api/routes/sql_query_controller.py:506` already filters by `EffectiveAccess`; verify no extra tables leak.

## 3. SoD & hierarchy check

* Current hierarchy: `admin -> viewer` `gateway.rego:30` `role_hierarchy`. `admin` implicitly has viewer tables.
* SoD constraints: `gateway.rego:33` `sod_constraints` (empty = disabled). If you add `["editor","approver"]`, verify no principal holds both via `violates_sod`.
* Middleware no longer hardcodes `ALLOWED_ROLES` `middlewares/rbac_middleware.py:11` - all auth'd principals reach OPA; OPA denies if no allow matches.

## 4. Audit evidence

* `log_audit_event` types: `sql_query`, `simulate_policy_evaluated`, `auth_failed`, `schema_introspection` `sql_query_api/services/query_gateway.py:85`, `config/app_logger.py`.
* Pull last quarter: `grafana/loki` filter `event="policy_denied" OR "auth_failed"`.
* Confirm `AUDIT_LOG_RAW_SQL=false` in prod (no raw SQL in logs).

## 5. Approve / remediate

* Approve: sign `docs/access-review-YYYY-QN.md` with reviewer, date, policy bundle revision (`scripts/build-opa-bundle.sh` revision).
* Remediate: edit `sql_query_api/opa/data.json` or `policies/gateway.rego`, rebuild bundle `scripts/build-opa-bundle.sh $REV`, redeploy `docker compose -f docker-compose.yml -f docker-compose.prod.yml up`.

## 6. Checklist

- [ ] All orgs have at least one deny-by-default (no wildcard allow without table)
- [ ] No principal has SoD violation
- [ ] `admin` inheritance verified (viewer tables visible to admin)
- [ ] `action` field is `select` or `*` only (no `update`/`delete`)
- [ ] Audit logs retained per retention policy
