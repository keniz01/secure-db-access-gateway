---
description: Commit the current work on a fresh branch off main, open a PR, and auto-merge to main once all CI checks pass — fixing failures and retrying.
---

Turn the uncommitted work in this repo into a clean, reviewable PR and land it on main. Do exactly this, in order, and stop with a clear message if any step cannot be completed safely.

`$ARGUMENTS` optionally contains the PR title (e.g. `/ship-this AI text-to-sql improvements`). If empty, infer a concise title from the changed files and areas.

## Steps

1. **Preconditions**
   - Verify `gh auth status` shows a logged-in account and `git remote get-url origin` exists. Refuse if either is missing.
   - Verify there are uncommitted changes (`git status --porcelain` non-empty). Refuse with a hint if the tree is clean.
   - Never commit files: `.env` / `gateway.env` (gitignored real secrets), anything under `certs/` or a legacy `secrets/`, nothing that looks like a credential.

2. **Fetch and branch off main**
   - `git fetch origin main` (use a generous timeout if the network is slow).
   - Determine a branch name from the PR title: strip a leading `feat:`/`fix:`/`chore:` prefix and slugify lowercase with dashes, prefixed with the matching category (default `feat/`). Example: title "AI text-to-sql improvements" -> `feat/ai-text-to-sql-improvements`. Use robust bash (avoid multi-line grep bug):
     ```bash
     LOWER=$(echo "$PR_TITLE" | tr '[:upper:]' '[:lower:]')
     if echo "$LOWER" | grep -q "^feat:"; then PREFIX="feat"; elif echo "$LOWER" | grep -q "^fix:"; then PREFIX="fix"; elif echo "$LOWER" | grep -q "^chore:"; then PREFIX="chore"; else PREFIX="feat"; fi
     SLUG=$(echo "$PR_TITLE" | sed -E 's/^[[:space:]]*(feat|fix|chore):[[:space:]]*//I' | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-|-$//g')
     BRANCH="${PREFIX}/${SLUG}"
     ```
   - `git switch -c <branch> origin/main` so the PR contains exactly this work and nothing from the currently checked-out branch. Uncommitted changes carry over automatically — do not stash or commit first.

3. **Quick verification (hermetic, fast)**
   - Run the quick test suites for the services that have changed files. These are hermetic and fast — use `workdir` param to run from inside each service dir (do NOT run `sql_query_api/.venv/bin/python` from repo root):
     - `workdir=sql_query_api .venv/bin/python -m pytest -q`
     - `workdir=auth0_api .venv/bin/python -m pytest -q`
     - `web-app`: `npm run lint` then `npm test` if web files changed
   - Also validate composition and policy when those areas changed:
     - `docker compose config > /dev/null` (and `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` if `docker-compose.*` changed)
     - `opa fmt --fail` / `opa test ./sql_query_api/opa/policies` if `opa/` changed
   - If any fail, stop and report the failures — do not commit.

4. **Stage and commit**
   - `git add -A`.
   - Review `git diff --cached --stat` and confirm only intended files are staged. Verify no `.env`/`certs/`/`secrets/` leaked (`git diff --cached --name-only | grep -E "^\.env$|^certs/|^secrets/"` must be empty).
   - Commit with a conventional message (`feat:`/`fix:`/`chore:` prefix + short summary) matching the repo style.
   - **Important**: the pre-commit hook runs the opencode code-review gate and can take 60-120s (plus retries). Give the commit command a tool timeout of **no less than 600000 ms** and do NOT skip or work around the hook. If the commit times out after 400000 ms, retry once with 600000 ms. If the review reports `REVIEW_VERDICT: REQUEST_CHANGES` with `REQUIRED_FIXES`, fix those before pushing - do not push with blocking findings. If `APPROVE` or only `SUGGESTED`, proceed.

5. **Push**
   - `git push -u origin <branch>`.

6. **Open the PR to main**
   - `gh pr create --base main --head <branch> --title "<PR title>" --body "<summary>"`
   - Write a short body: a one-line summary, the reasoning, and a bullet list of the main file changes grouped by area (e.g. auth0_api prompts/service, sql_query_api guardrail). Do not include secrets or connection strings.
   - If a PR already exists for this branch, resume at step 7 instead of creating a duplicate.

7. **Auto-merge once CI is green**
   - Confirm the PR and wait on its checks: `gh pr checks <branch> --watch --interval 30`. This blocks until every check finishes and exits 0 only if all passed, non-zero if any failed. Give it a large timeout (no less than 1200000 ms) — this repo's CI runs SQL/Auth0 pytest, web lint + typecheck + Playwright e2e, and a build.
   - Exit 0 → all CI passed. Merge now: `gh pr merge <branch> --squash --delete-branch`. The squash commit uses the PR title (default) — do not invent a subject.
   - Non-zero exit → a check failed. Do **not** merge. Go to step 8.
   - Guardrail: only merge when every CI check passes. Never merge with failing checks, never force-merge (`--admin`), never merge without polling. If `gh pr merge` fails for another reason (protected branch, required reviews), stop and report — do not work around it.

8. **Fix CI failures and retry (up to 3 fix cycles)**
   - List the failing checks: `gh pr checks <branch>`; for detail, `gh run list` and `gh run view <id> --log-failed`.
   - Reproduce and fix the failure locally:
     - SQL/Auth0 Python failures → run the hermetic suites from inside each service dir (`workdir=sql_query_api .venv/bin/python -m pytest -q`, `workdir=auth0_api .venv/bin/python -m pytest -q`).
     - Web failures → `npm run lint`, then `npm test` (typecheck). If the failure is only in Playwright e2e, try `npm run test:e2e` when browsers are installed, otherwise read the `gh run view` logs.
     - Diagnose the root cause and fix it at the source. Never weaken the governed query pipeline, security gates, or tests to turn a check green.
   - Re-run the applicable quick verification (including `docker compose config` if compose changed), then commit with a conventional message (`fix(ci):`, `fix(<area>):`, …). The pre-commit review hook runs again — keep timeouts **>= 600000 ms**, don't skip it. If commit times out, retry once with larger timeout.
   - Push, then re-run `gh pr checks <branch> --watch --interval 30`. If green, merge per step 7. If still red and this was fewer than 3 fix cycles, repeat from the top of this step.
   - After 3 failed fix cycles: stop. Print the remaining failing checks, summarize what was tried, and hand off to the user. Do not merge and do not bypass CI.

9. **Report**
   - If merged: print the squash commit SHA and confirm the branch was deleted.
   - If still open: print the PR URL and the reason (e.g. "CI still red after 3 fix cycles", "branch protection requires a review").
   - Confirm the branch name. After a successful merge, return to a clean state: `git fetch origin main` and `git switch -c main origin/main` (or `git switch main` if it already exists), then drop the merged feature branch locally if `--delete-branch` could not (e.g. `git branch -D <branch>`).