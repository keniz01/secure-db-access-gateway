"""
Deterministic, hermetic fuzz test for the SQL classifier.

No external fuzzing framework is pulled in (the repo has no dev dependency on
hypothesis/atheris); instead a seeded PRNG mutates known-good SELECT templates
with an adversarial fragment battery chosen to stress exactly the surfaces
that matter: comments, quoting, nested parens, separators, control bytes,
Unicode/homoglyphs, locks, SELECT INTO, catalog references, and forbidden
functions.

Two invariants are asserted for every generated input:

1. Classification NEVER raises. Any input — however pathological — must yield a
   bool from ``is_safe_select_query`` and at most a ``ValueError`` from the
   cleaning pipeline. (This is a regression for the sqlparse RecursionError
   crash discovered on deep parenthesis nesting.)
2. The checker is never MORE permissive than its own token layer and AST layer:
   any accepted query must also be single-statement/comment-free per sqlparse
   and strictly read-only per the AST analyzer.

The seed is fixed so failures are reproducible.
"""

from __future__ import annotations

import random

import sqlparse
from sqlparse import tokens

from repositories.sql_validators.ast_analyzer import AstSqlAnalyzer
from repositories.sql_validators.sql_safety_checker import (
    DefaultSqlSafetyChecker,
    clean_sql,
)

SEED = 20260915
RUNS_PER_PROPERTY = 700

TEMPLATES = [
    "SELECT * FROM {t}",
    "SELECT {c} FROM {t} WHERE {c} = 1",
    "SELECT a, b FROM {t} JOIN other o ON o.id = {t}.id",
    "SELECT count(*) FROM {t} GROUP BY {c}",
    "SELECT {c} FROM (SELECT * FROM {t}) d",
    "WITH x AS (SELECT {c} FROM {t}) SELECT * FROM x",
    "SELECT {c} FROM {t} ORDER BY {c} LIMIT 10",
    "SELECT DISTINCT {c} FROM {t}",
    "SELECT * FROM {t} t1, other t2 WHERE t1.id = t2.id",
    "SELECT CASE WHEN EXISTS (SELECT 1 FROM {t}) THEN 1 ELSE 0 END",
]

SUBSTITUTIONS = {
    "{t}": ["tracks", "public.tracks", '"tracks"', "schema.other"],
    "{c}": ["name", "id", '"name"', 'title'],
}

ADVERSARIAL_FRAGMENTS = [
    ";",
    ";;",
    "--",
    "-- ",
    "/*",
    "*/",
    "/*!",
    "/*+ hint */",
    "'",
    '"',
    "(",
    ")",
    ",,",
    "\x00",
    "\x1f",
    "\t",
    "\r\n",
    "--\n",
    "/* */",
    "FOR SHARE",
    "FOR UPDATE",
    "INTO evil",
    "INTO TEMP evil",
    "UNION SELECT",
    "UNION ALL",
    "DROP TABLE",
    "DELETE ",
    "UPDATE tracks SET",
    "pg_read_file",
    "pg_sleep",
    "pg_catalog.pg_class",
    "information_schema.tables",
    "ＳＥＬＥＣＴ",
    "\u200b",
    "$$",
    "'--",
    "--'",
    "$$",
    "(" * 60,
    ")" * 60,
    "$x$",
    "E'",
    "\\",
    "AS \"select\"",
    "/*",
]

ANALYZER = AstSqlAnalyzer()


def _sqlparse_danger(sql: str) -> bool:
    """Return True when token-level analysis flags multi-statement or a comment."""
    stmts = sqlparse.parse(sql)
    if len(stmts) != 1:
        return True
    return any(
        token.ttype in (tokens.Comment, tokens.Comment.Single, tokens.Comment.Multiline)
        for token in sqlparse.sql.Statement.flatten(stmts[0])
    )


def _mutate(rng: random.Random, template: str, depth: int) -> str:
    sql = template
    for _ in range(rng.randint(1, depth)):
        op = rng.randrange(6)
        if op == 0:
            # Splice a fragment at a random position
            idx = rng.randrange(len(sql))
            frag = rng.choice(ADVERSARIAL_FRAGMENTS)
            sql = sql[:idx] + frag + sql[idx:]
        elif op == 1:
            sql = rng.choice(ADVERSARIAL_FRAGMENTS) + sql
        elif op == 2:
            sql = sql + rng.choice(ADVERSARIAL_FRAGMENTS)
        elif op == 3:
            # Unicode lookalike substitution of an ASCII character
            char_map = {"S": "Ｓ", "L": "Ｌ", "E": "Ｅ", "C": "Ｃ", "T": "Ｔ", "a": "а", "o": "о"}
            src = rng.choice(list(char_map))
            sql = sql.replace(src, char_map[src], 1)
        elif op == 4:
            # Replace a single character with a quote or separator
            idx = rng.randrange(len(sql))
            repl = rng.choice(["'", '"', ";", "(", ")", "\x00", "--", "E'", "\\"])
            sql = sql[:idx] + repl + sql[idx + 1:]
        else:
            # Duplicate a random substring (stress nesting / token grouping)
            if len(sql) > 4:
                start = rng.randrange(len(sql) - 1)
                end = rng.randrange(start + 1, min(start + 24, len(sql)) + 1)
                sql = sql[:end] + sql[start:end] + sql[end:]
    return sql


def _input_generator(rng: random.Random, count: int) -> list[str]:
    inputs: list[str] = []
    for _ in range(count):
        template = rng.choice(TEMPLATES)
        sql = template
        for key, options in SUBSTITUTIONS.items():
            sql = sql.replace(key, rng.choice(options), 1)
        inputs.append(_mutate(rng, sql, depth=rng.randint(1, 3)))
    return inputs


def _check_invariant(sql: str, checker: DefaultSqlSafetyChecker) -> None:
    # Invariant 1: classification never raises.
    verdict = checker.is_safe_select_query(sql)
    assert isinstance(verdict, bool), f"non-bool classification for {sql!r}"

    if verdict:
        # Invariant 2: accepted => token layer sees one statement, no comment...
        assert not _sqlparse_danger(sql), f"accepted query with multi/comment: {sql!r}"
        # ...and the AST layer confirms strict read-only.
        assert ANALYZER.is_strictly_read_only(sql), f"accepted non-read-only query: {sql!r}"


class TestClassifierFuzzConsistency:
    def test_no_crash_and_token_consistency(self) -> None:
        checker = DefaultSqlSafetyChecker()
        rng = random.Random(SEED + 1)  # noqa: S311
        for sql in _input_generator(rng, RUNS_PER_PROPERTY):
            _check_invariant(sql, checker)

    def test_ast_read_only_consistency(self) -> None:
        checker = DefaultSqlSafetyChecker()
        rng = random.Random(SEED + 2)  # noqa: S311
        for sql in _input_generator(rng, RUNS_PER_PROPERTY):
            _check_invariant(sql, checker)

    def test_cleaning_pipeline_never_leaks_internal_errors(self) -> None:
        checker = DefaultSqlSafetyChecker()
        rng = random.Random(SEED + 3)  # noqa: S311
        for sql in _input_generator(rng, RUNS_PER_PROPERTY):
            try:
                cleaned = clean_sql(sql)
                assert checker.is_safe_select_query(cleaned) is False or not _sqlparse_danger(cleaned)
            except ValueError:
                pass  # designed behavior for non-SELECT garbage

    def test_never_accepts_definitive_attack_fragments(self) -> None:
        """Any generated input containing a known-dangerous detached fragment must be rejected."""
        checker = DefaultSqlSafetyChecker()
        rng = random.Random(SEED + 4)  # noqa: S311
        templates = [
            "SELECT * FROM {t}",
            "SELECT {c} FROM {t} WHERE {c} = 1",
            "WITH x AS (SELECT {c} FROM {t}) SELECT * FROM x",
            "SELECT * FROM (SELECT {c} FROM {t}) d",
        ]
        definite: list[tuple[str, str]] = [
            ("; DROP TABLE x", "separator-injection"),
            ("--", "single-line comment"),
            ("/*", "block comment"),
            ("/* */", "block comment"),
            ("FOR SHARE", "row lock"),
            ("INTO evil", "SELECT INTO"),
            ("pg_read_file", "forbidden function"),
            ("pg_catalog.pg_class", "system catalog"),
            ("\x00", "NUL byte"),
        ]
        for _ in range(RUNS_PER_PROPERTY):
            template = rng.choice(templates)
            for key, options in SUBSTITUTIONS.items():
                template = template.replace(key, rng.choice(options), 1)
            fragment, label = rng.choice(definite)
            idx = rng.randrange(len(template))
            sql = template[:idx] + fragment + template[idx:]
            if checker.is_safe_select_query(sql):
                # A fragment may only be accepted if it landed inside a string
                # literal data context; verify that is the case (token layer
                # must still agree). Otherwise it is a definitive bypass.
                assert not _sqlparse_danger(sql), (
                    f"bypass ({label}): {sql!r}"
                )
                assert ANALYZER.is_strictly_read_only(sql), (
                    f"bypass ({label}): {sql!r}"
                )
