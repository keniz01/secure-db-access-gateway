"""
Server-side resolution of logical tenant databases.

The GraphQL API only accepts an opaque logical database identifier.  Physical
connection details are loaded from server configuration and are never derived
from request data.

Supports two credential modes:
1. Static: connection_string from TENANT_DATABASES_JSON (legacy)
2. Rotated: credentials loaded from shared volume (written by rotate_creds.sh sidecar)
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from shared_secrets import read_secret

from auth import Principal


class TenantDatabaseResolutionError(PermissionError):
    """Raised when a tenant database cannot be resolved safely."""


@dataclass(frozen=True, slots=True)
class TenantDatabaseConfig:
    """A server-owned mapping between an organisation and a logical database."""

    org_id: str
    database_id: str
    # Credential mode: "static" or "rotated"
    credential_mode: str = "static"
    # Static mode fields
    connection_string: str = field(default="", repr=False)
    replica_connection_string: str | None = field(default=None, repr=False)
    use_read_replica: bool = False
    # Rotated mode fields
    role_name: str = ""
    creds_dir: str = "/creds"
    host: str = ""
    port: int = 5432
    db_name: str = ""
    # Common fields
    data_schema: str = "public"
    metadata_schema: str = "meta"
    # Per-tenant connection pool settings (optional; falls back to global env vars)
    pool_size: int | None = None
    max_overflow: int | None = None
    pool_timeout: float | None = None
    pool_recycle: int | None = None

    def __init__(
        self,
        org_id: str,
        database_id: str,
        connection_string: str | None = None,
        *,
        credential_mode: str = "static",
        replica_connection_string: str | None = None,
        use_read_replica: bool | None = None,
        role_name: str = "",
        creds_dir: str = "/creds",
        host: str = "",
        port: int = 5432,
        db_name: str = "",
        data_schema: str = "public",
        metadata_schema: str = "meta",
        pool_size: int | None = None,
        max_overflow: int | None = None,
        pool_timeout: float | None = None,
        pool_recycle: int | None = None,
    ) -> None:
        """Support both new keyword format and legacy positional (org_id, database_id, connection_string)."""
        # Auto-enable read replica when replica_connection_string provided (matches old behavior)
        if use_read_replica is None and replica_connection_string:
            use_read_replica = True
        elif use_read_replica is None:
            use_read_replica = False

        # Legacy positional format: TenantDatabaseConfig(org_id, database_id, connection_string)
        if connection_string is not None and "://" in connection_string:
            # Legacy format detected - treat as static mode with connection_string
            object.__setattr__(self, "org_id", org_id)
            object.__setattr__(self, "database_id", database_id)
            object.__setattr__(self, "credential_mode", "static")
            object.__setattr__(self, "connection_string", connection_string)
            object.__setattr__(self, "replica_connection_string", replica_connection_string)
            object.__setattr__(self, "use_read_replica", use_read_replica)
            object.__setattr__(self, "role_name", "")
            object.__setattr__(self, "creds_dir", creds_dir)
            object.__setattr__(self, "host", "")
            object.__setattr__(self, "port", 5432)
            object.__setattr__(self, "db_name", "")
            object.__setattr__(self, "data_schema", data_schema)
            object.__setattr__(self, "metadata_schema", metadata_schema)
            object.__setattr__(self, "pool_size", pool_size)
            object.__setattr__(self, "max_overflow", max_overflow)
            object.__setattr__(self, "pool_timeout", pool_timeout)
            object.__setattr__(self, "pool_recycle", pool_recycle)
            return

        # New keyword format
        object.__setattr__(self, "org_id", org_id)
        object.__setattr__(self, "database_id", database_id)
        object.__setattr__(self, "credential_mode", credential_mode)
        object.__setattr__(self, "connection_string", connection_string or "")
        object.__setattr__(self, "replica_connection_string", replica_connection_string)
        object.__setattr__(self, "use_read_replica", use_read_replica)
        object.__setattr__(self, "role_name", role_name)
        object.__setattr__(self, "creds_dir", creds_dir)
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "port", port)
        object.__setattr__(self, "db_name", db_name)
        object.__setattr__(self, "data_schema", data_schema)
        object.__setattr__(self, "metadata_schema", metadata_schema)
        object.__setattr__(self, "pool_size", pool_size)
        object.__setattr__(self, "max_overflow", max_overflow)
        object.__setattr__(self, "pool_timeout", pool_timeout)
        object.__setattr__(self, "pool_recycle", pool_recycle)

    def __post_init__(self) -> None:
        """Validate the server-owned database configuration invariants."""
        if not isinstance(self.org_id, str) or not self.org_id.strip():
            raise ValueError("org_id is required.")
        if not isinstance(self.database_id, str) or not self.database_id.strip():
            raise ValueError("database_id is required.")
        if not TenantDatabaseResolver.is_valid_database_id(self.database_id):
            raise ValueError("Tenant database identifiers must be opaque logical identifiers.")
        for schema_name in (self.data_schema, self.metadata_schema):
            if not schema_name.replace("_", "").isalnum():
                raise ValueError("Configured schema names must be valid identifiers.")

        if self.credential_mode == "static":
            if not self.connection_string.strip():
                raise ValueError("Static mode requires connection_string.")
        elif self.credential_mode == "rotated":
            if not self.role_name:
                raise ValueError("Rotated mode requires role_name.")
            if not self.host:
                raise ValueError("Rotated mode requires host.")
            if not self.db_name:
                raise ValueError("Rotated mode requires db_name.")
        else:
            raise ValueError(f"Unknown credential_mode: {self.credential_mode}")

    @property
    def effective_connection_string(self) -> str:
        """Return the active DB endpoint for read traffic."""
        if self.credential_mode == "static":
            if self.use_read_replica and self.replica_connection_string:
                return self.replica_connection_string
            return self.connection_string

        # Rotated mode: build connection string from rotated credentials
        from config.rotated_creds import get_credential_loader
        loader = get_credential_loader()
        creds = loader.get_credentials(self.org_id, self.role_name)
        return (
            f"postgresql+asyncpg://{creds.username}:{creds.password}"
            f"@{creds.host}:{creds.port}/{creds.database}"
        )

    @property
    def effective_target(self) -> str:
        """Return the DB target used for the request for auditing."""
        if self.credential_mode == "static":
            if self.use_read_replica and self.replica_connection_string:
                return "replica"
            return "primary"
        return "rotated"


class TenantDatabaseResolver:
    """
    Resolve ``(validated principal organisation, logical database id)``.

    Configuration is intentionally one-way: request data can select a logical
    identifier, but it can never provide or alter a connection string.
    """

    _DATABASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    def __init__(
        self,
        bindings: Iterable[TenantDatabaseConfig] = (),
    ) -> None:
        self._bindings: dict[tuple[str, str], list[TenantDatabaseConfig]] = {}
        for binding in bindings:
            key = (binding.org_id, binding.database_id)
            self._bindings.setdefault(key, []).append(binding)

        self._has_explicit_configuration = bool(self._bindings)

    @classmethod
    def is_valid_database_id(cls, database_id: str) -> bool:
        """Return whether the value is a valid opaque logical database identifier."""
        return isinstance(database_id, str) and bool(cls._DATABASE_ID_PATTERN.fullmatch(database_id.strip()))

    @classmethod
    def from_environment(cls) -> TenantDatabaseResolver:
        """Load tenant mappings from JSON in an environment variable or secret file."""
        raw_config = os.getenv("TENANT_DATABASES_JSON", "").strip()
        if not raw_config:
            raw_config = read_secret(
                "TENANT_DATABASES_JSON",
                required=True,
                error_message=(
                    "Tenant database configuration is required via TENANT_DATABASES_JSON "
                    "or TENANT_DATABASES_JSON_FILE."
                ),
            )

        if not raw_config:
            raise RuntimeError(
                "Tenant database configuration is required via TENANT_DATABASES_JSON "
                "or TENANT_DATABASES_JSON_FILE."
            )
        try:
            parsed = json.loads(raw_config)
            bindings = cls._parse_bindings(parsed)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Tenant database configuration is invalid.") from exc
        if not bindings:
            raise RuntimeError("Tenant database configuration must contain at least one mapping.")
        return cls(bindings)

    @classmethod
    def _parse_bindings(cls, parsed: object) -> list[TenantDatabaseConfig]:
        entries: list[dict[str, Any]] = []
        if isinstance(parsed, list):
            entries = [entry for entry in parsed if isinstance(entry, dict)]
            if len(entries) != len(parsed):
                raise ValueError("Tenant database entries must be objects.")
        elif isinstance(parsed, dict):
            # Support {"org": {"database": "connection"}} and a list-style
            # {"org_id": ..., "database_id": ..., "connection_string": ...}.
            if {"org_id", "database_id"} <= parsed.keys():
                entries = [parsed]
            else:
                for org_id, databases in parsed.items():
                    if not isinstance(databases, dict):
                        raise ValueError("Tenant database mappings must be nested objects.")
                    for database_id, value in databases.items():
                        if isinstance(value, str):
                            # Legacy static format
                            entries.append({
                                "org_id": org_id,
                                "database_id": database_id,
                                "connection_string": value,
                                "credential_mode": "static",
                            })
                        elif isinstance(value, dict):
                            entry = {**value, "org_id": org_id, "database_id": database_id}
                            # Default to static if connection_string present, rotated if role_name present
                            if "credential_mode" not in entry:
                                if "connection_string" in entry:
                                    entry["credential_mode"] = "static"
                                elif "role_name" in entry:
                                    entry["credential_mode"] = "rotated"
                                else:
                                    entry["credential_mode"] = "static"
                            entries.append(entry)
                        else:
                            raise ValueError("Tenant database connection configuration is invalid.")
        else:
            raise ValueError("Tenant database configuration must be a JSON object or array.")

        result: list[TenantDatabaseConfig] = []
        for entry in entries:
            credential_mode = entry.get("credential_mode", "static")
            
            if credential_mode == "static":
                connection_string = (
                    entry.get("connection_string")
                    or entry.get("database_url")
                    or entry.get("url")
                )
                replica_connection_string = (
                    entry.get("replica_connection_string")
                    or entry.get("read_replica_connection_string")
                    or entry.get("replica_url")
                    or entry.get("read_replica_url")
                )
                if isinstance(entry.get("replica"), dict):
                    replica_connection_string = (
                        replica_connection_string
                        or entry["replica"].get("connection_string")
                        or entry["replica"].get("database_url")
                        or entry["replica"].get("url")
                    )
                if isinstance(entry.get("read_replica"), dict):
                    replica_connection_string = (
                        replica_connection_string
                        or entry["read_replica"].get("connection_string")
                        or entry["read_replica"].get("database_url")
                        or entry["read_replica"].get("url")
                    )
                use_read_replica = entry.get("use_read_replica")
                if use_read_replica is None and replica_connection_string:
                    use_read_replica = True
                
                pool_size = entry.get("pool_size")
                if pool_size is not None:
                    pool_size = int(pool_size)
                max_overflow = entry.get("max_overflow")
                if max_overflow is not None:
                    max_overflow = int(max_overflow)
                pool_timeout = entry.get("pool_timeout")
                if pool_timeout is not None:
                    pool_timeout = float(pool_timeout)
                pool_recycle = entry.get("pool_recycle")
                if pool_recycle is not None:
                    pool_recycle = int(pool_recycle)
                
                if not all(isinstance(entry.get(key), str) for key in ("org_id", "database_id")):
                    raise ValueError("Tenant database entries require org_id and database_id.")
                if not isinstance(connection_string, str) or not connection_string.strip():
                    raise ValueError("Static mode entries require connection_string.")
                
                result.append(
                    TenantDatabaseConfig(
                        org_id=entry["org_id"].strip(),
                        database_id=entry["database_id"].strip(),
                        credential_mode="static",
                        connection_string=connection_string.strip(),
                        data_schema=str(entry.get("data_schema", os.getenv("SQL_DATA_SCHEMA", "public"))),
                        metadata_schema=str(entry.get("metadata_schema", os.getenv("SQL_METADATA_SCHEMA", "meta"))),
                        replica_connection_string=(
                            replica_connection_string.strip()
                            if isinstance(replica_connection_string, str) and replica_connection_string.strip()
                            else None
                        ),
                        use_read_replica=bool(use_read_replica),
                        pool_size=pool_size,
                        max_overflow=max_overflow,
                        pool_timeout=pool_timeout,
                        pool_recycle=pool_recycle,
                    )
                )
            
            elif credential_mode == "rotated":
                role_name = entry.get("role_name")
                host = entry.get("host")
                port = entry.get("port", 5432)
                db_name = entry.get("db_name")
                creds_dir = entry.get("creds_dir", "/creds")
                
                pool_size = entry.get("pool_size")
                if pool_size is not None:
                    pool_size = int(pool_size)
                max_overflow = entry.get("max_overflow")
                if max_overflow is not None:
                    max_overflow = int(max_overflow)
                pool_timeout = entry.get("pool_timeout")
                if pool_timeout is not None:
                    pool_timeout = float(pool_timeout)
                pool_recycle = entry.get("pool_recycle")
                if pool_recycle is not None:
                    pool_recycle = int(pool_recycle)
                
                if not all(isinstance(entry.get(key), str) for key in ("org_id", "database_id")):
                    raise ValueError("Tenant database entries require org_id and database_id.")
                if not role_name:
                    raise ValueError("Rotated mode entries require role_name.")
                if not host:
                    raise ValueError("Rotated mode entries require host.")
                if not db_name:
                    raise ValueError("Rotated mode entries require db_name.")
                
                result.append(
                    TenantDatabaseConfig(
                        org_id=entry["org_id"].strip(),
                        database_id=entry["database_id"].strip(),
                        credential_mode="rotated",
                        role_name=role_name.strip(),
                        host=host.strip(),
                        port=int(port),
                        db_name=db_name.strip(),
                        creds_dir=creds_dir,
                        data_schema=str(entry.get("data_schema", os.getenv("SQL_DATA_SCHEMA", "public"))),
                        metadata_schema=str(entry.get("metadata_schema", os.getenv("SQL_METADATA_SCHEMA", "meta"))),
                        pool_size=pool_size,
                        max_overflow=max_overflow,
                        pool_timeout=pool_timeout,
                        pool_recycle=pool_recycle,
                    )
                )
            
            else:
                raise ValueError(f"Unknown credential_mode: {credential_mode}")
        return result

    def resolve(self, principal: Principal, database_id: str | None) -> TenantDatabaseConfig:
        """Resolve a logical database for the trusted principal or fail closed."""
        if not isinstance(principal, Principal) or not principal.org_id.strip():
            raise TenantDatabaseResolutionError("Authenticated organisation is required.")
        if not self.is_valid_database_id(database_id or ""):
            raise TenantDatabaseResolutionError("A valid logical database_id is required.")

        normalized_database_id = database_id.strip()
        matches = self._bindings.get((principal.org_id, normalized_database_id), [])
        if len(matches) > 1:
            raise TenantDatabaseResolutionError("Database configuration is ambiguous.")
        if len(matches) == 1:
            return matches[0]

        # Do not distinguish an unknown database from a database owned by
        # another organisation.
        raise TenantDatabaseResolutionError("Database is not available for this organisation.")
