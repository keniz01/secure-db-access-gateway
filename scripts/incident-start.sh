#!/usr/bin/env bash
#
# File an incident issue in one command (runbook escalation step).
#
# Usage:
#   ./scripts/incident-start.sh SEV2 "Auth0 login failures"
#
# Creates an issue from .github/ISSUE_TEMPLATE/incident.md with the severity,
# an `incident:manual` label and spec-compliant labels (area:operations,
# severity-based priority), and prints the issue URL. Follow the incident
# template fields and RUNBOOKS.md during the incident.
#
# Requires the gh CLI authenticated with issue:write scope.

set -euo pipefail

SEV="${1:-}"
SUMMARY="${2:-}"
shift 2 || true

if [[ -z "$SEV" || -z "$SUMMARY" ]]; then
  echo "usage: $0 <SEV1|SEV2|SEV3> \"summary\"" >&2
  exit 2
fi
if [[ "$SEV" != "SEV1" && "$SEV" != "SEV2" && "$SEV" != "SEV3" ]]; then
  echo "severity must be SEV1, SEV2 or SEV3" >&2
  exit 2
fi

case "$SEV" in
  SEV1) priority="priority:blocker" ;;
  SEV2) priority="priority:high" ;;
  SEV3) priority="priority:nice-to-have" ;;
esac

url="$(
  gh issue create \
    --template incident \
    --title "[incident] $SUMMARY" \
    --label "incident:manual" \
    --label "area:operations" \
    --label "$priority"
)"

echo "Incident filed: $url"
echo "Follow RUNBOOKS.md (detection/containment/recovery) and update the timeline."