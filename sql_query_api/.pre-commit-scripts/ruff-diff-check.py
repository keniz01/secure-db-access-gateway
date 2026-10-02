#!/usr/bin/env python3
"""
Run Ruff only on changed lines of staged Python files.

Usage (pre-commit):
    entry: python3 sql_query_api/.pre-commit-scripts/ruff-diff-check.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys


def get_changed_lines(filename: str) -> set[int]:
    """Return a set of changed line numbers for the given file based on the staged diff."""
    try:
        diff = subprocess.check_output(  # noqa: S603 - filename is argv-controlled git input
            ["git", "diff", "--cached", "-U0", "--", filename],  # noqa: S607 - git must come from PATH
            text=True,
        )
    except subprocess.CalledProcessError:
        return set()

    changed = set()
    for line in diff.splitlines():
        # Hunk header example: @@ -10,0 +11,3 @@
        if line.startswith("@@"):
            try:
                hunk = line.split(" ")[2]  # +11,3
                start, length = hunk[1:].split(",")
                start, length = int(start), int(length)
                for i in range(start, start + length):
                    changed.add(i)
            except Exception:  # noqa: S110 - best-effort hunk parsing; skip malformed headers
                pass

    return changed


def find_ruff() -> str | None:
    """
    Locate the ruff executable.

    pre-commit's `language: system` inherits the ambient PATH, which does not
    include the sql_query_api virtualenv where ruff is a dev dependency. Fall
    back to that venv before giving up, otherwise the hook cannot run at all.
    """
    found = shutil.which("ruff")
    if found:
        return found

    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in (
        os.path.join(package_root, ".venv", "bin", "ruff"),
        os.path.join(package_root, ".venv", "Scripts", "ruff.exe"),
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def main(filenames: list[str]) -> int:
    """Run Ruff on provided filenames and filter results to changed lines only."""
    ruff = find_ruff()
    if ruff is None:
        print(  # noqa: T201 - CLI output
            "Error: ruff executable not found on PATH and no virtualenv ruff found.\n"
            "       Install it with:  pip install ruff  (or: uv sync in sql_query_api/)"
        )
        return 1

    # Run Ruff and request JSON output for easy filtering
    try:
        ruff_output = subprocess.check_output(  # noqa: S603 - filenames are argv-controlled
            [ruff, "check", "--output-format=json", "--force-exclude", *filenames],
            text=True,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as e:
        # Ruff exits nonzero on findings — still captures output
        ruff_output = e.output
    except OSError as exc:  # unreadable/not-executable binary, etc.
        print(f"Error: could not execute ruff at {ruff}: {exc}")  # noqa: T201 - CLI output
        return 1

    if not ruff_output.strip():
        return 0

    try:
        problems = json.loads(ruff_output)
    except json.JSONDecodeError:
        print("Error: Could not parse Ruff JSON output.")  # noqa: T201 - CLI output
        print(ruff_output)  # noqa: T201 - CLI output
        return 1

    # Build map of changed lines per file.
    #
    # Keys must be absolute: `ruff check --output-format=json` reports absolute
    # filenames, whereas the filenames pre-commit passes in are relative to this
    # directory. Keying the map by the raw relative path meant
    # `filename in changed_map` was never true, so no finding could ever be
    # reported and the hook silently exited 0 on everything.
    changed_map = {os.path.abspath(f): get_changed_lines(f) for f in filenames}

    violations = []
    for p in problems:
        reported = p.get("filename")
        line = p.get("location", {}).get("row")
        if not reported:
            continue
        absolute = os.path.abspath(reported)
        if absolute in changed_map and line in changed_map[absolute]:
            violations.append(p)

    # Output violations (if any)
    if violations:
        print("Ruff found issues in changed lines only:")  # noqa: T201 - CLI output
        print(json.dumps(violations, indent=2))  # noqa: T201 - CLI output
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
