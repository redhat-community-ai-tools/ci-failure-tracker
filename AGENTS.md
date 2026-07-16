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

8. **False-positive testing.** When writing pattern-matching logic (regex,
   string matching, classifiers), always include negative test cases that
   verify similar-but-incorrect inputs are NOT matched. For pre-classifiers
   that skip AI analysis, test that non-matching failure messages still fall
   through to AI.

9. **Config safety.** Changes to `config.yaml` must be backwards-compatible.
   New keys must have defaults. Never rename or remove existing keys.
   Domain-specific matching patterns (regex for log parsing, step name
   lists, URL templates) must be defined in `config.yaml` with built-in
   defaults in the code, so that format changes can be addressed via
   config updates rather than code changes.

10. **Config job-name verification.** When adding or modifying job names in
    `config.yaml`, first run `git log -p -- dashboard/config.yaml` to see
    how previous version entries were added. Use the most recent version-add
    commit as a template for job names, counts, and suffixes. Pay attention
    to correction commits (e.g., suffix changes, removed entries) as they
    indicate common pitfalls. Then compare each new entry against
    corresponding entries for adjacent versions in the current file. Do not
    assume uniform naming across all platforms for a given version. If an
    issue claims a naming change for a version, verify which specific
    platforms are affected by comparing the proposed names against the
    pattern used by neighboring versions. Flag any deviations in the commit
    message.

11. **Collector interface.** New collectors must implement the full `BaseCollector`
   ABC from `dashboard/src/collectors/base.py`.

12. **Security.** No hardcoded credentials. Use environment variables for secrets.
    Use parameterized SQLite queries.

13. **Filter parameter flow.** When adding or modifying collector methods that
    accept filtering parameters (date ranges, version lists, platform lists),
    verify every filter parameter is either (a) used in a conditional check
    within the method body, or (b) forwarded to a callee that applies it. Do
    not add filter parameters to method signatures without implementing or
    forwarding the filter logic.

14. **Template-embedded JavaScript testing.** String-presence assertions
    (e.g., checking that a function name appears in rendered HTML) are not
    sufficient tests for JavaScript logic embedded in Jinja templates.
    Tests must verify structural correctness: that cache-check logic
    precedes network calls, that invalidation is called before refresh,
    that error paths are handled. If the JS logic is complex enough to
    require tests, consider extracting it into a separate `.js` file
    that can be tested independently.
