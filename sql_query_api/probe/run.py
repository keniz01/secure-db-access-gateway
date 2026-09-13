"""Database connectivity probe for the operator health pipeline.

Runs inside the ``sql_query_api`` container (or anywhere its venv is on the
path) and verifies that every configured tenant database is reachable and
connectable in read-only mode. This closes the runbook R2 gap: ``/readyz``
only validates configuration, never database reachability.

The probe deliberately mirrors the production connection building from
:dependency_container:`setup_container`: SQLite URLs are forced to
``mode=ro`` and nothing is ever written. Only ``SELECT 1`` / ``SET TRANSACTION
READ ONLY`` are emitted.

Exit codes:
    0 — every configured target connected and stayed read-only.
    1 — at least one target failed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from services.tenant_database_resolver import TenantDatabaseConfig, TenantDatabaseResolver


def normalize_connection_string(connection_string: str) -> str:
    """Mirror the read-only SQLite normalization used by the gateway."""
    if connection_string.startswith(("sqlite://", "sqlite+aiosqlite://")) and (
        ":memory:" not in connection_string and "mode=ro" not in connection_string
    ):
        prefix, path = connection_string.split("://", 1)
        if not path.startswith("/file:"):
            connection_string = f"{prefix}:///file:/{path.lstrip('/')}"
        connection_string = (
            f"{connection_string}&mode=ro&uri=true"
            if "?" in connection_string
            else f"{connection_string}?mode=ro&uri=true"
        )
    return connection_string


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Result of probing a single database target."""

    target: str
    ok: bool
    detail: str = ""


async def probe_one(
    connection_string: str,
    *,
    timeout_seconds: float = 10.0,
    reporter: Callable[[ProbeResult], None] | None = None,
) -> ProbeResult:
    """Open a read-only connection to one target and run ``SELECT 1``.

    Args:
        connection_string: The effective connection string for the target.
        timeout_seconds: Per-connection hard timeout.
        reporter: Optional sink for individual results (e.g. the CLI).

    Returns:
        ProbeResult describing the outcome.
    """
    normalized = normalize_connection_string(connection_string)

    async def _attempt() -> None:
        engine = create_async_engine(normalized, echo=False, future=True, pool_pre_ping=True)
        try:
            async with engine.begin() as conn:
                if normalized.startswith(("postgresql:", "postgresql+")):
                    await conn.execute(text("SET TRANSACTION READ ONLY;"))
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        await asyncio.wait_for(_attempt(), timeout=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - probe must convert any failure into a result
        result = ProbeResult(target=connection_string, ok=False, detail=repr(exc))
    else:
        result = ProbeResult(target=connection_string, ok=True)
    if reporter is not None:
        reporter(result)
    return result


def _effective_targets(configs: Iterable[TenantDatabaseConfig]) -> dict[str, str]:
    """Collapse the resolver bindings into unique probe targets.

    Returns a ``{target: label}`` map where the label is the shortest owning
    ``org_id/database_id`` (or ``replica``) for the target so operators can see
    which tenant a failure belongs to.
    """
    targets: dict[str, str] = {}
    for config in configs:
        label = f"{config.org_id}/{config.database_id}"
        targets.setdefault(config.connection_string, label)
        if config.replica_connection_string:
            targets[config.replica_connection_string] = f"{label} (replica)"
    return targets


async def probe_all(
    configs: Iterable[TenantDatabaseConfig],
    *,
    timeout_seconds: float = 10.0,
    reporter: Callable[[ProbeResult], None] | None = None,
) -> list[ProbeResult]:
    """Probe every unique target (primary and replica) across all tenants."""
    results = [
        await probe_one(target, timeout_seconds=timeout_seconds, reporter=reporter)
        for target in _effective_targets(configs)
    ]
    return results


def load_and_probe(*, timeout_seconds: float) -> list[ProbeResult]:
    """Load the resolver from the environment and probe every tenant database."""
    resolver = TenantDatabaseResolver.from_environment()
    configs = [config for bindings in resolver._bindings.values() for config in bindings]
    return asyncio.run(probe_all(configs, timeout_seconds=timeout_seconds))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-connection timeout in seconds.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: exit 0 when every configured database is reachable."""
    args = _parse_args(argv)

    def _report(result: ProbeResult) -> None:
        if result.ok:
            print(f"[OK]   {result.target}")
        else:
            print(f"[FAIL] {result.target}: {result.detail}", file=sys.stderr)

    try:
        results = load_and_probe(timeout_seconds=args.timeout)
    except Exception as exc:  # noqa: BLE001 - any config failure is reported as a failed probe
        print(f"probe aborted: {exc}", file=sys.stderr)
        return 1

    failed = [result for result in results if not result.ok]
    if failed:
        print(f"probe failed: {len(failed)} of {len(results)} database targets unreachable", file=sys.stderr)
        return 1
    print(f"probe ok: {len(results)} database targets reachable and read-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())