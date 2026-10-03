---
name: code-review
description: Instructions for reviewing pending changes (staged or unstaged diff) for security, correctness, and repo conventions before a commit is landed.
---

# Code Review

Review the pending changes and report findings. **Read-only:** never modify files, never stage/unstage, never commit. You may run read-only commands and, where permitted, project test/lint commands to verify behavior. Run each shell command as one direct invocation from the repository root. Never prefix it with `cd`, chain commands, pipe/redirect output, or wrap it in another shell or script. If denied, do not broaden permissions or retry in a wrapper.

## Starting point

- Run these commands separately: `git status --short`, `git diff --cached --name-only`, `git diff --cached --unified=3`, and `git log -5 --oneline`. The first three are the authoritative scope and content of the staged changes.
- If `git diff --cached` is empty: report that there is nothing to review and APPROVE.
- Use a separate `git show :path/to/file` command to inspect a staged file's full content when the diff context is insufficient.
- Check the repo's AGENTS.md for conventions before finalizing a verdict.

## Repository-specific checks (secure-db-access-gateway)

- **Secrets:** flag any hardcoded credential, API key, password, or connection string. Secrets come only from env vars or `*_FILE` secret paths.
- **Auth:** tokens are httpOnly-cookie JWTs; `localStorage` may only hold the `app_jwt_exists` flag. Flag any new JS-visible token storage or weakened auth.
- **Governed SQL only:** `sql_query_api` is strictly SELECT-only. Every query path must go through the governed pipeline (safety/AST validation, tenant resolution, auto-LIMIT, read-only transaction flags, masking, audit) in `services/query_gateway.py` and `middlewares/rbac_middleware.py`. Flag any bypass, new ungoverned path, or DML support.
- **Tenant trust:** tokens must carry the trusted tenant claim `https://app.secure-db-access-gateway.org/tenant_id`; flag anything trusting client-supplied org/database IDs or headers.
- **Ruff hygiene:** new and edited lines in `sql_query_api` must be ruff-compliant (`sql_query_api/.venv/bin/ruff check <changed files>`). The whole repo is not ruff-clean historically — only judge the lines this diff touches.
- **Tests:** if the change touches behavior, run the relevant suite when permitted (SQL: `sql_query_api/.venv/bin/python -m pytest -q`; Web: `npm run lint && npm test` in `web-app/`).

## General checks

- Correctness: logic errors, race conditions, resource leaks (unclosed connections/engines), swallowed exceptions, missing `raise ... from` chaining.
- Type/annotation completeness in edited code where the project enforces ANN rules.
- Diff hygiene: debug prints, TODO markers, dead code, unrelated churn, accidental new untracked artifacts.
- Concurrency and lifecycle correctness in context managers and middleware.

## Output

End with an explicit verdict line and fix lists:

- Clean review → `REVIEW_VERDICT: APPROVE`
- Issues found → `REVIEW_VERDICT: REQUEST_CHANGES`

Then a `REQUIRED_FIXES:` block listing each blocking finding with `file:line` references (concise, actionable), and a `SUGGESTED:` block for non-blocking improvements. Be precise and minimal — only report real, actionable findings, never style nitpicks that are outside the diff's lines.
