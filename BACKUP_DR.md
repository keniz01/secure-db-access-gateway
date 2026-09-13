# Backup & Disaster Recovery

Issue #143 (M18) — tenant database backup/restore procedures, retention, and
DR objectives.

**Guiding rule:** until the restore path is exercised by a drill, treat any
data loss as SEV1. The drill below makes it a checked, repeatable procedure.

## Scope

What is backed up:

- **Tenant databases** — every unique PostgreSQL target derived from the same
  `TENANT_DATABASES_JSON`/`_FILE` config the gateway uses. This covers the data
  and, because the gateway's audit logging writes into the governed tenant DBs,
  the audit trail is included. Nothing extra to enable.
- **Gateway code + edge config** — rebuilt from the git repo and `docker
  compose` files; the running images are pinned deployment tags (`git log` /
  `docker image ls` has the tag). No backup needed: regenerate from source.
- **Secrets** — env-only, injected by the orchestrator from
  `/etc/gateway/gateway.env` (orchestrated equivalent). They live outside the
  container and must be restored from the operator's credential store
  (see `SECURITY.md`). They are **not** in the git repo or the backup script.

Not backed up: SQLite/dev targets (the gateway forces `mode=ro` on them; they
are not production data sources) and ephemeral infrastructure (networks,
caches). `BUILD_INFO`, image artifacts, and containers recreate from source.

## Backup procedure

Run on the host (not inside a container) so the OS `pg_dump`/`pg_restore` are
available:

```bash
GATEWAY_ENV_FILE=/etc/gateway/gateway.env BACKUP_DIR=/opt/secure-db-access-gateway/backups \
  python3 scripts/backup-databases.py
```

- `pg_dump --format=custom --no-owner --no-privileges`, one verified file per
  database: `<org>__<db>__<UTC timestamp>.dump`.
- **Verified** every run: `pg_restore --list` must parse the archive, or the
  run fails. Empty dumps are deleted.
- **Retention**: newest `BACKUP_RETENTION` (default 14) dumps per database;
  older ones are pruned per-database (never a shared glob).
- **Read replicas**: omit by default; `--include-replicas` backs up replicas
  when you want to offload dump load.
- `--dry-run` prints the target plan without dumping — CI uses this to ensure
  the script always matench the deployed config.
- Any failure sets exit code 1 and opens an `incident:backup` issue.

### Scheduling

`.github/workflows/backup.yml` runs `scripts/backup-databases.py` daily
(`17 2 * * *`, UTC) over the same SSH+secrets channel as the deploy and probe
workflows, and opens/closes an `incident:backup` issue. Add a manual run
(`workflow_dispatch`) **before risky changes** so the RPO for a rollback window
is minutes, not a day.

If you prefer not to depend on GitHub reachability, add a host crontab:

```
# /etc/crontab — run as root (or the deploy user):
17 2 * * *  cd /opt/secure-db-access-gateway && BACKUP_DIR=backups \
  /usr/bin/env python3 scripts/backup-databases.py >> /var/log/gateway-backup.log 2>&1
```

### Off-host copies (mandatory for real DR)

On-host dumps do not survive a host failure. Set `BACKUP_OFFLOAD_CMD` on the
workflow (or run it yourself) to push them somewhere else, e.g.:

```bash
BACKUP_OFFLOAD_CMD='rsync -a --remove-source-files' python3 scripts/backup-databases.py
```

A reasonable target is `rsync`-compatible object storage or another host in a
different failure domain. **Until an off-host copy is verified, the effective
RPO for a host-level failure is "restore from the last host snapshot / rebuild
from scratch" — treat that as SEV1.**

## Restore procedure + drill

Restore = recreate the tenant DB name, then `pg_restore` the dump into it.
The drill automates this safely:

```bash
STAGING=1 RESTORE_ADMIN_URL='postgresql://admin:****@staging-db:5432/postgres' \
  ./scripts/drills/drill-restore.sh
```

It validates the archive, creates a scratch database
(`drill_restore_<timestamp>`), restores into it, confirms user tables exist,
and drops the scratch database (even on failure, via `trap`). **Run this drill
at least once before launch and again on any restore-path change.**

Manual restore (the exact steps the drill performs):

1. `pg_restore --list <backup>.dump` — confirm the archive is readable.
2. `createdb <db_name>` (or `pg_restore -d` at a new name to avoid touching prod).
3. `pg_restore --no-owner --no-privileges -d <db_name> <backup>.dump`.
4. Grant the gateway's least-privilege role (`USE`/`SELECT` on the restored
   schema), re-add the tenant binding in `TENANT_DATABASES_JSON`, and verify
   with `scripts/probe-production.sh` (P5).

## DR objectives

| Objective | Default | Tightening |
| --- | --- | --- |
| **RPO** | ≤ 24 h (daily backup) | run a backup before risky changes (`workflow_dispatch`) — minutes |
| **RTO** (single tenant) | ≤ 30 min target | pre-warmed restore + documented grants cut this further |
| Retention | 14 daily dumps/target | raise `BACKUP_RETENTION`; add monthly snapshots via 2nd env |
| Off-host | required | `BACKUP_OFFLOAD_CMD`; verify a restore from the off-host copy |

These are **targets to validate, not promises**: record the measured restore
time from the drill (it prints nothing timed yet) and put the number in the
roadmap acceptance note for #143.

## Least-privilege grants (issue #148)

The gateway account only needs read access; the backup/restore tooling runs
under platform credentials. Document the production grant set with #148:
`USAGE` on the schema + `SELECT` on tables/views, and nothing else. The
`--no-owner --no-privileges` dump format also means a restore does not depend
on a specific role existing on the target cluster.

## During an incident

Follow **RUNBOOKS.md R6 (data loss / restore)**; this document is the reference
it points to. The restore drill is the same procedure, run in a safe order:
identify the affected tenant + last valid dump → validate → restore to a scratch
name first → point the tenant at the restored database → verify with the probe
→ close out in the incident issue.