"""Hermetic load harness for the governed query pipeline.

Establishes the gateway's **capacity envelope**: a concurrency level at which
p95 latency, p99 latency, and the hard-error rate stay inside documented
budgets, plus the resource usage (CPU/RSS) at that level. Because the harness
runs on an aiosqlite fixture in-process, it is fully reproducible in CI
(no Postgres, no Auth0, no network); a production-graded run is the same
harness pointed at a deployed stack (see ``LOAD_TESTING.md``).

The harness patches the RBAC token validator and injects the seeded tenant
binding so every request rides the exact governed path used in production:
RBAC middleware -> rate-limit middleware -> GraphQL resolver ->
TenantServiceProvider -> SqlSafetyChecker -> PolicyEvaluator -> repository
(read-only SQLite, row cap, per-row limit) -> masking + audit.

Scenarios:

- ``normal``: N virtual users (unique client IPs via ``X-Forwarded-For``) each
  issue a realistic weighted query mix for a fixed duration. Ramps across
  ``--ramp`` user counts and reports the envelope (the largest count that
  honors the budgets).
- ``attack``: a single client IP fires queries as fast as possible; expects
  429s to appear (in-memory rate limiter) while the app stays stable.

Budgets (ms) are the accepted thresholds: p95 <= 1000, p99 <= 2000,
hard-error rate <= 1% (row-cap/timeout/429 are classified and do not count
as hard errors).

Exit codes:
    0 - envelope found (normal) or rate limiting validated (attack)
    1 - budgets not met / envelope below the minimum requested concurrency
    2 - usage/config error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx

P95_BUDGET_MS = 1000.0
P99_BUDGET_MS = 2000.0
MAX_HARD_ERROR_RATE = 0.01

STATUSES = ("completed", "pending", "failed", "refunded", "processing")
EXECUTE_QUERY = """query Exec($sql: String!) {
  execute_sql_statement(request: { sql_statement: $sql, database_id: "default" })
}"""
INTROSPECT_QUERY = """query Introspect {
  introspect_schema(database_id: "default") {
    tables {
      name
      schema_name
      columns { name type nullable is_primary }
      foreign_keys { column foreign_table foreign_column }
    }
  }
}"""
COST_QUERY = """query Cost($sql: String!) {
  estimate_query_cost(sql_statement: $sql, database_id: "default") { score level }
}"""
PING_QUERY = "query { ping }"


@dataclass
class RunSummary:
    """Aggregated outcome of one scenario step."""

    users: int
    duration_seconds: float
    total: int
    ok: int
    errors: Counter[str] = field(default_factory=Counter)
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def qps(self) -> float:
        """Requests per second across the step duration."""
        return self.total / max(self.duration_seconds, 1e-9)

    @property
    def hard_error_rate(self) -> float:
        """Proportion of requests that failed the hard-error classes."""
        hard = sum(
            count for kind, count in self.errors.items() if kind in {"http_5xx", "timeout", "connection"}
        )
        return hard / max(self.total, 1)

    def percentile(self, p: float) -> float:
        """Return the ``p``-th latency percentile in milliseconds."""
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, max(0, int(math_ceil(len(ordered) * p)) - 1))
        return ordered[index]
```

I need math_ceil. Import math. Oops I wrote a bad stub — I referenced math_ceil without importing. Let me write the full file cleanly in one pass. Let me restart and write the complete run.py now, carefully in full.</think>

<｜DSML｜tool_calls>
<｜DSML｜invoke name="write">
<｜DSML｜parameter name="content" string="true">"""Hermetic load harness for the governed query pipeline.

Establishes the gateway's **capacity envelope**: a concurrency level at which
p95 latency, p99 latency, and the hard-error rate stay inside documented
budgets, plus the resource usage (CPU/RSS) at that level. Because the harness
runs on an aiosqlite fixture in-process, it is fully reproducible in CI
(no Postgres, no Auth0, no network); a production-graded run is the same
harness pointed at a deployed stack (see ``LOAD_TESTING.md``).

The harness patches the RBAC token validator and injects the seeded tenant
binding so every request rides the exact governed path used in production:
RBAC middleware -> rate-limit middleware -> GraphQL resolver ->
TenantServiceProvider -> SqlSafetyChecker -> PolicyEvaluator -> repository
(read-only SQLite, row cap) -> masking + audit.

Scenarios:

- ``normal``: N virtual users (unique client IPs via ``X-Forwarded-For``) each
  issue a realistic weighted query mix for a fixed duration. Ramps across
  ``--ramp`` user counts and reports the envelope (the largest count that
  honors the budgets).
- ``attack``: a single client IP fires queries as fast as possible; expects
  429s to appear (in-memory rate limiter) while the app stays stable.

Budgets (ms) are the accepted thresholds: p95 <= 1000, p99 <= 2000,
hard-error rate <= 1%. Row-cap/timeout/429 responses are classified and do not
count as hard errors.

Exit codes:
    0 - envelope found (normal) or rate limiting validated (attack)
    1 - budgets not met / envelope below the minimum requested concurrency
    2 - usage/config error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

P95_BUDGET_MS = 1000.0
P99_BUDGET_MS = 2000.0
MAX_HARD_ERROR_RATE = 0.01

STATUSES = ("completed", "pending", "failed", "refunded", "processing")
EXECUTE_QUERY = """query Exec($sql: String!) {
  execute_sql_statement(request: { sql_statement: $sql, database_id: "default" })
}"""
INTROSPECT_QUERY = """query Introspect {
  introspect_schema(database_id: "default") {
    tables {
      name
      schema_name
      columns { name type nullable is_primary }
      foreign_keys { column foreign_table foreign_column }
    }
  }
}"""
COST_QUERY = """query Cost($sql: String!) {
  estimate_query_cost(sql_statement: $sql, database_id: "default") { score level }
}"""
PING_QUERY = "query { ping }"

HARD_ERROR_KINDS = {"http_5xx", "timeout", "connection", "exception"}
BASE_HEADERS = {"Authorization": "Bearer loadtest-token"}


@dataclass
class RunSummary:
    """Aggregated outcome of one scenario step."""

    users: int
    duration_seconds: float
    total: int
    ok: int
    errors: Counter[str] = field(default_factory=Counter)
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def qps(self) -> float:
        """Return requests per second across the step duration."""
        return self.total / max(self.duration_seconds, 1e-9)

    @property
    def hard_error_rate(self) -> float:
        """Return the proportion of requests in the hard-error classes."""
        hard = sum(count for kind, count in self.errors.items() if kind in HARD_ERROR_KINDS)
        return hard / max(self.total, 1)

    def percentile(self, p: float) -> float:
        """Return the ``p``-th latency percentile in milliseconds."""
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, max(0, int(math.ceil(len(ordered) * p)) - 1))
        return ordered[index]

    def within_budget(self) -> bool:
        """Return whether latency and error budgets are honored."""
        return (
            self.percentile(0.95) <= P95_BUDGET_MS
            and self.percentile(0.99) <= P99_BUDGET_MS
            and self.hard_error_rate <= MAX_HARD_ERROR_RATE
        )


def _redact_message(detail: Any) -> str:
    """Normalize an error detail into a compact classification key."""
    return type(detail).__name__ if detail is not None else "unknown"


class LoadClient:
    """Small wrapper implementing the loadtest HTTPS client with latency tracking."""

    def __init__(self, transport: httpx.ASGITransport) -> None:
        self._client = httpx.AsyncClient(transport=transport, base_url="http://loadtest", timeout=35.0)

    async def aclose(self) -> None:
        """Close the underlying HTTPX client."""
        await self._client.aclose()

    async def send(self, payload: dict[str, Any], client_ip: str) -> tuple[float, int, str]:
        """POST one GraphQL payload and return latency, status, and classification."""
        headers = dict(BASE_HEADERS)
        headers["X-Forwarded-For"] = client_ip
        started = time.perf_counter()
        try:
            response = await self._client.post("/graphql", json=payload, headers=headers)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
        except (httpx.TimeoutException, httpx.TransportError):
            return (time.perf_counter() - started) * 1000.0, 0, "timeout"
        if response.status_code >= 500:
            return elapsed_ms, response.status_code, "http_5xx"
        if response.status_code == 429:
            return elapsed_ms, response.status_code, "rate_limited"
        try:
            body = response.json()
        except ValueError:
            return elapsed_ms, response.status_code, "http_error"
        if "errors" in body:
            kinds = {_redact_message(item.get("message", "")) for item in body["errors"]}
            kind = "row_cap" if "row" in " ".join(kinds).lower() else "exception"
            return elapsed_ms, response.status_code, kind
        return elapsed_ms, response.status_code, "ok"


def seed_database(db_path: Path, users: int, transactions: int) -> None:
    """Create a deterministically populated SQLite fixture for the load run."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, org_id TEXT, created_at TEXT)")
        conn.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL, status TEXT, created_at TEXT)")
        conn.execute("CREATE INDEX idx_transactions_user ON transactions (user_id)")
        conn.execute("CREATE INDEX idx_transactions_status ON transactions (status)")
        user_rows = [
            (i, f"user{i}@example.com", "org-acme", "2026-01-01T00:00:00Z" if i % 7 else "2026-03-03T00:00:00Z")
            for i in range(1, users + 1)
        ]
        conn.executemany("INSERT INTO users VALUES (?, ?, ?, ?)", user_rows)
        transaction_rows: list[tuple[int, int, float, str, str]] = []
        for i in range(1, transactions + 1):
            transaction_rows.append(
                (
                    i,
                    (i % users) + 1,
                    round((i * 7) % 1000 / 10, 2),
                    STATUSES[i % len(STATUSES)],
                    "2026-01-01T00:00:00Z",
                )
            )
        conn.executemany("INSERT INTO transactions VALUES (?, ?, ?, ?, ?)", transaction_rows)
        conn.commit()
    finally:
        conn.close()


def seed_sql_payloads(rng: random.Random, users: int, *, row_cap_heavy: bool = False) -> list[dict[str, Any]]:
    """Return a realistic weighted GraphQL query mix for one user iteration."""
    point = f"SELECT id, email, org_id, created_at FROM users WHERE id = {rng.randint(1, users)}"
    simple = (
        f"SELECT id, user_id, amount, status, created_at FROM transactions "
        f"WHERE status = '{rng.choice(STATUSES)}' ORDER BY created_at DESC LIMIT 100"
    )
    join = (
        "SELECT u.email, count(*) AS n FROM users u "
        "JOIN transactions t ON t.user_id = u.id GROUP BY u.email ORDER BY n DESC LIMIT 20"
    )
    aggregate = "SELECT status, count(*) AS n, sum(amount) AS total FROM transactions GROUP BY status"
    heavy = (
        "SELECT t.id, t.user_id, t.amount, u.email FROM transactions t "
        "JOIN users u ON u.id = t.user_id ORDER BY t.amount DESC LIMIT 2000"
    )
    row_cap = "SELECT * FROM transactions"
    options: list[tuple[dict[str, Any], int]] = [
        ({"query": PING_QUERY}, 3),
        ({"query": COST_QUERY, "variables": {"sql": simple}}, 4),
        ({"query": INTROSPECT_QUERY}, 12),
        ({"query": EXECUTE_QUERY, "variables": {"sql": point}}, 18),
        ({"query": EXECUTE_QUERY, "variables": {"sql": simple}}, 24),
        ({"query": EXECUTE_QUERY, "variables": {"sql": join}}, 16),
        ({"query": EXECUTE_QUERY, "variables": {"sql": aggregate}}, 10),
        ({"query": EXECUTE_QUERY, "variables": {"sql": heavy}}, 8),
        ({"query": EXECUTE_QUERY, "variables": {"sql": row_cap}}, 5 if row_cap_heavy else 0),
    ]
    return [item for item, weight in options for _ in range(weight)]


def _fake_validate_access_token(token: str | None) -> dict[str, Any] | None:
    """Return a fixed validated principal for loadtest requests."""
    del token
    from auth import TENANT_ID_CLAIM

    return {
        "sub": "loadtest-user",
        "email": "loadtest@example.com",
        TENANT_ID_CLAIM: "org-acme",
        "roles": ["admin"],
    }


def build_load_app() -> Any:
    """Create the FastAPI application with the seeded resolver and fake auth."""
    import middlewares.rbac_middleware as rbac  # noqa: PLC0415
    import sql_query_controller as controller  # noqa: PLC0415
    from app_factory import create_app  # noqa: PLC0415
    from dependencies.tenant_service_provider import TenantServiceProvider  # noqa: PLC0415
    from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker  # noqa: PLC0415
    from services.policy_engine import PolicyEvaluator  # noqa: PLC0415
    from services.query_gateway import GovernedQueryGateway  # noqa: PLC0415
    from services.tenant_database_resolver import TenantDatabaseResolver  # noqa: PLC0415

    resolver = TenantDatabaseResolver.from_environment()
    controller._tenant_database_resolver = resolver
    controller._tenant_service_provider = TenantServiceProvider(resolver)
    controller._sql_safety_checker = DefaultSqlSafetyChecker()
    controller._query_gateway = GovernedQueryGateway(
        lambda: controller._tenant_service_provider,
        controller._sql_safety_checker,
        policy_evaluator=PolicyEvaluator.from_environment(),
    )
    rbac.validate_access_token = _fake_validate_access_token  # type: ignore[assignment]
    return create_app()


def _client_ips(seed: int, users: int) -> list[str]:
    """Return one distinct client-IP per virtual user."""
    rng = random.Random(seed)
    return [f"10.{(i * 37) % 255}.{(i * 13) % 255}.{(i * 7) % 255}" for i in range(users)]


async def _run_user(
    client: LoadClient,
    payloads: list[dict[str, Any]],
    client_ip: str,
    duration: float,
    rng: random.Random,
    summary: RunSummary,
) -> None:
    """Run one virtual user performing a weighted query mix for ``duration``."""
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        payload = rng.choice(payloads)
        elapsed, status, kind = await client.send(payload, client_ip)
        summary.latencies_ms.append(elapsed)
        if kind == "ok":
            summary.ok += 1
        else:
            summary.errors[kind] += 1
        summary.total += 1
        del status


async def _run_attack(
    client: LoadClient,
    duration: float,
    payload: dict[str, Any],
    summary: RunSummary,
) -> None:
    """Fire one query kind back-to-back from a single client IP."""
    client_ip = "203.0.113.9"
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        elapsed, status, kind = await client.send(payload, client_ip)
        summary.latencies_ms.append(elapsed)
        if kind == "ok":
            summary.ok += 1
        else:
            summary.errors[kind] += 1
        summary.total += 1
        del status


async def _sample_resources(interval: float, stop: asyncio.Event) -> tuple[float, float]:
    """Sample peak CPU % and peak RSS (MB) of this process via ``ps``."""
    peak_cpu = 0.0
    peak_rss = 0.0
    while not stop.is_set():
        try:
            probe = subprocess.run(
                ["ps", "-o", "rss=,pcpu=", "-p", str(os.getpid())],
                capture_output=True,
                text=True,
                check=True,
            )
            parts = probe.stdout.split()
            if len(parts) >= 2:
                peak_rss = max(peak_rss, int(parts[0]) / 1024.0)
                peak_cpu = max(peak_cpu, float(parts[1]))
        except (subprocess.SubprocessError, ValueError):
            pass
        await asyncio.sleep(interval)
    return peak_cpu, peak_rss


async def _run_step_scenario_async(
    client: LoadClient,
    users: int,
    duration: float,
    payloads: list[dict[str, Any]],
    seed: int,
) -> RunSummary:
    """Run the normal scenario at a fixed concurrency and return the summary."""
    rng = random.Random(seed)
    summary = RunSummary(users=users, duration_seconds=duration)
    workers = [
        _run_user(client, payloads, client_ip, duration, random.Random(seed + i), summary)
        for i, client_ip in enumerate(_client_ips(seed, users))
    ]
    await asyncio.gather(*workers)
    return summary


async def _run_attack_scenario_async(
    client: LoadClient,
    duration: float,
    payload: dict[str, Any],
) -> RunSummary:
    """Run the attack scenario and return the summary."""
    summary = RunSummary(users=1, duration_seconds=duration)
    await _run_attack(client, duration, payload, summary)
    if not summary.total:
        summary.errors["no_requests"] += 1
    return summary


def _print_step(summary: RunSummary) -> None:
    """Print one scenario step as a compact table row."""
    line = (
        f"users={summary.users:>3}  total={summary.total:>5}  qps={summary.qps:>7.1f}  "
        f"p50={summary.percentile(0.50):>7.1f}  p95={summary.percentile(0.95):>7.1f}  "
        f"p99={summary.percentile(0.99):>7.1f}  hard_err={summary.hard_error_rate * 100:>5.2f}%  "
        f"errs={dict(summary.errors)}"
    )
    sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


def _print_envelope(steps: Sequence[RunSummary]) -> None:
    """Print the accepted envelope based on the largest honoring step."""
    honored = [step for step in steps if step.within_budget()]
    if not honored:
        sys.stdout.write("envelope: no tested concurrency stayed within budget\n")
        return
    envelope = honored[-1]
    sys.stdout.write(
        "envelope: max in-budget concurrency is "
        f"{envelope.users} users at {envelope.qps:.1f} qps "
        f"(p95={envelope.percentile(0.95):.1f}ms, p99={envelope.percentile(0.99):.1f}ms)\n"
    )


def _summary_to_dict(steps: Sequence[RunSummary], cpu: float, rss: float) -> dict[str, Any]:
    """Serialize scenario steps and resource peaks for ``--out``."""
    return {
        "steps": [
            {
                "users": step.users,
                "total": step.total,
                "ok": step.ok,
                "qps": round(step.qps, 2),
                "p50_ms": round(step.percentile(0.50), 2),
                "p95_ms": round(step.percentile(0.95), 2),
                "p99_ms": round(step.percentile(0.99), 2),
                "hard_error_rate": round(step.hard_error_rate, 5),
                "errors": dict(step.errors),
                "within_budget": step.within_budget(),
            }
            for step in steps
        ],
        "peak_cpu_percent": round(cpu, 1),
        "peak_rss_mb": round(rss, 1),
        "budgets_ms": {"p95": P95_BUDGET_MS, "p99": P99_BUDGET_MS},
    }


def run_scenario(
    *,
    scenario: str,
    users: int,
    duration: float,
    seeds: int,
    connection_string: str,
    attack_query: dict[str, Any] | None = None,
) -> tuple[list[RunSummary], float, float]:
    """Run load scenario inside a fresh event loop and return summaries + peaks."""
    app = build_load_app()
    transport = httpx.ASGITransport(app=app)
    client = LoadClient(transport)

    async def _run() -> tuple[list[RunSummary], float, float]:
        stop = asyncio.Event()
        sampler = asyncio.create_task(_sample_resources(0.5, stop))
        payloads = seed_sql_payloads(random.Random(1), seeds, row_cap_heavy=(scenario == "normal"))
        try:
            steps = [_run_step_scenario_async(client, users, duration, payloads, seeds)]
            if scenario == "attack":
                attack = attack_query or {"query": EXECUTE_QUERY, "variables": {"sql": "SELECT id FROM users LIMIT 5"}}
                steps = [_run_attack_scenario_async(client, duration, attack)]
            results = await asyncio.gather(*steps)
        finally:
            stop.set()
            cpu, rss = await sampler
        del transport
        return results, cpu, rss

    return asyncio.run(_run())


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the loadtest CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("normal", "attack"), default="normal")
    parser.add_argument("--ramp", default="1,5,10,25,50", help="Comma-separated user counts for the normal scenario")
    parser.add_argument("--users", type=int, default=25, help="Concurrency for one-shot normal runs")
    parser.add_argument("--duration", type=float, default=15.0, help="Seconds per step")
    parser.add_argument("--seed-users", type=int, default=20000, help="Rows in the users fixture")
    parser.add_argument("--seed-transactions", type=int, default=200000, help="Rows in the transactions fixture")
    parser.add_argument("--seed", type=int, default=1, help="Random seed for deterministic mixes")
    parser.add_argument("--db", help="Override the temp database path (advanced)")
    parser.add_argument("--out", help="Write a JSON summary to this path")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested scenario; return the process exit code."""
    args = parse_args(argv)
    os.environ.setdefault("ENVIRONMENT", "dev")
    if args.db:
        db_path = Path(args.db)
    else:
        handle, raw_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        db_path = Path(raw_path)
    try:
        seed_database(db_path, args.seed_users, args.seed_transactions)
        os.environ["TENANT_DATABASES_JSON"] = json.dumps(
            {"org-acme": {"default": f"sqlite+aiosqlite:///{db_path}"}}
        )

        if args.scenario == "attack":
            steps, cpu, rss = run_scenario(
                scenario="attack",
                users=1,
                duration=args.duration,
                seeds=args.seed_users,
                connection_string="",
            )
            attack = steps[0]
            _print_step(attack)
            sys.stdout.write(
                f"attack: {attack.ok} ok, {attack.errors.get('rate_limited', 0)} rate-limited, "
                f"{attack.hard_error_rate * 100:.2f}% hard errors\n"
            )
            if attack.errors.get("rate_limited", 0) > 0 and attack.hard_error_rate <= MAX_HARD_ERROR_RATE:
                sys.stdout.write("rate limiting validated\n")
                verdict = 0
            else:
                sys.stdout.write("rate limiting NOT validated: no 429s observed or app unstable\n")
                verdict = 1
        else:
            counts = [int(value) for value in args.ramp.split(",") if value.strip()]
            if not counts:
                return 2
            steps = []
            last_cpu = last_rss = 0.0
            for users in counts:
                step, cpu, rss = run_scenario(
                    scenario="normal",
                    users=users,
                    duration=args.duration,
                    seeds=args.seed_users,
                )
                _print_step(step)
                steps.append(step)
                last_cpu, last_rss = cpu, rss
            _print_envelope(steps)
            verdict = 0 if any(step.within_budget() for step in steps) else 1

        if args.out:
            payload = _summary_to_dict(steps, last_cpu, last_rss)
            Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return verdict
    finally:
        if not args.db:
            db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())