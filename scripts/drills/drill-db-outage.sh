#!/usr/bin/env bash
#
# Drill: tenant database outage (runbook R2).
#
# Simulates an unreachable tenant database by pointing a *temporary* compose
# override at a bogus connection string, restarts only sql_query_api, and
# verifies the drill-pipeline detects it (probe check P5 fails). It then
# removes the override, restores the stack, and verifies recovery (P5 passes).
#
# The secrets file and every tenant connection string are left untouched — the
# override lives only in a scratch compose overlay inside /tmp and is torn down
# on exit (trap), even on failure.
#
# Usage (must be an explicit drill environment, never production):
#   STAGING=1 ./scripts/drills/drill-db-outage.sh
#   # optional: COMPOSE_PROJECT=... to target a named staging stack
#
# Exit codes:
#   0 - outage detected, recovery verified, stack restored
#   1 - drill failed (or not run in a staging context)
#   2 - usage error

set -euo pipefail

if [[ "${STAGING:-0}" != "1" && "${DRILL_FORCE:-0}" != "1" ]]; then
  echo "refusing: this drill breaks a tenant database mapping; set STAGING=1 (or DRILL_FORCE=1) to run it." >&2
  exit 2
fi

DEPLOY_DIR="${DEPLOY_DIR:-$(pwd)}"
GATEWAY_ENV_FILE="${GATEWAY_ENV_FILE:-/etc/gateway/gateway.env}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-}"
FAKE_DB='{"drill-org": {"drill-db": "postgresql+asyncpg://drill:unreachable@127.0.0.1:59999/nope"}}'
OVERRIDE="$(mktemp /tmp/drill-db-outage.XXXXXX.yml)"

cleanup() {
  rm -f "$OVERRIDE"
  if [[ -d "$DEPLOY_DIR" ]]; then
    (cd "$DEPLOY_DIR" && "${COMPOSE[@]}" up -d --no-deps --force-recreate sql_query_api >/dev/null 2>&1 || true)
  fi
}
trap cleanup EXIT

cd "$DEPLOY_DIR"

if docker compose version >/dev/null 2>&1; then
  COMPOSE_BASE=(docker compose --env-file "$GATEWAY_ENV_FILE" -f docker-compose.yml -f docker-compose.prod.yml)
else
  COMPOSE_BASE=(docker-compose --env-file "$GATEWAY_ENV_FILE" -f docker-compose.yml -f docker-compose.prod.yml)
fi
if [[ -n "$COMPOSE_PROJECT" ]]; then
  COMPOSE_BASE+=(--project-name "$COMPOSE_PROJECT")
fi
COMPOSE=("${COMPOSE_BASE[@]}")

PROBE=(bash scripts/probe-production.sh)
PROBE_ENV=("DEPLOY_DIR=$DEPLOY_DIR" "GATEWAY_ENV_FILE=$GATEWAY_ENV_FILE")
if [[ -n "$COMPOSE_PROJECT" ]]; then
  PROBE_ENV+=("COMPOSE_PROJECT=$COMPOSE_PROJECT")
fi

cat > "$OVERRIDE" <<YAML
services:
  sql_query_api:
    environment:
      TENANT_DATABASES_JSON: '${FAKE_DB}'
YAML

FAILED=0

echo "==> Drill R2: tenant database outage"
echo "==> 1/4 baseline probe (expect healthy)"
if env "${PROBE_ENV[@]}" "${PROBE[@]}" | grep -q '\[P5 OK\]'; then
  echo "  baseline P5 healthy"
else
  echo "  baseline P5 was ALREADY failing — no outage to drill; aborting." >&2
  exit 1
fi

echo "==> 2/4 inject unreachable tenant database (override: $OVERRIDE)"
"${COMPOSE_BASE[@]}" -f "$OVERRIDE" up -d --no-deps --force-recreate sql_query_api

echo "==> 3/4 detect the outage (expect P5 FAIL)"
sleep 3
if env "${PROBE_ENV[@]}" "${PROBE[@]}" | grep -q '\[P5 FAIL\]'; then
  echo "  detected: P5 flags the unreachable tenant database (runbook R2)"
else
  echo "  DETECTION MISSED: probe did not flag the injected outage." >&2
  FAILED=1
fi

echo "==> 4/4 recover (remove override, restore the stack)"
rm -f "$OVERRIDE"
trap - EXIT
"${COMPOSE_BASE[@]}" up -d --no-deps --force-recreate sql_query_api
sleep 3
if env "${PROBE_ENV[@]}" "${PROBE[@]}" | grep -q '\[P5 OK\]'; then
  echo "  recovered: P5 healthy again; stack restored"
else
  echo "  RECOVERY VERIFY FAILED: P5 still failing after restore." >&2
  FAILED=1
fi

if (( FAILED )); then
  echo "==> Drill R2 FAILED — fix the probe/runbook and re-run." >&2
  exit 1
fi
echo "==> Drill R2 PASSED: outage detected and recovered."