#!/bin/bash
# rotate_creds.sh - Daily PostgreSQL credential rotation
# Runs as a sidecar, rotates passwords for tenant database roles
# Reads tenant config from /config/tenant_databases.json
# Writes rotated credentials to /creds volume for sql_query_api

set -euo pipefail

# Configuration
CONFIG_FILE="${CONFIG_FILE:-/config/tenant_databases.json}"
CREDS_DIR="${CREDS_DIR:-/creds}"
ADMIN_PASSWORD_FILE="${ADMIN_PASSWORD_FILE:-/run/secrets/pg_rotator_admin_pass}"

# Read admin password from file (Docker secret)
if [[ ! -f "${ADMIN_PASSWORD_FILE}" ]]; then
    echo "ERROR: Admin password file not found at ${ADMIN_PASSWORD_FILE}" >&2
    exit 1
fi
PG_ADMIN_PASS=$(cat "${ADMIN_PASSWORD_FILE}")

# Read admin user from config
PG_ADMIN_USER=$(jq -r '.rotation.admin_user // "rotator_admin"' "${CONFIG_FILE}")

# Validate config file
if [[ ! -f "${CONFIG_FILE}" ]]; then
    echo "ERROR: Config file not found at ${CONFIG_FILE}" >&2
    exit 1
fi

mkdir -p "${CREDS_DIR}"

log() {
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*"
}

rotate_tenant() {
    local tenant_id="$1"
    local role_name="$2"
    local db_name="$3"
    local host="$4"
    local port="$5"

    log "Rotating credentials for ${tenant_id}/${role_name} on database ${db_name}"

    # Generate new password (32 bytes, base64, URL-safe)
    local new_pass
    new_pass=$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-48)

    # Export for psql
    export PGHOST="${host}"
    export PGPORT="${port}"
    export PGUSER="${PG_ADMIN_USER}"
    export PGPASSWORD="${PG_ADMIN_PASS}"

    # Rotate in PostgreSQL
    # Use ALTER ROLE ... PASSWORD
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
  "host": "${host}",
  "port": ${port}
}
EOF
    # Atomic write
    mv "${creds_file}.tmp" "${creds_file}"
    chmod 600 "${creds_file}"

    log "Rotated ${tenant_id}/${role_name} -> written to ${creds_file}"
}

main() {
    log "Starting credential rotation"

    # Parse tenant_databases.json
    # Structure: { "tenants": { "tenant-a": { "databases": { "default": { ... } } } } }
    local tenant_count
    tenant_count=$(jq '.tenants | length' "${CONFIG_FILE}")
    log "Found ${tenant_count} tenant(s) in config"

    for tenant_id in $(jq -r '.tenants | keys[]' "${CONFIG_FILE}"); do
        for db_id in $(jq -r ".tenants[\"${tenant_id}\"].databases | keys[]" "${CONFIG_FILE}"); do
            # Read database config
            local credential_mode
            credential_mode=$(jq -r ".tenants[\"${tenant_id}\"].databases[\"${db_id}\"].credential_mode // \"static\"" "${CONFIG_FILE}")

            if [[ "${credential_mode}" != "rotated" ]]; then
                log "Skipping ${tenant_id}/${db_id} (credential_mode: ${credential_mode})"
                continue
            fi

            local role_name host port db_name rotation_enabled interval_seconds
            role_name=$(jq -r ".tenants[\"${tenant_id}\"].databases[\"${db_id}\"].role_name" "${CONFIG_FILE}")
            host=$(jq -r ".tenants[\"${tenant_id}\"].databases[\"${db_id}\"].host" "${CONFIG_FILE}")
            port=$(jq -r ".tenants[\"${tenant_id}\"].databases[\"${db_id}\"].port // 5432" "${CONFIG_FILE}")
            db_name=$(jq -r ".tenants[\"${tenant_id}\"].databases[\"${db_id}\"].db_name" "${CONFIG_FILE}")
            rotation_enabled=$(jq -r ".tenants[\"${tenant_id}\"].databases[\"${db_id}\"].rotation.enabled // true" "${CONFIG_FILE}")
            interval_seconds=$(jq -r ".tenants[\"${tenant_id}\"].databases[\"${db_id}\"].rotation.interval_seconds // 86400" "${CONFIG_FILE}")

            if [[ "${rotation_enabled}" != "true" ]]; then
                log "Skipping ${tenant_id}/${db_id} (rotation disabled)"
                continue
            fi

            if [[ -z "${role_name}" || -z "${host}" || -z "${db_name}" ]]; then
                log "ERROR: Invalid config for ${tenant_id}/${db_id} (missing required fields)"
                continue
            fi

            rotate_tenant "${tenant_id}" "${role_name}" "${db_name}" "${host}" "${port}" || {
                log "ERROR: Failed to rotate ${tenant_id}/${role_name}"
                exit 1
            }

            # Update global rotation interval if this tenant has a shorter one
            if [[ "${interval_seconds}" -lt "${ROTATION_INTERVAL:-86400}" ]]; then
                export ROTATION_INTERVAL="${interval_seconds}"
            fi
        done
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