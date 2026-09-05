"""Server-side resolution of logical tenant databases.

The GraphQL API only accepts an opaque logical database identifier.  Physical
connection details are loaded from server configuration and are never derived
from request data.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from auth import Principal


class TenantDatabaseResolutionError(PermissionError):
    """Raised when a tenant database cannot be resolved safely."""


@dataclass(frozen=True, slots=True)
class TenantDatabaseConfig:
    """A server-owned mapping between an organisation and a logical database."""

    org_id: str
    database_id: str
    connection_string: str = field(repr=False)
    data_schema: str = "public"
    metadata_schema: str = "meta"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.org_id, str)
            or not isinstance(self.database_id, str)
            or not isinstance(self.connection_string, str)
            or not self.org_id.strip()
            or not self.connection_string.strip()
        ):
            raise ValueError("Tenant database configuration is incomplete.")
        if not TenantDatabaseResolver.is_valid_database_id(self.database_id):
            raise ValueError("Tenant database identifiers must be opaque logical identifiers.")
        for schema_name in (self.data_schema, self.metadata_schema):
            if not schema_name.replace("_", "").isalnum():
                raise ValueError("Configured schema names must be valid identifiers.")


class TenantDatabaseResolver:
    """Resolve ``(validated principal organisation, logical database id)``.

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
        return isinstance(database_id, str) and bool(cls._DATABASE_ID_PATTERN.fullmatch(database_id.strip()))

    @classmethod
    def from_environment(cls) -> "TenantDatabaseResolver":
        """Load tenant mappings from JSON in an environment variable or secret file."""
        raw_config = os.getenv("TENANT_DATABASES_JSON", "").strip()
        config_file = os.getenv("TENANT_DATABASES_FILE", "").strip()
        if not raw_config and config_file:
            try:
                with open(config_file, "r", encoding="utf-8") as handle:
                    raw_config = handle.read().strip()
            except FileNotFoundError:
                raw_config = ""

        if not raw_config:
            raise RuntimeError(
                "Tenant database configuration is required via TENANT_DATABASES_JSON "
                "or TENANT_DATABASES_FILE."
            )
        try:
            parsed = json.loads(raw_config)
            bindings = cls._parse_bindings(parsed)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Tenant database configuration is invalid.") from exc
        if not bindings:
            raise RuntimeError("Tenant database configuration must contain at least one mapping.")
        return cls(bindings)

    @staticmethod
    def _read_secret(file_path: str) -> str:
        if not file_path:
            return ""
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                return handle.read().strip()
        except FileNotFoundError:
            return ""

    @classmethod
    def _parse_bindings(cls, parsed: Any) -> list[TenantDatabaseConfig]:
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
                            entries.append({
                                "org_id": org_id,
                                "database_id": database_id,
                                "connection_string": value,
                            })
                        elif isinstance(value, dict):
                            entries.append({
                                **value,
                                "org_id": org_id,
                                "database_id": database_id,
                            })
                        else:
                            raise ValueError("Tenant database connection configuration is invalid.")
        else:
            raise ValueError("Tenant database configuration must be a JSON object or array.")

        result: list[TenantDatabaseConfig] = []
        for entry in entries:
            connection_string = (
                entry.get("connection_string")
                or entry.get("database_url")
                or entry.get("url")
            )
            if not all(isinstance(entry.get(key), str) for key in ("org_id", "database_id")):
                raise ValueError("Tenant database entries require org_id and database_id.")
            if not isinstance(connection_string, str) or not connection_string.strip():
                raise ValueError("Tenant database entries require server-side connection configuration.")
            result.append(
                TenantDatabaseConfig(
                    org_id=entry["org_id"].strip(),
                    database_id=entry["database_id"].strip(),
                    connection_string=connection_string.strip(),
                    data_schema=str(entry.get("data_schema", os.getenv("SQL_DATA_SCHEMA", "public"))),
                    metadata_schema=str(entry.get("metadata_schema", os.getenv("SQL_METADATA_SCHEMA", "meta"))),
                )
            )
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
