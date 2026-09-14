# Prompt Changelog

Versioning follows semantic versioning (MAJOR.MINOR.PATCH). Version is implied by Git (commits and tags).

## text_to_sql

- **v1.0.0** (initial): System and user prompts for text-to-SQL generation (Postgres, table-augmented style). Stored in `prompts/text_to_sql/` with file-based registry.
- **v1.1.0**: Prompt v2 — explicitly require exactly one SELECT statement (aligns with the single-statement safety rule), allow scalar subqueries, add multi-part question example answered via scalar subquery.
- **v1.2.0**: Prompt v3 — strict aliasing invariants: every alias must be declared and used consistently; scalar subqueries must declare their own alias. Added a self-check section, a new multi-part example (artist longest-tracks + latest-year), fixed string mismatch in the record-label example, and added a COUNT-over-joined-table guideline.
