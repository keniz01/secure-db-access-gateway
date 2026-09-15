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
--   gateway_statement_timeout    — per-statement duration (default 30s, aligned
--                       with the gateway's SQL_QUERY_TIMEOUT_SECONDS default; keep
--                       it >= that env value so the DB budget is never looser
--                       than the application budget).
--   gateway_lock_timeout         — lock-wait duration (default 5s).
--   gateway_idle_in_transaction_session_timeout — idle-in-open-transaction kill
--                       (default 10s).
--   gateway_work_mem              — per-sort/hash operation memory budget
--                       (default 4MB). Tune down on memory-constrained hosts.
--   gateway_max_parallel_workers_per_gather — parallel-query workers/copy
--                       (default 0 = bounded/serial, for a predictable memory
--                       footprint on read-heavy analytics).
--   gateway_connection_limit      — max concurrent connections for the role
--                       (default 20). Must exceed the total number of gateway
--                       engines (primaries + read replicas across tenants)
--                       pointing at the same PostgreSQL cluster.
--
-- Resource-control model:
--   1. The DATABASE enforces statement_timeout / lock_timeout /
--      idle_in_transaction_session_timeout / work_mem /
--      max_parallel_workers_per_gather as role defaults, so even a direct
--      psql login as the gateway role is bounded.
--   2. The GATEWAY additionally pushes its per-query budgets (statement and
--      lock timeouts) at session start; CONNECTION LIMIT on the role caps how
--      many pooled connections the app (and anything else using the role) can
--      hold against the cluster.
--   3. Cluster-level settings (max_connections, shared_buffers,
--      effective_cache_size, maintenance_work_mem) are reviewed by the operator
--      — see ARCHITECTURE.md "Async PostgreSQL Pooling & Backpressure".
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
\if :{?gateway_statement_timeout}
\else
  \set gateway_statement_timeout 30s
\endif
\if :{?gateway_lock_timeout}
\else
  \set gateway_lock_timeout 5s
\endif
\if :{?gateway_idle_in_transaction_session_timeout}
\else
  \set gateway_idle_in_transaction_session_timeout 10s
\endif
\if :{?gateway_work_mem}
\else
  \set gateway_work_mem 4MB
\endif
\if :{?gateway_max_parallel_workers_per_gather}
\else
  \set gateway_max_parallel_workers_per_gather 0
\endif
\if :{?gateway_connection_limit}
\else
  \set gateway_connection_limit 20
\endif

-- Hand psql variables to PL/pgSQL as session-local settings.
SELECT set_config('app.gateway_role', :'gateway_role', false);
SELECT set_config('app.gateway_password', :'gateway_password', false);
SELECT set_config('app.schemas', :'schemas', false);
SELECT set_config('app.gateway_statement_timeout', :'gateway_statement_timeout', false);
SELECT set_config('app.gateway_lock_timeout', :'gateway_lock_timeout', false);
SELECT set_config('app.gateway_idle_in_transaction_session_timeout', :'gateway_idle_in_transaction_session_timeout', false);
SELECT set_config('app.gateway_work_mem', :'gateway_work_mem', false);
SELECT set_config('app.gateway_max_parallel_workers_per_gather', :'gateway_max_parallel_workers_per_gather', false);
SELECT set_config('app.gateway_connection_limit', :'gateway_connection_limit', false);

BEGIN;

-- 2. Create (or re-harden) the dedicated gateway role with no elevated flags,
--    no password leakage into role attributes beyond the login credential, and
--    a hard connection limit (resource bound for the whole cluster).
DO $do$
DECLARE
    gateway_role            text := current_setting('app.gateway_role', true);
    gateway_password        text := current_setting('app.gateway_password', true);
    gateway_connection_limit int := current_setting('app.gateway_connection_limit', true)::int;
BEGIN
    IF gateway_role IS NULL OR gateway_password IS NULL OR gateway_password = '' THEN
        RAISE EXCEPTION 'gateway_role and a non-empty gateway_password are required';
    END IF;
    IF gateway_connection_limit IS NULL OR gateway_connection_limit <= 0 THEN
        RAISE EXCEPTION 'gateway_connection_limit must be a positive integer';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = gateway_role) THEN
        EXECUTE format(
            'CREATE ROLE %I WITH LOGIN PASSWORD %L '
            'NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
            'NOBYPASSRLS NOINHERIT CONNECTION LIMIT %s',
            gateway_role, gateway_password, gateway_connection_limit
        );
    ELSE
        EXECUTE format(
            'ALTER ROLE %I WITH LOGIN PASSWORD %L '
            'NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
            'NOBYPASSRLS NOINHERIT CONNECTION LIMIT %s',
            gateway_role, gateway_password, gateway_connection_limit
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

-- 7. Enforce read-only, time budgets, memory bounds, and parallelism bounds at
--    the session level so even a direct psql login as the gateway role is a
--    well-behaved, resource-bounded read-only client.
DO $do$
DECLARE
    gateway_role text := current_setting('app.gateway_role', true);
    resource_settings text[][] := ARRAY[
        ARRAY['default_transaction_read_only', 'on'],
        ARRAY['statement_timeout', current_setting('app.gateway_statement_timeout', true)],
        ARRAY['lock_timeout', current_setting('app.gateway_lock_timeout', true)],
        ARRAY['idle_in_transaction_session_timeout', current_setting('app.gateway_idle_in_transaction_session_timeout', true)],
        ARRAY['work_mem', current_setting('app.gateway_work_mem', true)],
        ARRAY['max_parallel_workers_per_gather', current_setting('app.gateway_max_parallel_workers_per_gather', true)]
    ];
    setting_sub   text[];
BEGIN
    FOREACH setting_sub SLICE 1 IN ARRAY resource_settings LOOP
        IF setting_sub[2] IS NULL OR setting_sub[2] = '' THEN
            RAISE EXCEPTION 'resource setting "%" has no value; refusing to provision with an empty GUC', setting_sub[1];
        END IF;
        EXECUTE format('ALTER ROLE %I SET %s = %L', gateway_role, setting_sub[1], setting_sub[2]);
    END LOOP;
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
    conn_limit    bigint;
    guc_name      text;
    resource_gucs text[] := ARRAY['statement_timeout','lock_timeout','idle_in_transaction_session_timeout','work_mem','max_parallel_workers_per_gather'];
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

    -- 8h. A connection limit is enforced (resource bound).
    SELECT rolconnlimit INTO conn_limit FROM pg_roles WHERE oid = role_oid;
    IF conn_limit IS NULL OR conn_limit <= 0 THEN
        violations := violations || 'role has no connection limit';
    END IF;

    -- 8i. Resource GUCs are attached exactly as configured for this run so the
    --     engine actually enforces the time/memory/parallelism budgets.
    FOREACH guc_name IN ARRAY resource_gucs LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM unnest((SELECT rolconfig FROM pg_roles WHERE oid = role_oid)) cfg_entry
            WHERE cfg_entry = guc_name || '=' || current_setting('app.gateway_' || guc_name, true)
        ) THEN
            violations := violations || format(
                'role is missing %s=%s',
                guc_name,
                current_setting('app.gateway_' || guc_name, true)
            );
        END IF;
    END LOOP;

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
--
--   # 4) Resource controls are active:
--   psql "postgresql://gateway_readonly_user:<pw>@<host>/<db>" \
--        -c "SHOW statement_timeout; SHOW lock_timeout; SHOW idle_in_transaction_session_timeout; SHOW work_mem; SHOW max_parallel_workers_per_gather;"
--
--   # 5) Connection limit is enforced:
--   psql "postgresql://gateway_readonly_user:<pw>@<host>/<db>" \
--        -c "SELECT rolconnlimit FROM pg_roles WHERE rolname = current_user;"
-- ---------------------------------------------------------------------------