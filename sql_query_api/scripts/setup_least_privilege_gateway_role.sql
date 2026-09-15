-- setup_least_privilege_gateway_role.sql
-- Production provisioning: dedicated, strictly read-only PostgreSQL role for the
-- Secure DB Access Gateway.
--
-- PURPOSE:
--   Defense-in-depth. Even if the application-layer SQL safety checks were
--   bypassed, the PostgreSQL engine itself rejects every write: INSERT/UPDATE/
--   DELETE/TRUNCATE/COPY FROM, all DDL (CREATE/ALTER/DROP), and dangerous
--   function calls. The role holds only CONNECT on the database, USAGE on the
--   configured schemas, SELECT on their tables/views, and no ownership.
--
-- Required psql variable:
--   gateway_password  — the login password for the role. There is NO placeholder
--                       and NO default; provisioning fails closed when absent.
--                       Pass it at the shell (never commit it):
--                         psql -v gateway_password="$DB_GATEWAY_PASSWORD" ...
--
-- Optional psql variables:
--   gateway_role      — role name (default: gateway_readonly_user).
--   schemas           — comma-separated schemas to expose (default: public).
--                       Include the gateway metadata namespace (default "meta",
--                       hosts schema_embeddings) when it exists, e.g.
--                       -v schemas=music,meta
--
-- USAGE (as a PostgreSQL superuser, e.g. 'postgres', against the target DB):
--   psql -U postgres -d your_database \
--        -v gateway_password="$GATEWAY_DB_PASSWORD" \
--        -f setup_least_privilege_gateway_role.sql
--
-- Idempotent: safe to re-run (re-provisioning after object churn re-applies
-- the grants and re-checks the invariants). ON_ERROR_STOP is on, so any failed
-- verification aborts the whole script.
--
-- NOTE: psql does not substitute variables inside dollar-quoted PL/pgSQL
-- bodies, so the psql variables are handed to the script via session GUCs
-- (set_config / current_setting) instead.

\set ON_ERROR_STOP on

-- 1. Resolve role name (default), schemas (default), and require an explicit
--    password. A missing password variable refuses to run.
\if :{?gateway_role}
\else
  \set gateway_role gateway_readonly_user
\endif
\if :{?schemas}
\else
  \set schemas public
\endif
\if :{?gateway_password}
\else
\warn 'ERROR: gateway_password is required (pass -v gateway_password="<secure password>"); refusing to provision.'
\quit 1
\endif

-- Hand psql variables to PL/pgSQL as session-local settings.
SELECT set_config('app.gateway_role', :'gateway_role', false);
SELECT set_config('app.gateway_password', :'gateway_password', false);
SELECT set_config('app.schemas', :'schemas', false);

BEGIN;

-- 2. Create (or re-harden) the dedicated gateway role with no elevated flags
--    and no password leakage into role attributes beyond the login credential.
DO $do$
DECLARE
    gateway_role      text := current_setting('app.gateway_role', true);
    gateway_password  text := current_setting('app.gateway_password', true);
BEGIN
    IF gateway_role IS NULL OR gateway_password IS NULL OR gateway_password = '' THEN
        RAISE EXCEPTION 'gateway_role and a non-empty gateway_password are required';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = gateway_role) THEN
        EXECUTE format(
            'CREATE ROLE %I WITH LOGIN PASSWORD %L '
            'NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
            'NOBYPASSRLS NOINHERIT',
            gateway_role, gateway_password
        );
    ELSE
        EXECUTE format(
            'ALTER ROLE %I WITH LOGIN PASSWORD %L '
            'NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
            'NOBYPASSRLS NOINHERIT',
            gateway_role, gateway_password
        );
    END IF;
END
$do$;

-- 3. Baseline: strip every default/leftover privilege on the database itself.
--    PUBLIC gets nothing; the gateway role gets ONLY CONNECT.
DO $do$
DECLARE
    db_name       text := current_database();
    gateway_role  text := current_setting('app.gateway_role', true);
BEGIN
    EXECUTE format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM PUBLIC', db_name);
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', db_name);
    EXECUTE format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I', db_name, gateway_role);
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM %I', db_name, gateway_role);
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', db_name, gateway_role);
END
$do$;

-- 4. Per-schema hardening: drop PUBLIC privileges, deny CREATE, then grant
--    only USAGE + SELECT (existing tables/views now and default privileges for
--    tables created later). REVOKE + GRANT are issued per resolved schema.
DO $do$
DECLARE
    gateway_role text := current_setting('app.gateway_role', true);
    schema_name  text;
    schemas      text[] := string_to_array(current_setting('app.schemas', true), ',');
BEGIN
    FOREACH schema_name IN ARRAY schemas LOOP
        IF schema_name = '' THEN
            CONTINUE;
        END IF;
        IF NOT EXISTS (SELECT FROM pg_namespace WHERE nspname = schema_name) THEN
            RAISE EXCEPTION 'Schema "%" does not exist. Provision only real schemas (pass -v schemas=one,two).', schema_name;
        END IF;
        EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA %I FROM PUBLIC', schema_name);
        EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I', schema_name, gateway_role);
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', schema_name, gateway_role);
        EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', schema_name, gateway_role);
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA %I '
            'GRANT SELECT ON TABLES TO %I',
            schema_name, gateway_role
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES IN SCHEMA %I '
            'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
            'ON TABLES FROM %I',
            schema_name, gateway_role
        );
    END LOOP;
END
$do$;

-- 5. Explicitly deny DML/DDL on every granted table.
DO $do$
DECLARE
    gateway_role text := current_setting('app.gateway_role', true);
    schema_name  text;
    schemas      text[] := string_to_array(current_setting('app.schemas', true), ',');
BEGIN
    FOREACH schema_name IN ARRAY schemas LOOP
        IF schema_name = '' THEN
            CONTINUE;
        END IF;
        EXECUTE format(
            'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
            'ON ALL TABLES IN SCHEMA %I FROM %I',
            schema_name, gateway_role
        );
    END LOOP;
END
$do$;

-- 6. Explicitly deny dangerous system/file-access and dynamic-execution
--    function privileges. Only functions that actually exist are revoked, so
--    this stays valid across PostgreSQL versions/extensions.
DO $do$
DECLARE
    gateway_role text := current_setting('app.gateway_role', true);
    proc         regprocedure;
BEGIN
    FOR proc IN
        SELECT p.oid::regprocedure
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE p.proname = ANY (ARRAY[
            'pg_read_file',
            'pg_read_binary_file',
            'pg_write_file',
            'pg_ls_dir',
            'pg_ls_logdir',
            'pg_ls_waldir',
            'pg_stat_file',
            'pg_relation_filepath',
            'pg_tablespace_location',
            'pg_execute_server_program',
            'lo_import',
            'lo_export',
            'dblink',
            'dblink_connect',
            'dblink_exec'
        ])
    LOOP
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', proc);
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM %I', proc, gateway_role);
    END LOOP;
END
$do$;

-- 7. Enforce read-only and time budgets at the session level so even a direct
--    psql login as the gateway role cannot start a write transaction.
DO $do$
DECLARE
    gateway_role text := current_setting('app.gateway_role', true);
BEGIN
    EXECUTE format('ALTER ROLE %I SET default_transaction_read_only = on', gateway_role);
    EXECUTE format('ALTER ROLE %I SET statement_timeout = ''15s''', gateway_role);
    EXECUTE format('ALTER ROLE %I SET lock_timeout = ''5s''', gateway_role);
    EXECUTE format('ALTER ROLE %I SET idle_in_transaction_session_timeout = ''10s''', gateway_role);
END
$do$;

-- 8. Fail-closed verification: any violated invariant raises and aborts (the
--    transaction rolls back), so a misprovisioned role is never left behind.
DO $verify$
DECLARE
    gateway_role  text := current_setting('app.gateway_role', true);
    schemas       text[] := string_to_array(current_setting('app.schemas', true), ',');
    role_oid      oid;
    violations    text[] := '{}';
    cfg           text;
    owned_objects bigint;
    rel_count     bigint;
BEGIN
    SELECT oid INTO role_oid FROM pg_roles WHERE rolname = gateway_role;
    IF role_oid IS NULL THEN
        RAISE EXCEPTION 'Role "%" is missing after provisioning', gateway_role;
    END IF;

    -- 8a. No elevated role flags.
    IF EXISTS (
        SELECT FROM pg_roles
        WHERE oid = role_oid
          AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
    ) THEN
        violations := violations || 'role carries elevated flags (superuser/createdb/createrole/replication/bypassrls)';
    END IF;

    -- 8b. No CREATE anywhere (schemas reachable by the role must not allow object creation).
    IF EXISTS (
        SELECT 1
        FROM pg_namespace n
        CROSS JOIN LATERAL aclexplode(n.nspacl) a
        WHERE n.nspname = ANY (schemas)
          AND a.privilege_type = 'CREATE'
          AND (a.grantee = 0 OR a.grantee = role_oid)
    ) THEN
        violations := violations || 'schema-level CREATE is granted to the role or PUBLIC';
    END IF;

    -- 8c. No write/DDL reference privileges on any table/sequence.
    IF EXISTS (
        SELECT FROM information_schema.role_table_grants
        WHERE grantee = gateway_role
          AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')
          AND table_schema = ANY (schemas)
    ) THEN
        violations := violations || 'write or DDL table privileges are granted to the role';
    END IF;

    -- 8d. TEMPORARY/CREATE disabled on the database for the role and PUBLIC.
    IF has_database_privilege(role_oid, current_database(), 'TEMP') THEN
        violations := violations || 'role can create temporary tables';
    END IF;
    IF has_database_privilege(role_oid, current_database(), 'CREATE') THEN
        violations := violations || 'role can create objects on the database';
    END IF;

    -- 8e. The role must not own application objects (ownership implies full control).
    SELECT count(*) INTO owned_objects
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relowner = role_oid
      AND n.nspname NOT LIKE 'pg_%'
      AND n.nspname <> 'information_schema';
    IF owned_objects > 0 THEN
        violations := violations || 'role owns ' || owned_objects || ' database objects';
    END IF;

    -- 8f. Positive check: when a configured schema holds tables/views, the role
    --     must hold SELECT on them; an empty schema is skipped.
    SELECT count(*) INTO rel_count
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND n.nspname = ANY (schemas);
    IF rel_count > 0 THEN
        IF NOT EXISTS (
            SELECT FROM information_schema.role_table_grants
            WHERE grantee = gateway_role AND privilege_type = 'SELECT'
              AND table_schema = ANY (schemas)
        ) THEN
            violations := violations || 'role holds no SELECT grants on any configured schema';
        END IF;
    END IF;

    -- 8g. Session read-only default is attached to the role.
    SELECT rolconfig::text INTO cfg FROM pg_roles WHERE oid = role_oid;
    IF cfg IS NULL OR cfg NOT LIKE '%default_transaction_read_only=on%' THEN
        violations := violations || 'role is missing default_transaction_read_only=on';
    END IF;

    IF cardinality(violations) > 0 THEN
        RAISE EXCEPTION 'least-privilege verification failed for role "%": %',
            gateway_role, array_to_string(violations, '; ');
    END IF;
    RAISE NOTICE 'least-privilege verification passed for role "%"', gateway_role;
END
$verify$;

COMMIT;

-- ---------------------------------------------------------------------------
-- Post-provisioning smoke test (run manually, outside this script):
--
--   # 1) Session read-only is on:
--   psql "postgresql://gateway_readonly_user:<pw>@<host>/<db>" -c "SHOW transaction_read_only;"   # -> on
--
--   # 2) DDL denied:
--   psql "postgresql://gateway_readonly_user:<pw>@<host>/<db>" -c "CREATE TABLE _gw_smoke(id int);"
--   #    ERROR:  permission denied for schema <schema>
--
--   # 3) DML denied:
--   psql "postgresql://gateway_readonly_user:<pw>@<host>/<db>" -c "DELETE FROM <some_table>;"
--   #    ERROR:  permission denied for table <some_table>
-- ---------------------------------------------------------------------------