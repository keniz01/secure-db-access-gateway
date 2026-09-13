---
name: Incident
about: Track an operational incident (SEV1/2/3). Follow RUNBOOKS.md first.
title: "[incident] "
labels: ["area:operations"]
assignees: []
---

## Severity

<!-- Pick one: SEV1 = full outage, SEV2 = significant degradation, SEV3 = no user impact -->
SEV:

## Summary

<!-- One or two lines: what is broken and who is affected -->

## Affected component / runbook

<!-- Component: nginx edge / auth0_api / sql_query_api / web_app / tenant DB / Auth0 / AI provider / deploy
     Runbook: R1 (Auth0), R2 (DB), R3 (API error spike), R4 (proxy/TLS), R5 (deploy/secrets) -->

## Started

<!-- UTC timestamp when the first symptom was observed -->

## Impact

<!-- Tenants/users affected, error rate or query failure counts -->

## Detection

<!-- How was this found: user report, automated probe (P-check), deploy gate -->

## Timeline

<!-- Append entries as the incident progresses: time, action, outcome -->


## Current actions

- [ ] Contain
- [ ] Recover
- [ ] Verify recovery
- [ ] Postmortem scheduled (SEV1/2 only, 5 business days)

## References

- RUNBOOKS.md (repo root) — incident runbooks and escalation chain
- PRODUCTION_READINESS_ROADMAP.md — Milestone C / M18