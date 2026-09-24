#!/bin/bash
# Build an OPA bundle tarball from sql_query_api/opa/ for hot-reload.
# Requires: opa CLI (brew install opa) or docker.
# Output: sql_query_api/bundle.tar.gz (served by opa-bundle-server or S3/GCS).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPA_DIR="$ROOT/sql_query_api/opa"
OUT="$ROOT/sql_query_api/bundle.tar.gz"

MANIFEST_REVISION="${1:-dev}"
# Avoid dirtying tracked sql_query_api/opa/.manifest — build from temp copy
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
cp -R "$OPA_DIR"/* "$TMP_DIR"/ 2>/dev/null || cp -R "$OPA_DIR"/. "$TMP_DIR"/
# Ensure manifest reflects requested revision in temp build dir
echo "{\"roots\": [\"gateway\", \"policies\"], \"revision\": \"$MANIFEST_REVISION\"}" > "$TMP_DIR/.manifest"

if command -v opa >/dev/null 2>&1; then
  echo "Building bundle with opa CLI..."
  # opa build expects a directory containing policies + data.json + .manifest
  opa build -b "$TMP_DIR" -o "$OUT"
else
  echo "opa CLI not found, falling back to docker (openpolicyagent/opa)..."
  docker run --rm -v "$TMP_DIR:/src:ro" -v "$ROOT/sql_query_api:/out" \
    openpolicyagent/opa:latest build -b /src -o /out/bundle.tar.gz
fi

# Also produce a plain tar for bundle-servers that serve uncompressed bundles
# (optional, not required when using opa build output).
echo "Bundle written to $OUT ($(du -h "$OUT" | cut -f1))"
echo "Serve with: python3 -m http.server 8080 --directory $ROOT/sql_query_api"
echo "Or add opa-bundle-server service to docker-compose.yml and set"
echo "  BUNDLE_SERVICE_URL=http://opa-bundle-server:8080"
echo "  OPA_BUNDLE_URL is not set by the app; OPA fetches bundles directly."

# Verify
if command -v opa >/dev/null 2>&1; then
  echo "Verifying bundle..."
  opa test "$OPA_DIR/policies" -v || true
fi
