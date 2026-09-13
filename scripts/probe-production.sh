#!/usr/bin/env bash
#
# Production health probe for the incident-response automation (runbook R1-R5).
#
# Runs ON the production host (as the deploy user) and verifies, in order:
#   P1  TLS edge serves the SPA                  (nginx + web_app)
#   P2  TLS certificate is not near expiry       (R4)
#   P3  auth0_api is ready through the edge      (R1)
#   P4  sql_query_api is ready internally        (R2/R3)
#   P5  every tenant database is reachable       (R2 - closes the /readyz gap)
#   P6  Auth0 identity provider is reachable     (R1)
#
# The scheduled .github/workflows/probe.yml runs this over SSH, captures the
# output + exit code, and opens/updates/closes an incident issue accordingly.
#
# Exit code: 0 when all enabled checks pass, 1 otherwise. Every failure is
# printed on stdout as `[P<n> FAIL] ...`.
#
# Configuration (env):
#   DEPLOY_DIR       repo checkout to run compose from (default: cwd)
#   GATEWAY_ENV_FILE secrets file             (default /etc/gateway/gateway.env)
#   COMPOSE_PROJECT  --project-name for compose (optional)
#   MIN_TLS_DAYS     minimum cert validity days (default 30)
#   PROBE_HOST       host to probe over HTTPS (default localhost)

set -uo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-$(pwd)}"
GATEWAY_ENV_FILE="${GATEWAY_ENV_FILE:-/etc/gateway/gateway.env}"
MIN_TLS_DAYS="${MIN_TLS_DAYS:-30}"
PROBE_HOST="${PROBE_HOST:-localhost}"
BASE_URL="https://${PROBE_HOST}"

failures=0

log_ok()   { printf '[%s OK]   %s\n' "$1" "$2"; }
log_fail() { printf '[%s FAIL] %s\n' "$1" "$2"; failures=$((failures + 1)); }
log_skip() { printf '[%s SKIP] %s\n' "$1" "$2"; }

cd "$DEPLOY_DIR" || { echo "[FATAL] cannot cd to DEPLOY_DIR=$DEPLOY_DIR" >&2; exit 1; }

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose --env-file "$GATEWAY_ENV_FILE" -f docker-compose.yml -f docker-compose.prod.yml)
else
  COMPOSE=(docker-compose --env-file "$GATEWAY_ENV_FILE" -f docker-compose.yml -f docker-compose.prod.yml)
fi
if [[ -n "${COMPOSE_PROJECT:-}" ]]; then
  COMPOSE+=(--project-name "$COMPOSE_PROJECT")
fi

# ---------------------------------------------------------------------------
# P1 - TLS edge serves the SPA (nginx L7 + web_app behind it)
# ---------------------------------------------------------------------------
code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 15 "$BASE_URL/" 2>/dev/null || true)"
if [[ "$code" == "200" ]]; then
  log_ok P1 "TLS edge reached: $BASE_URL returned HTTP 200"
else
  log_fail P1 "TLS edge unreachable: got HTTP '${code:-connection error}' from $BASE_URL (runbook R4)"
fi

# ---------------------------------------------------------------------------
# P2 - TLS certificate validity (runbook R4)
# ---------------------------------------------------------------------------
not_after="$(echo | openssl s_client -servername "$PROBE_HOST" -connect "$PROBE_HOST:443" 2>/dev/null \
              | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
if [[ -z "$not_after" ]]; then
  log_skip P2 "could not read TLS certificate over $PROBE_HOST:443"
else
  expires_epoch="$(date -j -f "%b %e %H:%M:%S %Y %Z" "$not_after" +%s 2>/dev/null || date -d "$not_after" +%s 2>/dev/null)"
  now_epoch="$(date +%s)"
  if [[ -n "$expires_epoch" ]] && (( expires_epoch - now_epoch > MIN_TLS_DAYS * 86400 )); then
    log_ok P2 "TLS certificate valid for more than ${MIN_TLS_DAYS} days (expires: $not_after)"
  else
    log_fail P2 "TLS certificate expires soon or unreadable: '$not_after' (runbook R4)"
  fi
fi

# ---------------------------------------------------------------------------
# P3 - auth0_api readiness (runbook R1/R3)
# ---------------------------------------------------------------------------
body="$(curl -sk --max-time 15 "$BASE_URL/api/readyz" 2>/dev/null || true)"
if [[ "$body" == *'"status":"ready"'* ]]; then
  log_ok P3 "auth0_api /readyz through edge reports ready"
else
  log_fail P3 "auth0_api not ready through edge (got: ${body:-no response}) (runbook R3)"
fi

# ---------------------------------------------------------------------------
# P4 - sql_query_api readiness, internally (runbook R2/R3)
# ---------------------------------------------------------------------------
if "${COMPOSE[@]}" exec -T sql_query_api \
    python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8002/readyz', timeout=5)" >/dev/null 2>&1; then
  log_ok P4 "sql_query_api /readyz (internal) reports ready"
else
  log_fail P4 "sql_query_api /readyz (internal) failed (runbook R2/R3)"
fi

# ---------------------------------------------------------------------------
# P5 - tenant database connectivity (runbook R2: the /readyz gap)
# ---------------------------------------------------------------------------
if "${COMPOSE[@]}" exec -T sql_query_api python -m probe.run --timeout 10; then
  log_ok P5 "every configured tenant database reachable in read-only mode"
else
  log_fail P5 "one or more tenant databases unreachable (runbook R2; see $DEPLOY_DIR)"
fi

# ---------------------------------------------------------------------------
# P6 - Auth0 identity provider reachability (runbook R1)
# ---------------------------------------------------------------------------
auth0_issuer="$(grep -E '^AUTH0_ISSUER=' "$GATEWAY_ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)"
auth0_domain="${auth0_issuer#https://}"
page="$(echo "$auth0_domain" | cut -d/ -f1)"
if [[ -z "$page" ]]; then
  auth0_domain="$(grep -E '^AUTH0_DOMAIN=' "$GATEWAY_ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)"
  page="$auth0_domain"
fi
if [[ -z "$page" ]]; then
  log_skip P6 "no AUTH0_ISSUER / AUTH0_DOMAIN in $GATEWAY_ENV_FILE to probe"
elif curl -fsS --max-time 15 "https://${page}/.well-known/oauth-authorization-server" >/dev/null 2>&1 \
     || curl -fsS --max-time 15 "https://${page}/.well-known/jwks.json" >/dev/null 2>&1; then
  log_ok P6 "Auth0 provider reachable at ${page}"
else
  log_fail P6 "Auth0 provider unreachable at ${page} (runbook R1)"
fi

# ---------------------------------------------------------------------------
if (( failures > 0 )); then
  printf '\nprobe failed: %d check(s) failing (see lines above and RUNBOOKS.md)\n' "$failures" >&2
  exit 1
fi
printf '\nprobe ok: all checks passing\n'