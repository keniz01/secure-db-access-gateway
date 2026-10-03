---
description: Review the staged Git changes before committing (security, correctness, repo conventions).
agent: code-reviewer
---

Review the pending changes before commit. Do not invoke a skill-loading or
filesystem-read tool. The command starts at the repository root. Invoke each
permitted read-only shell command separately and exactly as listed; never add
`cd`, chain commands, pipe output, or wrap commands in another shell:

1. `git status --short`
2. `git diff --cached --name-only`
3. `git diff --cached --unified=3`
4. `git log -5 --oneline`

Use a separate `git show :path/to/file` command to inspect a staged file's full
content when the diff context is insufficient. If any shell command is denied,
do not retry it in a wrapper or broaden permissions. Review the changes and
report findings with a `REVIEW_VERDICT: APPROVE` or
`REVIEW_VERDICT: REQUEST_CHANGES` line plus `REQUIRED_FIXES:` and `SUGGESTED:`
blocks. You are read-only; do not modify anything.
