#!/usr/bin/env python3
"""Static freshness lint for ``RUNBOOKS.md`` (issue #142 / M18).

Runbooks that reference services, files, or endpoints that no longer exist are
worse than none — they teach operators to trust instructions that cannot work.
This check fails CI whenever the runbook drifts from the repository:

1. Every compose service named in the runbook exists in ``docker-compose.yml``.
2. Every path-like token (backticked) exists in the repository.
3. Every endpoint the runbook tells operators to curl is defined in source.
4. The automation the runbook points at still exists
   (probe module, probe workflow, drill scripts).

No network access; runs from anywhere (``python3 scripts/check-runbooks.py``).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = REPO_ROOT / "RUNBOOKS.md"
COMPOSE = REPO_ROOT / "docker-compose.yml"

SERVICE_NAME = re.compile(r"^\s{2}([a-z0-9_-]+):\s*$", re.MULTILINE)
SERVICE_REF = re.compile(r"\$C (?:logs --tail=\d+ -f|logs|restart) <([^>]+)>")
TICKED = re.compile(r"`([^`]+)`")
PATH_EXT = (".md", ".yml", ".yaml", ".py", ".sh", ".conf", ".toml", ".json", ".example")

ENDPOINT_SOURCES = {
    "/healthz": [REPO_ROOT / "auth0_api/app/routes/health_routes.py"],
    "/readyz": [
        REPO_ROOT / "auth0_api/app/routes/health_routes.py",
        REPO_ROOT / "sql_query_api/routes/health_routes.py",
    ],
    # The GraphQL route is defined as prefix + path in two literals.
    "/api/graphql": [REPO_ROOT / "auth0_api/app/routes/graphql_routes.py"],
    "/metrics": [REPO_ROOT / "sql_query_api/app_factory.py"],
    "/nginx-health": [REPO_ROOT / "nginx/nginx.conf"],
}

PLACEHOLDER_SERVICES = {"service"}


def services_in_compose() -> set[str]:
    """Return the service names declared under ``services:`` in Compose."""
    text = COMPOSE.read_text(encoding="utf-8")
    block = text.split("services:", 1)[1] if "services:" in text else ""
    return set(SERVICE_NAME.findall(block))


def services_referenced(runbook: str) -> set[str]:
    """Collect the service tokens operators are told to use with ``$C``."""
    referenced: set[str] = set()
    for match in SERVICE_REF.finditer(runbook):
        for name in match.group(1).split("|"):
            name = name.strip().strip("<>")
            if name and name not in PLACEHOLDER_SERVICES:
                referenced.add(name)
    return referenced


def path_tokens(runbook: str) -> list[str]:
    """Return backticked tokens that look like repository-relative paths."""
    tokens: list[str] = []
    for match in TICKED.finditer(runbook):
        token = match.group(1).strip()
        if token.startswith(("http", "$C", "$", "\\", "curl", "echo", "ssh")):
            continue
        if " " in token or "|" in token or token.endswith((")", ",")):
            continue
        if token.startswith(("<", "/etc", "https")):
            continue
        if "/" in token or token.endswith(PATH_EXT) or token.startswith("."):
            tokens.append("/" + token.lstrip("/"))
    return tokens


def endpoints_defined() -> list[str]:
    """Verify the endpoints the runbook pings exist in the source files."""
    missing: list[str] = []
    for endpoint, sources in ENDPOINT_SOURCES.items():
        if endpoint == "/api/graphql":
            source = next((s for s in sources if s.exists()), None)
            text = source.read_text(encoding="utf-8") if source else ""
            if not (source and 'prefix="/api"' in text and 'post("/graphql")' in text):
                missing.append(endpoint)
            continue
        if not any(endpoint in source.read_text(encoding="utf-8") for source in sources if source.exists()):
            missing.append(endpoint)
    return missing


def main() -> int:
    """Run all freshness checks and report failures."""
    runbook = RUNBOOK.read_text(encoding="utf-8")
    problems: list[str] = []

    known_services = services_in_compose()
    for service in sorted(services_referenced(runbook) - known_services):
        problems.append(f"runbook references unknown compose service: {service!r}")

    for token in sorted(path_tokens(runbook)):
        candidate = REPO_ROOT / token
        if not candidate.exists():
            problems.append(f"runbook references missing path: {token}")

    for endpoint in endpoints_defined():
        problems.append(f"runbook references endpoint that is not defined in source: {endpoint}")

    automation = [
        "sql_query_api/probe/run.py",
        "scripts/probe-production.sh",
        ".github/workflows/probe.yml",
        ".github/ISSUE_TEMPLATE/incident.md",
        "scripts/incident-start.sh",
    ]
    for path in automation:
        if not (REPO_ROOT / path).exists():
            problems.append(f"runbook automation missing: {path}")

    if problems:
        print("RUNBOOKS.md is out of sync with the repository:")
        for problem in problems:
            print(f"  - {problem}")
        print("Update RUNBOOKS.md (or the referenced code) and re-run this check.")
        return 1
    print("RUNBOOKS.md freshness check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())