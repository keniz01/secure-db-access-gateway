#!/usr/bin/env python3
"""Host-side tenant-database backup for the gateway (issue #143 / M18).

Reads the same ``TENANT_DATABASES_JSON`` / ``TENANT_DATABASES_JSON_FILE``
configuration the gateway uses, then creates a verified, rotating ``pg_dump``
custom-format backup for every unique tenant database. Runs on the host (not
inside a container) so it can use the host ``pg_dump``/``pg_restore`` tools.

Guarantees:

- **Idempotent**: a new timestamped dump per database per run; old dumps beyond
  the retention window are deleted (per-database, never a shared glob).
- **Verified**: every dump is parsed with ``pg_restore --list`` before it is
  accepted; an unreadable or empty dump fails the run.
- **Read-only**: only ``pg_dump`` SELECTs run on the source; the gateway's DB
  accounts need no write privileges and no write is ever issued.
- **Non-destructive**: ``--no-owner --no-privileges`` so a restore does not
  depend on a specific role existing on the target cluster.
- **SQLite-safe**: SQLite/dev targets are skipped with a warning (the gateway
  forces ``mode=ro`` on them; they are not production data sources).

Off-host copies are the operator's obligation — see ``BACKUP_DR.md``. This
script can run an optional ``BACKUP_OFFLOAD_CMD`` (e.g. an ``rsync``) after a
successful run.

Usage:
    TENANT_DATABASES_JSON='{"org": {"db": "postgresql://..."}}' \
        scripts/backup-databases.py [--retention 14] [--backup-dir ...] \
        [--include-replicas] [--dry-run]

    # or use the deployed secrets file:
    scripts/backup-databases.py --env-file "$GATEWAY_ENV_FILE" --dry-run

Exit codes: 0 = all configured targets dumped and verified; 1 = any failure;
2 = usage/config error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REDACT_MARKER = "****"


class BackupConfigError(RuntimeError):
    """Raised when tenant/connection config cannot be interpreted."""


def load_tenant_config(gateway_env_file: str | None) -> dict[str, dict[str, object]]:
    """Load ``TENANT_DATABASES_JSON`` (env, then secrets file) like the resolver.

    Mirrors ``TenantDatabaseResolver.from_environment``: the direct env var wins,
    otherwise ``*_FILE``, otherwise an optional ``--env-file`` secret file gets
    read for a literal ``TENANT_DATABASES_JSON``/``_FILE`` line.
    """
    raw_json = os.getenv("TENANT_DATABASES_JSON", "").strip()
    raw_file = os.getenv("TENANT_DATABASES_JSON_FILE", "").strip()

    if not raw_json and not raw_file and gateway_env_file:
        source = Path(gateway_env_file).expanduser()
        if source.is_file():
            for line in source.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if "=" not in line or line.startswith("#"):
                    continue
                key, value = line.split("=", 1)
                value = value.strip().strip('"').strip("'")
                if key == "TENANT_DATABASES_JSON" and not raw_json:
                    raw_json = value
                elif key == "TENANT_DATABASES_JSON_FILE" and not raw_file:
                    raw_file = value

    if not raw_json and not raw_file:
        raise BackupConfigError(
            "no tenant database config: set TENANT_DATABASES_JSON (or *_FILE, or --env-file)"
        )
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise BackupConfigError(f"TENANT_DATABASES_JSON is not valid JSON: {exc}") from exc
    else:
        config_path = Path(raw_file).expanduser()
        if not config_path.is_file():
            raise BackupConfigError(f"TENANT_DATABASES_JSON_FILE does not exist: {raw_file}")
        try:
            parsed = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BackupConfigError(f"TENANT_DATABASES_JSON_FILE is not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise BackupConfigError("tenant database config must be a JSON object keyed by org_id")
    return parsed


def target_urls(config: dict[str, dict[str, object]], *, include_replicas: bool) -> list[tuple[str, str]]:
    """Flatten the config into ``(label, connection_string)`` pairs.

    Label is ``org_id/database_id`` (plus `` (replica)``), matching the probe.
    The effective (primary-or-replica) connection is used when a replica is
    configured and enabled, mirroring ``TenantDatabaseConfig.effective_connection_string``.
    """
    targets: list[tuple[str, str]] = []
    for org_id in sorted(config):
        bindings = config[org_id]
        if not isinstance(bindings, dict):
            raise BackupConfigError(f"tenant {org_id!r}: bindings must be a JSON object")
        for database_id in sorted(bindings):
            entry = bindings[database_id]
            label = f"{org_id}/{database_id}"
            if isinstance(entry, str):
                targets.append((label, entry))
                continue
            if not isinstance(entry, dict):
                raise BackupConfigError(f"tenant {label!r}: entry must be a string or object")
            primary = str(entry.get("connection_string") or entry.get("primary") or "").strip()
            replica = str(
                entry.get("replica_connection_string")
                or entry.get("read_replica_connection_string")
                or (entry.get("replica") or {}).get("connection_string", "")
            ).strip()
            if not primary:
                raise BackupConfigError(f"tenant {label!r}: missing connection_string")
            if replica and include_replicas:
                targets.append((f"{label} (replica)", replica))
            targets.append((label, primary))
    return targets


def portable_pg_url(connection_string: str) -> str:
    """Return a libpq-parseable URL for ``pg_dump``/``psql``.

    Strips SQLAlchemy ``+driver`` suffixes (``postgresql+asyncpg://`` →
    ``postgresql://``); non-Postgres schemes are left untouched so callers can
    reject them explicitly.
    """
    for suffix in ("+asyncpg", "+psycopg", "+psycopg2", "+pg8000"):
        marker = f"postgresql{suffix}://"
        if connection_string.startswith(marker):
            return connection_string.replace(marker, "postgresql://", 1)
        marker = f"postgres{suffix}://"
        if connection_string.startswith(marker):
            return connection_string.replace(marker, "postgres://", 1)
    return connection_string


def redact(connection_string: str) -> str:
    """Replace the userinfo password with ``****`` for safe logging."""
    try:
        scheme, _, rest = connection_string.partition("://")
        userinfo, _, _remainder = rest.partition("@")
        if "@" in connection_string and ":" in userinfo:
            user, _pass = userinfo.split(":", 1)
            return f"{scheme}://{user}:{REDACT_MARKER}@{_remainder}"
    except ValueError:
        pass
    return connection_string


def backup_one(
    connection_string: str,
    label: str,
    backup_dir: Path,
    timestamp: str,
    pg_dump: str = "pg_dump",
    pg_restore: str = "pg_restore",
) -> Path:
    """Dump one database to a verified custom-format backup file."""
    url = portable_pg_url(connection_string)
    if not url.startswith(("postgres://", "postgresql://")):
        raise BackupConfigError(f"{label}: only PostgreSQL targets are backed up here ({url})")

    safe_label = label.replace("/", "__").replace(" ", "_").replace("(replica)", "replica")
    dump_path = backup_dir / f"{safe_label}__{timestamp}.dump"

    dump_cmd = [pg_dump, "--format=custom", "--no-owner", "--no-privileges", "--file", str(dump_path), "-d", url]
    result = subprocess.run(dump_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        dump_path.unlink(missing_ok=True)
        raise RuntimeError(f"{label}: pg_dump failed: {result.stderr.strip() or result.stdout.strip()}")

    if dump_path.stat().st_size == 0:
        dump_path.unlink(missing_ok=True)
        raise RuntimeError(f"{label}: pg_dump produced an empty file")

    verify = subprocess.run([pg_restore, "--list", str(dump_path)], capture_output=True, text=True)
    if verify.returncode != 0:
        dump_path.unlink(missing_ok=True)
        raise RuntimeError(f"{label}: pg_restore --list failed: {verify.stderr.strip()}")
    return dump_path


def enforce_retention(backup_dir: Path, label: str, keep: int) -> None:
    """Delete the oldest dumps per database beyond the retention window."""
    if keep < 1:
        return
    prefix = label.replace("/", "__").replace(" ", "_").replace("(replica)", "replica")
    dumps = sorted(backup_dir.glob(f"{prefix}__*.dump"))
    for stale in dumps[:-keep] if len(dumps) > keep else []:
        stale.unlink()


def main(argv: list[str] | None = None) -> int:
    """Run the backup pipeline; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=os.getenv("GATEWAY_ENV_FILE"), help="Path to the gateway secrets file")
    parser.add_argument("--backup-dir", default=os.getenv("BACKUP_DIR", "backups"))
    parser.add_argument("--retention", type=int, default=int(os.getenv("BACKUP_RETENTION", "14")))
    parser.add_argument("--include-replicas", action="store_true", help="Also dump read replicas")
    parser.add_argument("--dry-run", action="store_true", help="Only resolve and print the target plan")
    parser.add_argument("--pg-dump", default="pg_dump")
    parser.add_argument("--pg-restore", default="pg_restore")
    args = parser.parse_args(argv)

    backup_dir = Path(args.backup_dir).expanduser()
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    offload_cmd = os.getenv("BACKUP_OFFLOAD_CMD", "").strip()

    try:
        config = load_tenant_config(args.env_file)
        targets = target_urls(config, include_replicas=args.include_replicas)
        if not targets:
            raise BackupConfigError("tenant config resolved to zero backup targets")
    except BackupConfigError as exc:
        print(f"backup config error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        for label, url in targets:
            print(f"[PLAN] {label}: {redact(portable_pg_url(url))}")
        print(f"[DRY-RUN] {len(targets)} target(s); would write to {backup_dir} at {timestamp}")
        return 0

    backup_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    failed = False

    for label, url in targets:
        try:
            path = backup_one(url, label, backup_dir, timestamp, args.pg_dump, args.pg_restore)
            created.append(path)
            enforce_retention(backup_dir, label, args.retention)
            print(f"[OK]   {label}: {path.name}")
        except (RuntimeError, BackupConfigError) as exc:
            failed = True
            print(f"[FAIL] {label}: {exc}", file=sys.stderr)

    if failed:
        print(f"backup failed: {len(created)} of {len(targets)} databases dumped", file=sys.stderr)
        return 1

    if offload_cmd:
        result = subprocess.run(
            f"{offload_cmd} '{backup_dir}/'", shell=True, capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"backup offload command failed: {result.stderr.strip()}", file=sys.stderr)
            return 1
        print(f"[OFFLOAD] {offload_cmd.split()[0]} succeeded")

    print(f"backup ok: {len(created)} database(s) dumped and verified in {backup_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())