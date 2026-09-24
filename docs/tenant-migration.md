# Tenant Database Mapping — Future Migration (Item 2)

> **Status:** Deferred. Item 1 (OPA bundles for `POLICY_POLICIES_JSON`) is implemented.
> This doc records the plan for `TENANT_DATABASES_JSON` scalability.

## Problem

`TENANT_DATABASES_JSON` / `TENANT_DATABASES_JSON_FILE` (`sql_query_api/services/tenant_database_resolver.py:105`) is **infra routing**, not authz:

```python
TenantDatabaseConfig(org_id, database_id, connection_string, data_schema, ...)
```

* Inline `TENANT_DATABASES_JSON` `.env.example:64` is a single-line JSON blob. Unscalable: hits env-var size limits (~128k), requires app restart (`routes/sql_query_controller.py:121` loads once), no versioning/auditing, secrets + metadata co-mingled.
* `*_FILE` already exists via `shared/shared_secrets/secrets.py:55` (`env var → *_FILE → default`) but still requires host file management.

Do **not** move connection strings into OPA/Cedar — PDP should not hold DB credentials.

## Target

File-only secret, mounted by orchestrator, with optional DB-backed registry later.

### Phase A — File-only, no code change (recommended next)

1. **Prod:** set `TENANT_DATABASES_JSON_FILE=/run/secrets/tenants.json` (or `/etc/gateway/tenants.json`), unset `TENANT_DATABASES_JSON`. Provide file via:
   * Docker `secrets:` / `configs:`, Kubernetes `ExternalSecrets` / `SealedSecrets`, Vault Agent, Infisical, 1Password Connect, SOPS-encrypted file.
2. **Permissions:** `chmod 600`, owned by gateway user. Never commit.
3. **Rotation:** rewrite file atomically (`mv` onto target), then `SIGHUP` or rolling restart. No bundle hot-reload — resolver is not a PDP.
4. **Validation at deploy:** `python -c "import json; json.load(open('/run/secrets/tenants.json'))"` + `scripts/probe-production.sh` check.

`.env.example` already documents the shape; keep it as example, not prod source.

### Phase B — Registry with cache (when >~50 tenants or frequent rotation)

Replace file with control-plane store:

* Table `tenant_bindings(org_id, database_id, connection_string_encrypted, data_schema, ...)`, encrypted at rest (Vault transit / KMS).
* `TenantDatabaseResolver` gains `from_db(cached)` with TTL (e.g. 60s) + `get_tenant_database_config` cache invalidation on webhook.
* Keep `*_FILE` as bootstrap fallback for cold start.

Connection pool: per-engine `DB_POOL_SIZE + DB_MAX_OVERFLOW` `ARCHITECTURE.md:210` must stay below role `CONNECTION LIMIT` (`scripts/setup_least_privilege_gateway_role.sql`).

## Non-goals

* No XACML/Cedar for tenant routing.
* OPA bundles (`sql_query_api/opa/config.yaml`, `scripts/build-opa-bundle.sh`) remain for **policies** only.

## Checklist when implementing

- [ ] Update `.env.example` to mark `TENANT_DATABASES_JSON` deprecated, `*_FILE` required in prod
- [ ] Add `docker-compose.yml` secret mount example (commented)
- [ ] Add `scripts/validate-tenants.py` (JSON schema + connection_string format)
- [ ] Document rotation runbook in `docs/wiki/`
- [ ] Add CI check: `python -m json.tool /run/secrets/tenants.json` in `ci.yml` smoke test
