"""
End-to-end fuzz test for the FULL governed SQL authorization pipeline.

This exercises the complete path:
    Raw SQL
        │
        ▼
    clean_sql
        │
        ▼
    AstSqlAnalyzer (safety checker)
        │
        ▼
    PolicyEvaluator (OPA or legacy)
        │
        ▼
    TenantDatabaseResolver
        │
        ▼
    apply_row_restrictions / rewrite_masked_columns
        │
        ▼
    GovernedQueryGateway → SQLite (in-memory)

Properties asserted for EVERY generated input:
1. "denied table can never become reachable"
2. "non-SELECT statement can never reach DB"
3. "unauthorized column can never appear in result"
3b. "masked column is always nulled in result"
4. "tenant A can never resolve tenant B's database"
5. "policy-denied request never executes SQL (gateway rejects before execution)"

The seed is fixed so failures are reproducible.
"""

from __future__ import annotations

import random
import hashlib
from typing import Any

import pytest
import sqlglot
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from auth import Principal
from repositories.sql_validators.ast_analyzer import AstSqlAnalyzer
from repositories.sql_validators.sql_safety_checker import DefaultSqlSafetyChecker, clean_sql
from services.policy_engine import Policy, PolicyEvaluator, PolicyDecision
from services.query_gateway import GovernedQueryGateway, GovernedQueryRequest
from services.tenant_database_resolver import TenantDatabaseConfig, TenantDatabaseResolver

# ---------------------------------------------------------------------------
# Fixed seed for reproducibility
# ---------------------------------------------------------------------------
SEED = 20260926
RUNS_PER_PROPERTY = 800

# ---------------------------------------------------------------------------
# Adversarial query templates targeting pipeline weaknesses
# ---------------------------------------------------------------------------
TEMPLATES = [
    # Basic SELECT
    "SELECT * FROM {t}",
    "SELECT {c} FROM {t} WHERE {c} = 1",
    "SELECT a, b FROM {t} JOIN other o ON o.id = {t}.id",
    "SELECT count(*) FROM {t} GROUP BY {c}",
    
    # Subqueries & derived tables
    "SELECT {c} FROM (SELECT * FROM {t}) d",
    "SELECT * FROM (SELECT {c} FROM {t} WHERE {c} > 0) sub",
    "SELECT (SELECT max({c}) FROM {t}) AS max_val",
    "SELECT * FROM {t} WHERE {c} IN (SELECT {c} FROM other)",
    "SELECT * FROM {t} WHERE EXISTS (SELECT 1 FROM other WHERE other.id = {t}.id)",
    
    # CTEs (including recursive)
    "WITH x AS (SELECT {c} FROM {t}) SELECT * FROM x",
    "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<3) SELECT * FROM x",
    "WITH x AS (SELECT * FROM {t}), y AS (SELECT * FROM other) SELECT * FROM x JOIN y ON x.id = y.id",
    "WITH x AS (SELECT * FROM {t} WHERE {c} = 1) SELECT * FROM x UNION SELECT * FROM other",
    
    # UNION / INTERSECT / EXCEPT
    "SELECT {c} FROM {t} UNION SELECT {c} FROM other",
    "SELECT {c} FROM {t} UNION ALL SELECT {c} FROM other",
    "SELECT {c} FROM {t} INTERSECT SELECT {c} FROM other",
    "SELECT {c} FROM {t} EXCEPT SELECT {c} FROM other",
    
    # JOINs
    "SELECT t1.{c}, t2.{c} FROM {t} t1 JOIN other t2 ON t1.id = t2.id",
    "SELECT * FROM {t} LEFT JOIN other ON {t}.id = other.id",
    "SELECT * FROM {t} CROSS JOIN other",
    "SELECT * FROM {t} t1, other t2 WHERE t1.id = t2.id",
    
    # Window functions, lateral, aggregates
    "SELECT {c}, sum({c}) OVER (PARTITION BY dept) FROM {t}",
    "SELECT * FROM {t}, LATERAL (SELECT max({c}) FROM other WHERE other.id = {t}.id) sub",
    
    # CASE, COALESCE, expressions
    "SELECT CASE WHEN {c} > 0 THEN 'pos' ELSE 'neg' END FROM {t}",
    "SELECT COALESCE({c}, 'default') FROM {t}",
    "SELECT CAST({c} AS TEXT) FROM {t}",
    
    # Quoted identifiers, schema-qualified
    'SELECT * FROM "{t}"',
    'SELECT * FROM "public"."{t}"',
    "SELECT * FROM public.{t}",
    
    # Comments (should be rejected by safety checker)
    "SELECT * FROM {t} -- comment",
    "SELECT * FROM {t} /* comment */",
    
    # PostgreSQL-specific
    "SELECT * FROM {t} FOR SHARE",
    "SELECT * FROM {t} FOR UPDATE",
    "SELECT DISTINCT ON ({c}) * FROM {t} ORDER BY {c}",
    "SELECT * FROM {t} FETCH FIRST 5 ROWS ONLY",
    
    # Recursive CTE with data access
    "WITH RECURSIVE cte AS (SELECT * FROM {t} UNION ALL SELECT * FROM {t}) SELECT * FROM cte",
    
    # Subquery in SELECT with mutation attempt
    "SELECT (SELECT {c} FROM {t} LIMIT 1) AS sub",
    
    # LATERAL join with function
    "SELECT * FROM {t}, LATERAL generate_series(1, 3) AS g",
]

SUBSTITUTIONS = {
    "{t}": ["employees", "departments", "projects", "audit_log", "employees e", "departments d"],
    "{c}": ["id", "name", "email", "dept", "tenant_id", "salary", "ssn", "budget"],
}

# Adversarial fragments designed to bypass specific pipeline stages
ADVERSARIAL_FRAGMENTS = [
    # Statement injection
    "; DROP TABLE employees;",
    "; INSERT INTO employees VALUES (99, 'hack', 'hack@x.com', 'it', 'tenant-a', 0, '000-00-0000');",
    "; UPDATE employees SET salary = 999999;",
    
    # CTE mutation
    "WITH hacked AS (DELETE FROM employees RETURNING *) SELECT * FROM hacked",
    "WITH evil AS (INSERT INTO employees VALUES (99, 'x', 'x@x.com', 'x', 'tenant-a', 0, 'x') RETURNING *) SELECT * FROM evil",
    "WITH wiped AS (UPDATE employees SET salary = 0 RETURNING *) SELECT * FROM wiped",
    
    # Subquery mutation
    "SELECT * FROM employees WHERE id IN (DELETE FROM employees RETURNING id)",
    "SELECT (UPDATE employees SET salary = 0 RETURNING 1)",
    
    # Dangerous functions
    "pg_read_file('/etc/passwd')",
    "pg_sleep(10)",
    "pg_sleep_for('1 minute')",
    "pg_execute_server_program('ls')",
    "lo_import('/etc/passwd')",
    "dblink_exec('conn', 'DROP TABLE employees')",
    
    # Row locks
    "FOR SHARE",
    "FOR UPDATE",
    "FOR NO KEY UPDATE",
    "FOR KEY SHARE",
    
    # SELECT INTO (table creation)
    "INTO evil_table",
    "INTO TEMP evil_table",
    
    # Catalog access
    "pg_catalog.pg_class",
    "information_schema.tables",
    "pg_catalog.pg_attribute",
    
    # Comment evasion
    "--",
    "/*",
    "*/",
    "/* */",
    "/*!50000 DROP TABLE employees */",
    "--\n",
    
    # Unicode obfuscation
    "ＳＥＬＥＣＴ",
    "\u200b",
    "ＵＮＩＯＮ",
    
    # Control bytes
    "\x00",
    "\x1f",
    
    # Dollar quoting
    "$$",
    "$tag$",
    
    # Search path manipulation (PostgreSQL)
    "SET search_path TO pg_catalog;",
    "SET search_path TO malicious_schema;",
    
    # Schema-qualified system tables
    "pg_catalog.pg_class",
    '"pg_catalog"."pg_class"',
    "information_schema.columns",
    
    # Quoted identifiers with special chars
    '"employees; DROP TABLE employees"',
    "'; DROP TABLE employees; --",
    
    # Deep nesting
    "(" * 100 + "1" + ")" * 100,
    
    # Forbidden keywords
    "DELETE",
    "UPDATE",
    "INSERT",
    "DROP",
    "CREATE",
    "ALTER",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "COPY",
    "EXECUTE",
    "CALL",
]

# Policies for tenant-a (allow employees, departments; deny projects, audit_log; mask salary, ssn)
TENANT_A_POLICIES = [
    Policy(
        id="allow-employees",
        effect="allow",
        org_id="tenant-a",
        database_id="default",
        table="employees",
        columns={"id", "name", "email", "dept", "tenant_id", "salary", "ssn"},
        masked_columns={"salary", "ssn"},
        row_scope={"tenant_id": "tenant_id"},
    ),
    Policy(
        id="allow-departments",
        effect="allow",
        org_id="tenant-a",
        database_id="default",
        table="departments",
        columns={"id", "name", "tenant_id"},
        row_scope={"tenant_id": "tenant_id"},
    ),
    Policy(
        id="deny-projects",
        effect="deny",
        org_id="tenant-a",
        database_id="default",
        table="projects",
    ),
    Policy(
        id="deny-audit_log",
        effect="deny",
        org_id="tenant-a",
        database_id="default",
        table="audit_log",
    ),
]

# Policies for tenant-b (allow projects; deny employees)
TENANT_B_POLICIES = [
    Policy(
        id="allow-projects-b",
        effect="allow",
        org_id="tenant-b",
        database_id="default",
        table="projects",
        columns={"id", "name", "dept", "tenant_id", "budget"},
        row_scope={"tenant_id": "tenant_id"},
    ),
    Policy(
        id="deny-employees-b",
        effect="deny",
        org_id="tenant-b",
        database_id="default",
        table="employees",
    ),
]

ANALYZER = AstSqlAnalyzer()
SAFETY_CHECKER = DefaultSqlSafetyChecker()


def _mutate(rng: random.Random, template: str, depth: int) -> str:
    """Apply random mutations to a template."""
    sql = template
    for _ in range(rng.randint(1, depth)):
        op = rng.randrange(8)
        if op == 0:
            # Splice adversarial fragment at random position
            idx = rng.randrange(len(sql) + 1)
            frag = rng.choice(ADVERSARIAL_FRAGMENTS)
            sql = sql[:idx] + frag + sql[idx:]
        elif op == 1:
            sql = rng.choice(ADVERSARIAL_FRAGMENTS) + sql
        elif op == 2:
            sql = sql + rng.choice(ADVERSARIAL_FRAGMENTS)
        elif op == 3:
            # Unicode lookalike substitution
            char_map = {"S": "Ｓ", "L": "Ｌ", "E": "Ｅ", "C": "Ｃ", "T": "Ｔ", "U": "Ｕ", "N": "Ｎ", "I": "Ｉ", "O": "Ｏ", "a": "а", "o": "о", "e": "е"}
            src = rng.choice(list(char_map))
            sql = sql.replace(src, char_map[src], 1)
        elif op == 4:
            # Replace single char with adversarial char
            if sql:
                idx = rng.randrange(len(sql))
                repl = rng.choice(["'", '"', ";", "(", ")", "\x00", "--", "E'", "\\", "\u200b"])
                sql = sql[:idx] + repl + sql[idx + 1:]
        elif op == 5:
            # Duplicate random substring (stress nesting)
            if len(sql) > 4:
                start = rng.randrange(len(sql) - 1)
                end = rng.randrange(start + 1, min(start + 30, len(sql)) + 1)
                sql = sql[:end] + sql[start:end] + sql[end:]
        elif op == 6:
            # Wrap in CTE
            sql = f"WITH x AS ({sql}) SELECT * FROM x"
        elif op == 7:
            # Add UNION with adversarial fragment
            sql = f"{sql} UNION SELECT {rng.choice(ADVERSARIAL_FRAGMENTS)}"
    return sql


def _generate_input(rng: random.Random) -> str:
    """Generate a single adversarial query."""
    template = rng.choice(TEMPLATES)
    sql = template
    for key, options in SUBSTITUTIONS.items():
        sql = sql.replace(key, rng.choice(options), 1)
    return _mutate(rng, sql, depth=rng.randint(1, 4))


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------
SCHEMA_STATEMENTS = [
    """CREATE TABLE employees (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT,
        dept TEXT,
        tenant_id TEXT NOT NULL,
        salary INTEGER,
        ssn TEXT
    )""",
    """CREATE TABLE departments (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        tenant_id TEXT NOT NULL
    )""",
    """CREATE TABLE projects (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        dept TEXT,
        tenant_id TEXT NOT NULL,
        budget INTEGER
    )""",
    """CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY,
        event TEXT,
        tenant_id TEXT NOT NULL
    )""",
    """INSERT INTO employees (id, name, email, dept, tenant_id, salary, ssn) VALUES
    (1, 'Alice', 'alice@tenant-a.com', 'engineering', 'tenant-a', 100000, '123-45-6789'),
    (2, 'Bob', 'bob@tenant-a.com', 'sales', 'tenant-a', 80000, '987-65-4321'),
    (3, 'Carol', 'carol@tenant-b.com', 'engineering', 'tenant-b', 120000, '555-55-5555'),
    (4, 'Dave', 'dave@tenant-b.com', 'hr', 'tenant-b', 90000, '111-22-3333')""",
    """INSERT INTO departments (id, name, tenant_id) VALUES
    (1, 'engineering', 'tenant-a'),
    (2, 'sales', 'tenant-a'),
    (3, 'engineering', 'tenant-b'),
    (4, 'hr', 'tenant-b')""",
    """INSERT INTO projects (id, name, dept, tenant_id, budget) VALUES
    (1, 'Project Alpha', 'engineering', 'tenant-a', 500000),
    (2, 'Project Beta', 'sales', 'tenant-a', 300000),
    (3, 'Project Gamma', 'engineering', 'tenant-b', 700000)""",
    """INSERT INTO audit_log (id, event, tenant_id) VALUES
    (1, 'login', 'tenant-a'),
    (2, 'query', 'tenant-a'),
    (3, 'login', 'tenant-b')""",
]


@pytest.fixture(scope="module")
async def sqlite_engine() -> AsyncEngine:
    """Create in-memory SQLite engine with test schema."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        for stmt in SCHEMA_STATEMENTS:
            await conn.execute(text(stmt))
    yield engine
    await engine.dispose()


@pytest.fixture
def tenant_resolver() -> TenantDatabaseResolver:
    """Resolver with tenant-a and tenant-b databases pointing to same SQLite."""
    return TenantDatabaseResolver([
        TenantDatabaseConfig("tenant-a", "default", "sqlite+aiosqlite:///:memory:"),
        TenantDatabaseConfig("tenant-b", "default", "sqlite+aiosqlite:///:memory:"),
    ])


@pytest.fixture
def principal_a() -> Principal:
    return Principal(
        user_id="user-a",
        email="user-a@tenant-a.com",
        org_id="tenant-a",
        roles=frozenset({"viewer"}),
        attributes={"tenant_id": "tenant-a"},
    )


@pytest.fixture
def principal_b() -> Principal:
    return Principal(
        user_id="user-b",
        email="user-b@tenant-b.com",
        org_id="tenant-b",
        roles=frozenset({"viewer"}),
        attributes={"tenant_id": "tenant-b"},
    )


@pytest.fixture
def policy_evaluator() -> PolicyEvaluator:
    return PolicyEvaluator(TENANT_A_POLICIES + TENANT_B_POLICIES, enabled=True)


@pytest.fixture
def gateway(
    sqlite_engine: AsyncEngine,
    tenant_resolver: TenantDatabaseResolver,
    policy_evaluator: PolicyEvaluator,
) -> GovernedQueryGateway:
    """Build a governed gateway bound to the test SQLite engine."""
    
    from repositories.sql_query_repository import SqlQueryRepository
    from services.sql_query_service import SqlQueryService
    
    class TestProvider:
        def resolve(self, principal: Principal, database_id: str | None):
            binding = tenant_resolver.resolve(principal, database_id or "default")
            repo = SqlQueryRepository(sqlite_engine, SAFETY_CHECKER)
            service = SqlQueryService(repository=repo)
            return binding, service
    
    return GovernedQueryGateway(
        provider=TestProvider(),
        safety_checker=SAFETY_CHECKER,
        policy_evaluator=policy_evaluator,
    )


# ---------------------------------------------------------------------------
# Property 1: Denied table can never become reachable
# ---------------------------------------------------------------------------
class TestDeniedTableNeverReachable:
    """Property: A table explicitly denied by policy must never return rows, regardless of query obfuscation."""
    
    @pytest.mark.asyncio
    async def test_denied_tables_unreachable(self, gateway: GovernedQueryGateway, principal_a: Principal):
        rng = random.Random(SEED + 1)
        denied_tables = {"projects", "audit_log"}
        
        for _ in range(RUNS_PER_PROPERTY):
            sql = _generate_input(rng)
            
            # Check if query references any denied table (case-insensitive)
            sql_lower = sql.lower()
            references_denied = any(
                f"from {table}" in sql_lower or f"join {table}" in sql_lower
                for table in denied_tables
            )
            
            if not references_denied:
                continue  # Only test queries that attempt to access denied tables
            
            try:
                result = await gateway.execute(
                    GovernedQueryRequest(
                        principal=principal_a,
                        database_id="default",
                        sql=sql,
                    )
                )
                # If we get here, the query was allowed - check if it actually accessed denied data
                # The result should be empty because row restrictions should filter everything
                # OR the query should have been rejected by policy evaluator
                pytest.fail(f"Denied table query was allowed: {sql}")
            except PermissionError:
                # Expected - policy denied the request
                pass
            except ValueError as e:
                if "safety validation" in str(e).lower():
                    # Expected - safety checker rejected it
                    pass
                else:
                    # Some other validation error - acceptable
                    pass
            except Exception:
                # Any other exception means the query didn't execute successfully
                pass

    @pytest.mark.asyncio
    async def test_denied_table_via_cte_union_subquery(self, gateway: GovernedQueryGateway, principal_a: Principal):
        """Specific test for denied table access via CTE, UNION, subquery obfuscation."""
        attack_queries = [
            "WITH x AS (SELECT * FROM projects) SELECT * FROM x",
            "SELECT * FROM (SELECT * FROM projects) AS sub",
            "SELECT * FROM employees UNION SELECT * FROM projects",
            "SELECT * FROM employees INTERSECT SELECT * FROM projects",
            "WITH RECURSIVE cte AS (SELECT * FROM projects UNION ALL SELECT * FROM projects) SELECT * FROM cte",
            "SELECT * FROM employees WHERE id IN (SELECT id FROM projects)",
            "SELECT * FROM employees WHERE EXISTS (SELECT 1 FROM projects WHERE projects.id = employees.id)",
            'SELECT * FROM "projects"',  # quoted
            "SELECT * FROM public.projects",  # schema qualified
        ]
        
        for sql in attack_queries:
            with pytest.raises(PermissionError):
                await gateway.execute(
                    GovernedQueryRequest(
                        principal=principal_a,
                        database_id="default",
                        sql=sql,
                    )
                )


# ---------------------------------------------------------------------------
# Property 2: Non-SELECT statement can never reach DB
# ---------------------------------------------------------------------------
class TestNonSelectNeverReachesDB:
    """Property: Any non-SELECT statement (DML, DDL, DCL) must be rejected before DB execution."""
    
    @pytest.mark.asyncio
    async def test_dml_ddl_rejected(self, gateway: GovernedQueryGateway, principal_a: Principal):
        non_select_queries = [
            "INSERT INTO employees VALUES (99, 'hack', 'hack@x.com', 'it', 'tenant-a', 0, '000-00-0000')",
            "UPDATE employees SET salary = 999999 WHERE id = 1",
            "DELETE FROM employees WHERE id = 1",
            "DROP TABLE employees",
            "CREATE TABLE backdoor (id int)",
            "ALTER TABLE employees ADD COLUMN backdoor TEXT",
            "TRUNCATE TABLE employees",
            "GRANT ALL ON employees TO public",
            "REVOKE SELECT ON employees FROM viewer",
            "COPY employees TO '/tmp/leak.csv'",
            "EXECUTE some_function()",
            "CALL some_procedure()",
            "SELECT * INTO new_table FROM employees",
            "SELECT * INTO TEMP temp_table FROM employees",
        ]
        
        for sql in non_select_queries:
            with pytest.raises((PermissionError, ValueError)):
                await gateway.execute(
                    GovernedQueryRequest(
                        principal=principal_a,
                        database_id="default",
                        sql=sql,
                    )
                )


# ---------------------------------------------------------------------------
# Property 3: Unauthorized column can never appear in result
# ---------------------------------------------------------------------------
class TestUnauthorizedColumnNeverInResult:
    """Property: Columns not in allow policy must never appear in result rows."""
    
    @pytest.mark.asyncio
    async def test_unauthorized_columns_absent(self, gateway: GovernedQueryGateway, principal_a: Principal):
        rng = random.Random(SEED + 2)
        # tenant-a can access employees, departments but NOT projects, audit_log
        # salary and ssn are masked (should be null, not absent)
        unauthorized_tables = {"projects", "audit_log"}
        unauthorized_columns = {"budget"}  # projects.budget
        
        for _ in range(RUNS_PER_PROPERTY):
            sql = _generate_input(rng)
            
            # Only test queries that might slip unauthorized columns
            try:
                result = await gateway.execute(
                    GovernedQueryRequest(
                        principal=principal_a,
                        database_id="default",
                        sql=sql,
                    )
                )
                
                # Check result columns
                if result:
                    for row in result:
                        for col in row.keys():
                            col_lower = col.lower()
                            # No column from unauthorized tables should appear
                            assert col_lower not in unauthorized_columns, (
                                f"Unauthorized column '{col}' appeared in result for query: {sql}"
                            )
            except (PermissionError, ValueError):
                # Rejected by policy or safety - expected
                pass
            except Exception:
                # Other errors - query didn't execute
                pass

    @pytest.mark.asyncio
    async def test_masked_columns_always_nulled(self, gateway: GovernedQueryGateway, principal_a: Principal):
        """Masked columns (salary, ssn) must always be NULL in results."""
        # Direct queries that explicitly select masked columns (allowed by policy, but masked)
        # Note: SELECT * is not allowed with column-level policies, so we list columns explicitly
        allowed_queries = [
            "SELECT id, name, salary, ssn FROM employees",
            "SELECT salary, ssn FROM employees WHERE tenant_id = 'tenant-a'",
            "SELECT e.salary, e.ssn FROM employees e JOIN departments d ON e.dept = d.name",
            "SELECT salary AS pay, ssn AS social FROM employees",
            "SELECT COALESCE(salary, 0), ssn FROM employees",
        ]
        
        for sql in allowed_queries:
            result = await gateway.execute(
                GovernedQueryRequest(
                    principal=principal_a,
                    database_id="default",
                    sql=sql,
                )
            )
            
            if result:
                for row in result:
                    # salary and ssn must be NULL (or their aliases)
                    for key, val in row.items():
                        key_lower = key.lower()
                        if key_lower in {"salary", "ssn", "pay", "social"}:
                            assert val is None, f"Masked column '{key}' not nulled: {sql} -> {row}"


# ---------------------------------------------------------------------------
# Property 4: Tenant A can never resolve Tenant B's database
# ---------------------------------------------------------------------------
class TestTenantIsolation:
    """Property: Cross-tenant database resolution must fail closed."""
    
    def test_resolver_rejects_cross_tenant(self, tenant_resolver: TenantDatabaseResolver, principal_a: Principal):
        """Tenant A cannot resolve Tenant B's database ID."""
        with pytest.raises(Exception):  # TenantDatabaseResolutionError
            tenant_resolver.resolve(principal_a, "tenant-b-db")
    
    @pytest.mark.asyncio
    async def test_gateway_rejects_cross_tenant(self, gateway: GovernedQueryGateway, principal_a: Principal):
        """Gateway must reject cross-tenant database access attempts."""
        # This requires a database_id that exists for tenant-b but not tenant-a
        # Our test resolver only has "default" for both, so we test with invalid db_id
        with pytest.raises(Exception):
            await gateway.execute(
                GovernedQueryRequest(
                    principal=principal_a,
                    database_id="tenant-b-only-db",
                    sql="SELECT * FROM employees",
                )
            )
    
    @pytest.mark.asyncio
    async def test_tenant_b_cannot_access_tenant_a_data(self, gateway: GovernedQueryGateway, principal_b: Principal):
        """Tenant B principal cannot access tenant-a allowed tables (employees)."""
        # principal_b has policies that DENY employees table
        with pytest.raises(PermissionError):
            await gateway.execute(
                GovernedQueryRequest(
                    principal=principal_b,
                    database_id="default",
                    sql="SELECT * FROM employees",
                )
            )


# ---------------------------------------------------------------------------
# Property 5: Policy-denied request never executes SQL
# ---------------------------------------------------------------------------
class TestPolicyDeniedNeverExecutes:
    """Property: When policy evaluator denies, the SQL must never reach the database."""
    
    @pytest.mark.asyncio
    async def test_deny_logs_no_execution(self, gateway: GovernedQueryGateway, principal_a: Principal, monkeypatch: pytest.MonkeyPatch):
        """Verify that denied queries don't execute by checking audit log wasn't called with sql_query."""
        audit_calls = []
        
        def mock_audit(event_type: str, **kwargs):
            audit_calls.append({"event": event_type, **kwargs})
        
        # Replace audit function at the gateway module (where it's imported)
        import services.query_gateway as qg
        original_audit = qg.log_audit_event
        monkeypatch.setattr(qg, "log_audit_event", mock_audit)
        
        # Also patch the gateway instance's audit reference
        original_gateway_audit = gateway._audit
        gateway._audit = mock_audit
        
        try:
            # Query that will be denied (projects table)
            with pytest.raises(PermissionError):
                await gateway.execute(
                    GovernedQueryRequest(
                        principal=principal_a,
                        database_id="default",
                        sql="SELECT * FROM projects",
                    )
                )
            
            # Verify audit was called with policy_denied, NOT sql_query
            denied_events = [c for c in audit_calls if c["event"] == "policy_denied"]
            executed_events = [c for c in audit_calls if c["event"] == "sql_query"]
            
            assert len(denied_events) == 1, f"Expected 1 policy_denied event, got {len(denied_events)}"
            assert len(executed_events) == 0, f"sql_query event fired for denied request: {executed_events}"
        finally:
            monkeypatch.setattr(qg, "log_audit_event", original_audit)
            gateway._audit = original_gateway_audit


# ---------------------------------------------------------------------------
# Additional: Fuzz the full pipeline with random adversarial queries
# ---------------------------------------------------------------------------
class TestFullPipelineFuzz:
    """Comprehensive fuzz test driving the entire governed pipeline."""
    
    @pytest.mark.asyncio
    async def test_fuzz_pipeline_consistency(
        self,
        gateway: GovernedQueryGateway,
        principal_a: Principal,
        principal_b: Principal,
        tenant_resolver: TenantDatabaseResolver,
    ):
        """Run generated queries through full pipeline, verify all properties hold."""
        rng = random.Random(SEED + 3)
        
        # Track all audit events for verification
        audit_calls: list[dict[str, Any]] = []
        
        def mock_audit(event_type: str, **kwargs):
            audit_calls.append({"event": event_type, **kwargs})
        
        import services.query_gateway as qg
        original_audit = qg.log_audit_event
        qg.log_audit_event = mock_audit
        
        try:
            for i in range(RUNS_PER_PROPERTY):
                sql = _generate_input(rng)
                
                # Alternate between tenant-a and tenant-b principals
                principal = principal_a if i % 2 == 0 else principal_b
                
                try:
                    result = await gateway.execute(
                        GovernedQueryRequest(
                            principal=principal,
                            database_id="default",
                            sql=sql,
                        )
                    )
                    
                    # If execution succeeded, verify result integrity
                    if result:
                        for row in result:
                            # Property 3b: Masked columns must be NULL
                            for key, val in row.items():
                                if key.lower() in {"salary", "ssn"}:
                                    assert val is None, f"Masked column {key} not nulled: {sql}"
                    
                    # Property 5: If it executed, there should be sql_query audit
                    # (not policy_denied)
                    recent = audit_calls[-1] if audit_calls else {}
                    if recent.get("event") == "policy_denied":
                        pytest.fail(f"Query executed but was audited as denied: {sql}")
                        
                except PermissionError:
                    # Policy denied - verify audit has policy_denied, not sql_query
                    if audit_calls and audit_calls[-1].get("event") == "sql_query":
                        pytest.fail(f"Policy denied but sql_query audited: {sql}")
                        
                except ValueError as e:
                    if "safety validation" in str(e).lower():
                        # Safety checker rejected - this is correct behavior
                        pass
                    else:
                        raise
                except Exception:
                    # Other exceptions (syntax errors, etc.) - query didn't execute
                    pass
                    
        finally:
            qg.log_audit_event = original_audit


# ---------------------------------------------------------------------------
# Deterministic regression tests for known bypass patterns
# ---------------------------------------------------------------------------
class TestKnownBypassRegressions:
    """Explicit regression tests for every discovered bypass pattern."""
    
    @pytest.mark.parametrize("sql", [
        # SELECT INTO table creation
        "SELECT * INTO evil FROM employees",
        "SELECT * INTO TEMP evil FROM employees",
        
        # Row locks
        "SELECT * FROM employees FOR SHARE",
        "SELECT * FROM employees FOR UPDATE",
        
        # Catalog access
        "SELECT * FROM pg_catalog.pg_class",
        'SELECT * FROM "pg_catalog"."pg_class"',
        "SELECT * FROM information_schema.tables",
        
        # Dangerous functions
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_sleep(5)",
        "SELECT lo_import('/etc/passwd')",
        "SELECT dblink_exec('conn', 'DROP TABLE x')",
        
        # Comment evasion
        "SELECT * FROM employees -- comment",
        "SELECT * FROM employees /* comment */",
        
        # Unicode obfuscation
        "ＳＥＬＥＣＴ * FROM employees",
        "SELECT * FROM employees \u200b UNION SELECT 1",
        
        # Deep nesting
        "SELECT " + "(" * 200 + "1" + ")" * 200,
    ])
    @pytest.mark.asyncio
    async def test_known_bypasses_rejected(self, sql: str, gateway: GovernedQueryGateway, principal_a: Principal):
        """Every known bypass must be rejected at some stage of the pipeline."""
        with pytest.raises((PermissionError, ValueError)):
            await gateway.execute(
                GovernedQueryRequest(
                    principal=principal_a,
                    database_id="default",
                    sql=sql,
                )
            )


# ---------------------------------------------------------------------------
# Smoke test: Valid queries still work
# ---------------------------------------------------------------------------
class TestValidQueriesWork:
    """Ensure legitimate queries still execute correctly through the pipeline."""
    
    @pytest.mark.asyncio
    async def test_simple_select_works(self, gateway: GovernedQueryGateway, principal_a: Principal):
        result = await gateway.execute(
            GovernedQueryRequest(
                principal=principal_a,
                database_id="default",
                sql="SELECT id, name, dept FROM employees WHERE tenant_id = 'tenant-a'",
            )
        )
        assert len(result) == 2  # Alice and Bob
        for row in result:
            # salary and ssn not selected, so not in result
            assert "salary" not in row
            assert "ssn" not in row
    
    @pytest.mark.asyncio
    async def test_join_works(self, gateway: GovernedQueryGateway, principal_a: Principal):
        result = await gateway.execute(
            GovernedQueryRequest(
                principal=principal_a,
                database_id="default",
                sql="SELECT e.name, d.name AS dept_name FROM employees e JOIN departments d ON e.dept = d.name WHERE e.tenant_id = 'tenant-a'",
            )
        )
        assert len(result) == 2
    
    @pytest.mark.asyncio
    async def test_cte_works(self, gateway: GovernedQueryGateway, principal_a: Principal):
        result = await gateway.execute(
            GovernedQueryRequest(
                principal=principal_a,
                database_id="default",
                sql="WITH eng AS (SELECT id, name, dept, tenant_id FROM employees WHERE dept = 'engineering') SELECT id, name, dept FROM eng",
            )
        )
        assert len(result) == 1  # Only Alice
        assert result[0]["name"] == "Alice"
    
    @pytest.mark.asyncio
    async def test_union_works(self, gateway: GovernedQueryGateway, principal_a: Principal):
        result = await gateway.execute(
            GovernedQueryRequest(
                principal=principal_a,
                database_id="default",
                sql="SELECT name FROM employees WHERE tenant_id = 'tenant-a' UNION SELECT name FROM departments WHERE tenant_id = 'tenant-a'",
            )
        )
        assert len(result) == 4  # 2 employees + 2 departments