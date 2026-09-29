#!/usr/bin/env python3
"""
Validate tenant database configuration.

Checks:
- JSON schema validity
- No duplicate (org_id, database_id) pairs
- Environment separation (no dev/prod mixing in same config)
- Required fields present
- Connection string format validation
"""

import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str]
    warnings: list[str]


# Regex for valid database_id (opaque logical identifier)
DATABASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

# Regex for valid schema names
SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Valid credential modes
VALID_CREDENTIAL_MODES = {"static", "rotated"}

# Required fields per credential mode
STATIC_REQUIRED = {"connection_string"}
ROTATED_REQUIRED = {"role_name", "host", "db_name"}


def validate_database_id(database_id: str) -> list[str]:
    errors = []
    if not DATABASE_ID_PATTERN.fullmatch(database_id):
        errors.append(
            f"Invalid database_id '{database_id}': must match ^[A-Za-z0-9][A-Za-z0-9_.-]{{0,127}}$"
        )
    return errors


def validate_schema_name(schema_name: str, field_name: str) -> list[str]:
    errors = []
    if not SCHEMA_PATTERN.fullmatch(schema_name):
        errors.append(
            f"Invalid {field_name} '{schema_name}': must be a valid identifier (alphanumeric + underscore)"
        )
    return errors


def validate_connection_string(conn_str: str) -> list[str]:
    errors = []
    if not conn_str:
        errors.append("connection_string is empty")
        return errors

    # Basic format check
    if "://" not in conn_str:
        errors.append(f"connection_string missing protocol (e.g., postgresql+asyncpg://): {conn_str[:50]}")
    return errors


def validate_rotated_config(config: dict[str, Any], prefix: str) -> list[str]:
    errors = []
    for field in ROTATED_REQUIRED:
        if not config.get(field):
            errors.append(f"{prefix}: rotated mode requires '{field}'")
    return errors


def validate_static_config(config: dict[str, Any], prefix: str) -> list[str]:
    errors = []
    for field in STATIC_REQUIRED:
        if not config.get(field):
            errors.append(f"{prefix}: static mode requires '{field}'")
        else:
            errors.extend(validate_connection_string(config[field]))
    return errors


def validate_tenant_config(config: dict[str, Any]) -> ValidationResult:
    errors = []
    warnings = []

    if not isinstance(config, dict):
        return ValidationResult(False, ["Config must be a JSON object"], [])

    if "tenants" not in config:
        return ValidationResult(False, ["Missing 'tenants' key at root"], [])

    tenants = config.get("tenants", {})
    if not isinstance(tenants, dict):
        return ValidationResult(False, ["'tenants' must be an object"], [])

    if not tenants:
        return ValidationResult(False, ["At least one tenant is required"], [])

    # Track duplicates across all tenants
    seen_pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    environments: set[str] = set()

    for tenant_id, tenant_data in tenants.items():
        if not isinstance(tenant_data, dict):
            errors.append(f"Tenant '{tenant_id}': must be an object")
            continue

        if "databases" not in tenant_data:
            errors.append(f"Tenant '{tenant_id}': missing 'databases' key")
            continue

        databases = tenant_data.get("databases", {})
        if not isinstance(databases, dict):
            errors.append(f"Tenant '{tenant_id}': 'databases' must be an object")
            continue

        if not databases:
            errors.append(f"Tenant '{tenant_id}': at least one database is required")
            continue

        for database_id, db_config in databases.items():
            if not isinstance(db_config, dict):
                errors.append(f"Tenant '{tenant_id}', database '{database_id}': must be an object")
                continue

            prefix = f"Tenant '{tenant_id}', database '{database_id}'"

            # Validate database_id
            errors.extend(validate_database_id(database_id))

            # Check for duplicates
            key = (tenant_id, database_id)
            seen_pairs[key].append(prefix)

            # Validate credential_mode
            credential_mode = db_config.get("credential_mode", "static")
            if credential_mode not in VALID_CREDENTIAL_MODES:
                errors.append(f"{prefix}: invalid credential_mode '{credential_mode}' (must be 'static' or 'rotated')")
                continue

            # Validate required fields per mode
            if credential_mode == "static":
                errors.extend(validate_static_config(db_config, prefix))
            else:
                errors.extend(validate_rotated_config(db_config, prefix))

            # Validate schemas
            data_schema = db_config.get("data_schema", "public")
            metadata_schema = db_config.get("metadata_schema", "meta")
            errors.extend(validate_schema_name(data_schema, f"{prefix} data_schema"))
            errors.extend(validate_schema_name(metadata_schema, f"{prefix} metadata_schema"))

            # Track environment (from host or connection string)
            if credential_mode == "rotated":
                host = db_config.get("host", "")
                if "prod" in host.lower():
                    environments.add("prod")
                elif "dev" in host.lower() or "staging" in host.lower() or "test" in host.lower():
                    environments.add("dev")
            else:
                conn_str = db_config.get("connection_string", "")
                if "prod" in conn_str.lower():
                    environments.add("prod")
                elif "dev" in conn_str.lower() or "staging" in conn_str.lower() or "test" in conn_str.lower():
                    environments.add("dev")

            # Pool settings validation
            for pool_field in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle"):
                value = db_config.get(pool_field)
                if value is not None:
                    try:
                        if pool_field in ("pool_size", "max_overflow", "pool_recycle"):
                            int(value)
                        else:
                            float(value)
                    except (ValueError, TypeError):
                        errors.append(f"{prefix}: '{pool_field}' must be a number")

    # Check for duplicates
    for pair, locations in seen_pairs.items():
        if len(locations) > 1:
            errors.append(f"Duplicate (org_id, database_id) pair: {pair} found in {', '.join(locations)}")

    # Environment separation warning
    if len(environments) > 1:
        warnings.append(f"Mixed environments detected in config: {', '.join(sorted(environments))}")

    return ValidationResult(len(errors) == 0, errors, warnings)


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: validate_tenant_config.py <config_file>", file=sys.stderr)
        return 1

    config_path = sys.argv[1]

    if not os.path.exists(config_path):
        print(f"Config file not found: {config_path}", file=sys.stderr)
        return 1

    try:
        with open(config_path) as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        return 1

    result = validate_tenant_config(config)

    if result.warnings:
        print("WARNINGS:")
        for w in result.warnings:
            print(f"  - {w}")
        print()

    if result.errors:
        print("ERRORS:")
        for e in result.errors:
            print(f"  - {e}")
        return 1

    print("Configuration is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())