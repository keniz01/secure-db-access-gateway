#!/bin/bash
set -euo pipefail

# Local development bootstrap for env-based secrets (approach A).
#
# Secrets are never stored in this repository. They live in a single
# gitignored env file (.env locally, /etc/gateway/gateway.env on a host) that
# is COPY-created from .env.example and filled in manually.
#
# This script:
#   1. Creates .env from .env.example if it does not exist (never overwrites).
#   2. Creates the PostgreSQL rotator secret if it is missing and derives an
#      ignored local tenant config from TENANT_DATABASES_JSON.
#   3. Generates a TLS certificate signed by the mkcert local CA into certs/ so
#      browsers trust https://localhost:8443 with no security warning.
#      Requires mkcert (brew install mkcert). Production: replace with a
#      trusted CA cert / ACME.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${GATEWAY_ENV_FILE:-.env}"
if [ ! -f "$ENV_FILE" ]; then
  cp .env.example "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE from .env.example"
  echo "Edit it and fill in real values:"
  echo "  open $ENV_FILE"
else
  echo "Kept  $ENV_FILE (does not overwrite existing values)"
fi

if [ "$ENV_FILE" = ".env" ]; then
  mkdir -p secrets
  chmod 700 secrets

  python3 - <<'PY'
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

env_path = Path(".env")
lines = env_path.read_text().splitlines()
values = {}
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip()

def env_value(name, default=""):
    raw = values.get(name, default)
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        return raw[1:-1]
    return raw

secret_path = Path(env_value(
    "PG_ROTATOR_ADMIN_PASS_FILE", "./secrets/pg_rotator_admin_pass.txt"
))
if not secret_path.is_absolute():
    secret_path = Path.cwd() / secret_path
secret_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
if not secret_path.exists():
    generated_password = subprocess.run(
        ["openssl", "rand", "-hex", "32"],
        check=True, capture_output=True, text=True,
    ).stdout
    try:
        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as secret_file:
            secret_file.write(generated_password)
            secret_file.write("\n")
        print(f"Created rotator admin secret at {secret_path}")
secret_path.chmod(0o600)

try:
    tenant_mapping = env_value("TENANT_DATABASES_JSON")
    if not tenant_mapping:
        mapping_path = Path(env_value("TENANT_DATABASES_JSON_FILE"))
        if not mapping_path.is_absolute():
            mapping_path = Path.cwd() / mapping_path
        tenant_mapping = mapping_path.read_text()
    tenant_entries = json.loads(tenant_mapping)
except json.JSONDecodeError as exc:
    raise SystemExit(f"TENANT_DATABASES_JSON in .env is invalid JSON: {exc}") from exc
except OSError as exc:
    raise SystemExit(f"Could not read TENANT_DATABASES_JSON[_FILE]: {exc}") from exc
if not isinstance(tenant_entries, list) or not tenant_entries:
    raise SystemExit("TENANT_DATABASES_JSON in .env must be a non-empty array")

config = {
    "version": 1,
    "tenants": {},
    "rotation": {
        "admin_user": "rotator_admin",
        "admin_password_file": "/run/secrets/pg_rotator_admin_pass",
    },
}
for entry in tenant_entries:
    try:
        org_id = entry["org_id"]
        database_id = entry["database_id"]
        source_url = urlsplit(entry["connection_string"])
        db_name = source_url.path.lstrip("/")
    except (KeyError, TypeError) as exc:
        raise SystemExit(
            "Each TENANT_DATABASES_JSON entry needs org_id, database_id, "
            "and connection_string"
        ) from exc
    if not org_id or not database_id or not db_name:
        raise SystemExit("Tenant org_id, database_id and database name must be non-empty")
    if source_url.scheme not in ("postgres", "postgresql", "postgresql+asyncpg"):
        raise SystemExit(f"Tenant {org_id}/{database_id} is not a PostgreSQL connection")
    config["tenants"].setdefault(org_id, {"databases": {}})["databases"][database_id] = {
        "credential_mode": "rotated",
        "role_name": "analytics",
        "host": "postgres",
        "port": 5432,
        "db_name": db_name,
        "data_schema": entry.get("data_schema", "music"),
        "metadata_schema": entry.get("metadata_schema", "meta"),
        "rotation": {"enabled": True, "interval_seconds": 86400},
    }

local_config = Path("secrets/tenant_databases.local.json")
temporary_config = local_config.with_suffix(".json.tmp")
temporary_config.write_text(json.dumps(config, indent=2) + "\n")
temporary_config.chmod(0o600)
os.replace(temporary_config, local_config)

source_var = "TENANT_DATABASES_CONFIG_SOURCE="
source_value = "./secrets/tenant_databases.local.json"
found_source = False
updated_lines = []
for line in lines:
    if line.startswith(source_var):
        found_source = True
        current_source = line.split("=", 1)[1]
        if current_source in ("", "./config/tenant_databases.json", source_value):
            line = source_var + source_value
    updated_lines.append(line)
if not found_source:
    updated_lines.append(source_var + source_value)
env_path.write_text("\n".join(updated_lines) + "\n")
env_path.chmod(0o600)
print(f"Generated ignored local tenant config at {local_config}")
PY
fi

if ! command -v mkcert >/dev/null 2>&1; then
  echo "ERROR: mkcert is required to generate trusted dev TLS certificates."
  echo "Install it with:   brew install mkcert"
  exit 1
fi

mkdir -p certs

# Install the mkcert local CA into the OS trust store once (prompts for the
# macOS admin password the first time). Browsers then trust every cert mkcert
# signs, including the one generated below.
if ! mkcert -install >/dev/null 2>&1; then
  echo "ERROR: could not install the mkcert CA automatically."
  echo "Run 'mkcert -install' (follow the password prompt), then re-run this script."
  exit 1
fi

# (Re)generate the leaf cert for localhost + 127.0.0.1 + Docker service names.
mkcert -cert-file certs/web_tls_cert.pem -key-file certs/web_tls_key.pem \
  localhost 127.0.0.1 web-app auth0_api sql_query_api >/dev/null
chmod 600 certs/web_tls_key.pem
echo "Generated mkcert-signed TLS certificate (localhost) into certs/"

# Reload the running nginx edge so it serves the new certificate.
if docker compose ps nginx >/dev/null 2>&1; then
  docker compose exec -T nginx nginx -s reload >/dev/null 2>&1 \
    && echo "Reloaded nginx to pick up the new certificate."
fi

echo ""
echo "Next steps:"
echo "1. Fill in real values in $ENV_FILE (see the comments in .env.example)."
echo "2. Re-run this script after editing tenant mappings to regenerate the local config."
echo "3. Run: docker compose up --build"
echo ""
echo "Production: copy $ENV_FILE to the host as /etc/gateway/gateway.env"
echo "  (chmod 600), keep the source of truth in a password manager, and run:"
echo "  docker compose --env-file /etc/gateway/gateway.env up -d"
