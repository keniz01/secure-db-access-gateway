#!/usr/bin/env bash
#
# Drill: deploy rollback (runbook R5).
#
# Exercises the production rollback mechanism end to end: dispatch the deploy
# workflow with a provisional image tag, confirm health, then re-pin the
# previous known-good release tag and confirm the stack recovers. With
# --fault-tag you may point the provisional deploy at a tag you have built to
# be unhealthy; the drill then asserts the probe FAILS before it is rolled
# back — the honest version of a rollback drill.
#
# Usage:
#   ./scripts/drills/drill-rollback.sh <previous-good-tag>
#        [--fault-tag <tag>] [--dry-run]
#
# Requires: gh authenticated + the repo's deploy workflow secrets already
# configured (see DOCKER_README). PROBE_HOST/DEPLOY_HOST must reach the
# production/staging host over HTTPS.
#
# Exit codes:
#   0 - rollback verified healthy at <previous-good-tag>
#   1 - drill failed
#   2 - usage error

set -euo pipefail

GOOD_TAG="${1:-}"
FAULT_TAG=""
DRY_RUN=0
for arg in "${@:2}"; do
  case "$arg" in
    --fault-tag) FAULT_TAG="${3:-}"; shift 2 ;;
    --fault-tag=*) FAULT_TAG="${arg#*=}" ;;
    --dry-run) DRY_RUN=1 ;;
  esac
done

if [[ -z "$GOOD_TAG" ]]; then
  echo "usage: $0 <previous-good-tag> [--fault-tag <tag>] [--dry-run]" >&2
  exit 2
fi
if ! command -v gh >/dev/null 2>&1; then
  echo "gh CLI is required for this drill." >&2
  exit 2
fi

HOST="${PROBE_HOST:-${DEPLOY_HOST:-}}"
if [[ -z "$HOST" ]]; then
  echo "set PROBE_HOST (or DEPLOY_HOST) to the staging/production host." >&2
  exit 2
fi
PROBE=(env PROBE_HOST="$HOST" bash scripts/probe-production.sh)

echo "==> Drill R5: deploy rollback"
echo "==> 1/4 baseline (expect healthy): probe $HOST"
if "${PROBE[@]}" | grep -q 'probe ok'; then
  echo "  baseline healthy"
else
  echo "  baseline probe FAILING — nothing to roll back from; aborting." >&2
  exit 1
fi

dispatch_and_wait() {
  local tag="$1"
  echo "==> dispatching deploy with image_tag=$tag"
  local started
  started="$(date +%s)"
  if (( DRY_RUN )); then
    echo "  [dry-run] gh workflow run deploy.yml -f image_tag=$tag"
    return 0
  fi
  gh workflow run 'Deploy to Production' -f "image_tag=$tag" >/dev/null
  for _ in $(seq 1 60); do
    run="$(gh run list --workflow 'Deploy to Production' --json databaseId,status,conclusion,event,createdAt --limit 20 2>/dev/null \
            | (python3 -c "
import json,sys,datetime
now=datetime.datetime.now(datetime.timezone.utc)
rows=json.load(sys.stdin)
rows=[r for r in rows if r['event']=='workflow_dispatch']
if not rows: sys.exit(0)
r=rows[0]
import datetime as d
created=d.datetime.fromisoformat(r['createdAt'].replace('Z','+00:00'))
age=(now-created).total_seconds()
if age<120: print(r['status'], r['conclusion'] or '')
" 2>/dev/null || true))"
    if [[ "$run" =~ ^completed ]]; then
      local conclusion
      conclusion="${run#completed }"
      if [[ "$conclusion" == "success" ]]; then
        echo "  deploy $tag completed successfully"
        return 0
      fi
      echo "  deploy $tag completed with conclusion=$conclusion" >&2
      return 1
    fi
    sleep 10
  done
  echo "  deploy $tag timed out waiting for the workflow run" >&2
  return 1
}

PROVISIONAL_TAG="${FAULT_TAG:-edge}"
echo "==> 2/4 deploy provisional tag '$PROVISIONAL_TAG'"
if ! dispatch_and_wait "$PROVISIONAL_TAG"; then
  if [[ -n "$FAULT_TAG" ]]; then
    echo "  expected for --fault-tag"
  else
    exit 1
  fi
fi

if [[ -n "$FAULT_TAG" ]]; then
  echo "==> 3/4 assert the fault tag is INDEED unhealthy (expect probe FAIL)"
  if "${PROBE[@]}" | grep -q 'probe ok'; then
    echo "  fault tag was healthy — rollback drill pointless; failing." >&2
    exit 1
  fi
  echo "  fault confirmed: probe failed under image_tag=$FAULT_TAG"
else
  echo "==> 3/4 (no --fault-tag: provisional edge was deployed; continuing to rollback)"
fi

echo "==> 3/4 → roll back: re-pin image_tag=$GOOD_TAG"
dispatch_and_wait "$GOOD_TAG"

echo "==> 4/4 verify recovery at $GOOD_TAG"
if "${PROBE[@]}" | grep -q 'probe ok'; then
  echo "==> Drill R5 PASSED: rollback to $GOOD_TAG restored a healthy stack."
else
  echo "==> Drill R5 FAILED: rollback deployed but probe still failing." >&2
  exit 1
fi