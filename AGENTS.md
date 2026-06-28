# Agent Instructions -- CI Failure Tracker

These instructions apply to all fullsend agents operating on this repository.

## Project Context

This repository contains two tools for tracking CI test failures:

1. **Dashboard** (`dashboard/`) -- Flask web app for test health visualization
2. **Jira Bridge** (`ci_failure_tracker.py`) -- automated Jira ticket creation

Most agent work targets the dashboard.

## Rules

1. **Think before acting.** State your assumptions explicitly before making changes.
   If something is unclear, check the code rather than guessing.

2. **Simplicity first.** Make the smallest change that solves the problem. Do not
   refactor adjacent code, add abstractions, or "improve" things the issue does
   not authorize.

3. **Surgical changes.** Only modify files directly related to the issue. If you
   discover unrelated problems, note them but do not fix them.

4. **Commit message format.** Use Conventional Commits:
   - `fix(collector): handle empty JUnit XML`
   - `feat(dashboard): add version dropdown filter`
   - `docs: update deployment guide`

5. **No attribution.** Do not add Co-Authored-By lines, AI signatures, or any
   mention of Claude, Anthropic, or AI assistance in commits, PRs, or comments.

6. **Python conventions.** Follow PEP 8 and match the existing code style. Do
   not add type hints to files that do not already use them.

7. **Testing.** Run `cd dashboard && python -m pytest -v` after changes. If no
   tests exist for the changed module, create a test file.

8. **Config safety.** Changes to `config.yaml` must be backwards-compatible.
   New keys must have defaults. Never rename or remove existing keys.

9. **Collector interface.** New collectors must implement the full `BaseCollector`
   ABC from `dashboard/src/collectors/base.py`.

10. **Security.** No hardcoded credentials. Use environment variables for secrets.
    Use parameterized SQLite queries.
