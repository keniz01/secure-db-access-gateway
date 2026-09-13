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
#   2. Generates a TLS certificate signed by the mkcert local CA into certs/ so
#      browsers trust https://localhost:8443 with no security warning.
#      Requires mkcert (brew install mkcert). Production: replace with a
#      trusted CA cert / ACME.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${GATEWAY_ENV_FILE:-.env}"
if [ ! -f "$ENV_FILE" ]; then
  cp .env.example "$ENV_FILE"
  echo "Created $ENV_FILE from .env.example"
  echo "Edit it and fill in real values:"
  echo "  open $ENV_FILE"
else
  echo "Kept  $ENV_FILE (does not overwrite existing values)"
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
echo "2. Run: docker compose up --build"
echo ""
echo "Production: copy $ENV_FILE to the host as /etc/gateway/gateway.env"
echo "  (chmod 600), keep the source of truth in a password manager, and run:"
echo "  docker compose --env-file /etc/gateway/gateway.env up -d"