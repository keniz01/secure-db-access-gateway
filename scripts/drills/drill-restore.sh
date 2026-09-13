#!/usr/bin/env bash
#
# Drill: tenant database restore (runbook R6, issue #143).
#
# Restores a backup (from scripts/backup-databases.py, pg_dump custom format)
# into a *scratch* database on a STAGING Postgres cluster, verifies the data
# came back, then drops the scratch database. It never touches the production
# databases or the tenant config — the scratch database is created from an
# admin URL you supply and is removed by a trap even on failure.
#
# Usage:
#   STAGING=1 RESTORE_ADMIN_URL='postgresql://admin:****@db:5432/postgres' \
#     ./scripts/drills/drill-restore.sh [backup.dump]
#
#   # no path = restore the newest dump in $BACKUP_DIR (default ./backups)
#
# RESTORE_ADMIN_URL must be a superuser/maintenance URL to the STAGING cluster
# (e.g. the local admin role), not a tenant database or the gateway account.
#
# Exit codes:
#   0 - restore successful and scratch database dropped
#   1 - drill failed
#   2 - usage/guard error

set -euo pipefail

if [[ "${STAGING:-0}" != "1" && "${DRILL_FORCE:-0}" != "1" ]]; then
  echo "refusing: this drill restores data into a database; set STAGING=1 (or DRILL_FORCE=1) to run it." >&2
  exit 2
fi

BACKUP="${1:-}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
ADMIN_URL="${RESTORE_ADMIN_URL:-}"
if [[ -z "$ADMIN_URL" ]]; then
  echo "set RESTORE_ADMIN_URL to a maintenance admin URL on the STAGING postgres cluster." >&2
  exit 2
fi
ADMIN_URL="$(printf '%s' "$ADMIN_URL" | sed -E 's#^(postgresql)\+[^:+]+(://)#\1\2#')"

if [[ -z "$BACKUP" ]]; then
  BACKUP="$(ls -t "$BACKUP_DIR"/*.dump 2>/dev/null | head -n1 || true)"
fi
if [[ -z "$BACKUP" || ! -f "$BACKUP" ]]; then
  echo "no backup file given and none found in $BACKUP_DIR" >&2
  exit 2
fi

TS="$(date +%Y%m%d_%H%M%S)"
SCRATCH_DB="drill_restore_${TS}"

cleanup() {
  psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $SCRATCH_DB;" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if ! command -v pg_restore >/dev/null 2>&1; then
  echo "pg_restore is required (install postgresql-client on the host)." >&2
  exit 2
fi
if ! command -v psql >/dev/null 2>&1; then
  echo "psql is required (install postgresql-client on the host)." >&2
  exit 2
fi

FAILED=0

echo "==> Drill R6: tenant database restore"
echo "==> 1/4 validate the backup archive ($BACKUP)"
if pg_restore --list "$BACKUP" >/dev/null; then
  echo "  archive is readable"
else
  echo "  archive FAILED pg_restore --list" >&2
  exit 1
fi

echo "==> 2/4 create scratch database $SCRATCH_DB"
psql "$ADMIN_URL" -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$SCRATCH_DB\";" >/dev/null
SCRATCH_URL="$(printf '%s' "$ADMIN_URL" | sed -E "s#/([^/]+)\$#/$SCRATCH_DB#")"

echo "==> 3/4 restore into scratch database"
if pg_restore --no-owner --no-privileges -d "$SCRATCH_URL" "$BACKUP" >/dev/null 2>&1; then
  echo "  pg_restore completed"
else
  echo "  pg_restore FAILED" >&2
  FAILED=1
fi

echo "==> 4/4 verify data + drop scratch database"
if (( ! FAILED )); then
  table_count="$(psql "$SCRATCH_URL" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema');" | tr -d '[:space:]' || true)"
  if [[ -n "$table_count" && "$table_count" -gt 0 ]]; then
    echo "  restored $table_count user table(s) into $SCRATCH_DB"
  else
    echo "  RESTORE VERIFY FAILED: no user tables in scratch database" >&2
    FAILED=1
  fi
fi

cleanup
trap - EXIT

if (( FAILED )); then
  echo "==> Drill R6 FAILED — fix the backup/restore path and re-run." >&2
  exit 1
fi
echo "==> Drill R6 PASSED: backup restored and scratch database cleaned up."