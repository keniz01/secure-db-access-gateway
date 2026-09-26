#!/bin/bash
# rotate_creds.sh - Daily PostgreSQL credential rotation
# Runs as a sidecar, rotates passwords for tenant database roles
# Writes new credentials to shared volume for sql_query_api to pick up

set -euo pipefail

# Configuration from environment
PG_HOST="${PG_HOST:-postgres}"
PG_PORT="${PG_PORT:-5432}"
PG_ADMIN_USER="${PG_ADMIN_USER:-rotator_admin}"
PG_ADMIN_PASS="${PG_ADMIN_PASS:-}"
CREDS_DIR="${CREDS_DIR:-/creds}"
TENANTS_JSON="${TENANTS_JSON:-}"

# Validate required env
if [[ -z "${PG_ADMIN_PASS}" ]]; then
    echo "ERROR: PG_ADMIN_PASS not set" >&2
    exit 1
fi
if [[ -z "${TENANTS_JSON}" ]]; then
    echo "ERROR: TENANTS_JSON not set" >&2
    exit 1
fi

mkdir -p "${CREDS_DIR}"

# Export for psql
export PGHOST="${PG_HOST}"
export PGPORT="${PG_PORT}"
export PGUSER="${PG_ADMIN_USER}"
export PGPASSWORD="${PG_ADMIN_PASS}"

log() {
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*"
}

rotate_tenant() {
    local tenant_id="$1"
    local role_name="$2"
    local db_name="$3"

    log "Rotating credentials for ${tenant_id}/${role_name} on database ${db_name}"

    # Generate new password (32 bytes, base64, URL-safe)
    local new_pass
    new_pass=$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-48)

    # Rotate in PostgreSQL
    # Use ALTER ROLE ... PASSWORD with VALID UNTIL for optional expiry
    psql -d "${db_name}" -v ON_ERROR_STOP=1 -c \
        "ALTER ROLE ${role_name} PASSWORD '${new_pass}';"

    # Write new credentials to shared volume (JSON format for resolver)
    local creds_file="${CREDS_DIR}/${tenant_id}_${role_name}.json"
    cat > "${creds_file}.tmp" <<EOF
{
  "tenant_id": "${tenant_id}",
  "role_name": "${role_name}",
  "database": "${db_name}",
  "username": "${role_name}",
  "password": "${new_pass}",
  "rotated_at": "$(date -u +'%Y-%m-%dT%H:%M:%SZ')",
  "host": "${PG_HOST}",
  "port": ${PG_PORT}
}
EOF
    # Atomic write
    mv "${creds_file}.tmp" "${creds_file}"
    chmod 600 "${creds_file}"

    log "Rotated ${tenant_id}/${role_name} -> written to ${creds_file}"
}

main() {
    log "Starting credential rotation"

    # Parse TENANTS_JSON: {"tenant-a":{"role":"analytics","db":"analytics"},"tenant-b":{...}}
    echo "${TENANTS_JSON}" | jq -c 'to_entries[]' | while read -r entry; do
        tenant_id=$(echo "${entry}" | jq -r '.key')
        role_name=$(echo "${entry}" | jq -r '.value.role')
        db_name=$(echo "${entry}" | jq -r '.value.db')

        if [[ -z "${tenant_id}" || -z "${role_name}" || -z "${db_name}" ]]; then
            log "ERROR: Invalid tenant config entry: ${entry}"
            continue
        fi

        rotate_tenant "${tenant_id}" "${role_name}" "${db_name}" || {
            log "ERROR: Failed to rotate ${tenant_id}/${role_name}"
            exit 1
        }
    done

    log "Credential rotation complete"
}

# Run once (for cron) or loop (for sidecar)
if [[ "${1:-}" == "--loop" ]]; then
    INTERVAL="${ROTATION_INTERVAL:-86400}"  # default 24h
    log "Running in loop mode, interval: ${INTERVAL}s"
    while true; do
        main
        sleep "${INTERVAL}"
    done
else
    main
fi