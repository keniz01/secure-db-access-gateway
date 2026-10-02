#!/usr/bin/env python3
"""Enforce the security exception register against the CI workflows.

A security scanner that is configured not to fail the build is not a control;
it is a report. The gap is invisible because `continue-on-error: true` looks
identical in review whether it is a deliberate, expiring risk acceptance or a
leftover from a scan that was failing on day one.

This check closes that gap:

1. Every non-blocking security control in `.github/workflows/` (job-level or
   step-level ``continue-on-error: true``, Grype ``fail-build: false``, Trivy
   ``exit-code: "0"``) must map to an entry in the exception register.
2. Every register entry must carry an id, scope, reason, owner, tracking
   reference, at least one compensating control and an ISO ``expires`` date.
3. ``expires`` must be in the future and at most ``MAX_EXPIRY_DAYS`` away, so a
   downgrade has to be renewed on purpose.
4. Every register entry must still correspond to a real non-blocking control,
   so exceptions cannot accumulate after the control is fixed.

No network access. Requires PyYAML (it parses the workflows themselves):
``python3 -m pip install pyyaml && python3 scripts/check-security-exceptions.py``
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - dependency guard
    sys.exit("PyYAML is required: python3 -m pip install pyyaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTER = REPO_ROOT / ".github" / "security-exceptions.yml"
DEFAULT_WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# An exception is a deferral, not a waiver. Requiring renewal inside this
# window bounds how long a control can stay non-blocking without a new
# decision being recorded.
MAX_EXPIRY_DAYS = 180
VALID_STATUSES = {"accepted", "revoked"}
REASON_MIN_CHARS = 80

# Grype/Trivy configure severity gating through action inputs rather than
# continue-on-error, so those inputs are scanned as downgrade markers too.
NON_BLOCKING_INPUTS = (
    ("anchore/scan-action", "fail-build", "false"),
    ("aquasecurity/trivy-action", "exit-code", "0"),
)

EXCEPTION_ID_RE = re.compile(r"^SEC-EXC-\d{3,}$")


def load_register(register: Path) -> list[dict]:
    if not register.is_file():
        sys.exit(f"missing exception register: {register}")
    data = yaml.safe_load(register.read_text()) or {}
    if data.get("version") != 1:
        sys.exit("exception register must declare `version: 1`")
    exceptions = data.get("exceptions")
    if not isinstance(exceptions, list):
        sys.exit("exception register must contain an `exceptions` list")
    return exceptions


def find_non_blocking_controls(workflow: dict, source: Path) -> dict[str, str]:
    """Return {control_id: location} for every intentionally non-blocking control."""
    found: dict[str, str] = {}
    for job_name, job in (workflow.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        if job.get("continue-on-error") is True:
            found[job_name] = f"{source.name}: job `{job_name}`"

        for index, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            label = step.get("name") or f"step {index}"
            uses = str(step.get("uses") or "")
            with_ = step.get("with") or {}

            if step.get("continue-on-error") is True:
                found[f"{job_name}/{label}"] = f"{source.name}: step `{label}` in job `{job_name}`"
                continue

            for action, key, bad_value in NON_BLOCKING_INPUTS:
                if action in uses and str(with_.get(key)) == bad_value:
                    control = f"{job_name}/{label}"
                    found[control] = (
                        f"{source.name}: step `{label}` in job `{job_name}` sets "
                        f"`{key}: {bad_value}`"
                    )
    return found


def parse_expiry(value: object, entry_id: str, errors: list[str]) -> dt.date | None:
    if not isinstance(value, str):
        errors.append(f"{entry_id}: `expires` must be an ISO date string, got {value!r}")
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        errors.append(f"{entry_id}: `expires` is not a valid ISO date: {value!r}")
        return None


def validate_entry(entry: dict, today: dt.date, errors: list[str]) -> set[str]:
    """Validate one register entry; return the control ids it claims to cover."""
    if not isinstance(entry, dict):
        errors.append(f"register entry must be a mapping, got {type(entry).__name__}")
        return set()

    entry_id = entry.get("id")
    if not isinstance(entry_id, str) or not EXCEPTION_ID_RE.match(entry_id):
        errors.append(f"invalid exception id {entry_id!r}: expected SEC-EXC-NNN")
        entry_id = str(entry_id)

    for field in ("control", "scope", "owner", "tracking"):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{entry_id}: missing required field `{field}`")

    reason = entry.get("reason")
    if not isinstance(reason, str) or len(reason.strip()) < REASON_MIN_CHARS:
        errors.append(
            f"{entry_id}: `reason` must be a real decision record "
            f"(at least {REASON_MIN_CHARS} characters)"
        )

    compensating = entry.get("compensating_controls")
    if not isinstance(compensating, list) or not compensating:
        errors.append(f"{entry_id}: `compensating_controls` must list at least one control")

    status = entry.get("status")
    if status not in VALID_STATUSES:
        errors.append(f"{entry_id}: `status` must be one of {sorted(VALID_STATUSES)}")

    claimed: set[str] = set()
    control = entry.get("control")
    if isinstance(control, str) and control.strip():
        claimed.update(part.strip() for part in control.split(",") if part.strip())

    if status == "revoked":
        # A revoked exception documents history; it must not authorize anything.
        return set()

    expiry = parse_expiry(entry.get("expires"), entry_id, errors)
    if expiry is not None:
        if expiry < today:
            errors.append(
                f"{entry_id}: exception expired on {expiry.isoformat()}; "
                "make the control blocking or record a renewal decision"
            )
        elif (expiry - today).days > MAX_EXPIRY_DAYS:
            errors.append(
                f"{entry_id}: `expires` is {(expiry - today).days} days out, "
                f"more than the {MAX_EXPIRY_DAYS}-day maximum"
            )
    return claimed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", type=Path, default=DEFAULT_REGISTER)
    parser.add_argument("--workflows", type=Path, default=DEFAULT_WORKFLOW_DIR)
    args = parser.parse_args()
    register = args.register.resolve()
    workflow_dir = args.workflows.resolve()

    today = dt.date.today()
    errors: list[str] = []

    try:
        workflows = sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml"))
    except OSError as exc:  # pragma: no cover - filesystem guard
        sys.exit(f"cannot list workflows: {exc}")
    if not workflows:
        sys.exit(f"no workflows found in {workflow_dir}")

    non_blocking: dict[str, str] = {}
    for path in workflows:
        try:
            workflow = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            sys.exit(f"{path.name}: invalid YAML: {exc}")
        for control, location in find_non_blocking_controls(workflow, path).items():
            non_blocking[control] = location

    claimed_by: dict[str, str] = {}
    for entry in load_register(register):
        entry_id = entry.get("id") if isinstance(entry, dict) else "?"
        claimed = validate_entry(entry, today, errors)
        for control in claimed:
            if control in claimed_by:
                errors.append(
                    f"{entry_id}: control `{control}` is already covered by {claimed_by[control]}"
                )
            else:
                claimed_by[control] = str(entry_id)

    for control, location in sorted(non_blocking.items()):
        if control not in claimed_by:
            errors.append(
                f"non-blocking control `{control}` has no registered exception "
                f"({location}); register it in {register.name} or make it blocking"
            )

    for control, entry_id in sorted(claimed_by.items()):
        if control not in non_blocking:
            errors.append(
                f"{entry_id}: exception covers `{control}`, which is no longer "
                "non-blocking; delete the stale entry"
            )

    print(f"security exception register: {register}")
    print(f"workflows checked: {', '.join(p.name for p in workflows)}")
    print(f"non-blocking controls found: {len(non_blocking)}")
    for control, entry_id in sorted(claimed_by.items()):
        if control in non_blocking:
            print(f"  registered: {control} -> {entry_id}")
    print(f"today: {today.isoformat()} (max expiry window: {MAX_EXPIRY_DAYS} days)")

    if errors:
        print(f"\nFAIL: {len(errors)} security exception register problem(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("\nOK: every non-blocking security control is registered, owned and unexpired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
