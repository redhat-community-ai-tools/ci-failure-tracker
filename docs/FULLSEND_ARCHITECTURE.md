# CI Dashboard Tracker -- Fullsend Agent Architecture

## Overview

This document describes the architecture for agent-powered SDLC of the
CI Dashboard Tracker using [fullsend](https://github.com/fullsend-ai/fullsend).
Agents handle issue triage, code implementation, PR review, fix iteration,
retrospectives, DevOps maintenance, and a self-healing loop where the
dashboard's own failure detection feeds issues back into the development
process.

## System Landscape

```
+-------------------------------+       +-------------------------------+
|   CI Infrastructure           |       |   Issue Sources               |
|   (Prow, GCS, ReportPortal)   |       |   (Users, Teams, Dashboard)   |
+---------------+---------------+       +---------------+---------------+
                |                                       |
                v                                       v
+-------------------------------+       +-------------------------------+
|   CI Dashboard Tracker        |       |   GitHub Issues               |
|   (Flask / SQLite / Vertex)   |       |   (ci-failure-tracker repo)   |
|                               |       |                               |
|   Collectors --> DB --> UI    |       |   bug_report                  |
|                    |          |       |   feature_request             |
|              AI Analyzer      |       |   adoption_support            |
|                    |          |       |   ci_failure_report            |
|              Jira Bridge      |       +---------------+---------------+
+---------------+---------------+                       |
                |                                       |
                v                                       v
+-------------------------------+       +-------------------------------+
|   Jira (issues.redhat.com)    |       |   Fullsend Agent Pipeline     |
|                               |       |                               |
|   Product bugs filed by       |       |   Triage --> Code --> Review   |
|   dashboard + agents          |       |              ^          |     |
|                               |       |              |          v     |
+-------------------------------+       |              +--- Fix <--+    |
                                        |                               |
                                        |   Retro (post-merge)          |
                                        +-------------------------------+
```

## Repositories

| Repo | Role | GitHub |
|------|------|--------|
| `ci-failure-tracker` | POC/Staging -- actively running, fullsend enrolled first | `rrasouli/ci-failure-tracker` |
| `ci-dashboard-tracker` | Vanilla -- clean upstream for team adoption | `redhat-community-ai-tools/ci-dashboard-tracker` |
| `fullsend` | Agent SDLC platform -- provides agent runtime, workflows, policies | `fullsend-ai/fullsend` |

## Enrollment Model

Uses fullsend's **per-repo installation mode** (ADR-0033). No org-wide
`.fullsend` config repo needed.

```
ci-failure-tracker/
  .fullsend/
    config.yaml                    <-- Per-repo config (roles, version)
    customized/
      agents/                      <-- Dashboard-specific agent prompts
        code.md, triage.md,
        review.md, fix.md
      harness/                     <-- Python/Flask-adapted harness configs
        code.yaml, triage.yaml,
        review.yaml, fix.yaml
      skills/                      <-- Domain knowledge for agents
        dashboard-context/
        collector-patterns/
        config-validation/
      policies/                    <-- Sandbox network policies
        code.yaml, triage.yaml
      env/                         <-- Python environment (replaces Go)
        python-agent.env
      plugins/                     <-- Python LSP (replaces gopls)
        pylsp/
  .github/
    workflows/
      fullsend.yaml                <-- Shim workflow routing events
  AGENTS.md                        <-- Repo-level agent instructions
```

### Event Flow

```
GitHub Event (issue opened, PR created, comment posted)
       |
       v
.github/workflows/fullsend.yaml   (shim -- routes event)
       |
       v
fullsend-ai/fullsend reusable-dispatch.yml   (determines stage)
       |
       v
reusable-{stage}.yml   (triage | code | review | fix | retro)
       |
       v
OpenShell Sandbox   (isolated container with agent runtime)
       |
       +-- Reads: .fullsend/customized/harness/{stage}.yaml
       +-- Loads: .fullsend/customized/agents/{stage}.md
       +-- Applies: .fullsend/customized/policies/{stage}.yaml
       +-- Sources: .fullsend/customized/env/python-agent.env
       +-- Activates: .fullsend/customized/skills/*
       |
       v
Agent executes (Claude via Vertex AI)
       |
       v
post_script.sh   (push branch, create PR, post comments)
```

### Slash Commands

Users trigger agents via issue/PR comments:

| Command | Triggers | Context |
|---------|----------|---------|
| `/fs-triage` | Triage agent | On issues |
| `/fs-code` | Code agent | On issues (no existing PR) |
| `/fs-review` | Review agent | On PRs |
| `/fs-fix` | Fix agent | On PRs (after review feedback) |
| `/fs-fix-stop` | Disable fix agent | On PRs |

### Automatic Triggers

| Event | Agent | Condition |
|-------|-------|-----------|
| Issue opened | Triage | Always |
| Issue edited | Triage | Always |
| PR opened | Review | Always |
| PR synchronized | Review | Always |
| PR review submitted | Fix | When changes requested |
| PR merged | Retro | Always |

## Agent Pipeline

### 1. Triage Agent

```
Issue opened/edited
       |
       v
+------------------+
| Triage Agent     |
|                  |
| 1. Fetch issue   |
| 2. Read context  |
| 3. Classify type |
|    - bug         |
|    - collector   |
|    - config      |
|    - ai-analysis |
|    - adoption    |
|    - upstream-bug|
| 4. Search dupes  |
| 5. Check prereqs |
| 6. Assess info   |
| 7. Label + comment|
+------------------+
       |
       v
Structured triage result (JSON)
  action: sufficient | insufficient | duplicate | prerequisites
```

**Dashboard-specific triage:** The agent understands collector types,
config.yaml structure, and common failure modes via the `dashboard-context`
and `config-validation` skills.

### 2. Code Agent

```
/fs-code or triage marks "sufficient"
       |
       v
+------------------+
| Code Agent       |
|                  |
| 1. Read issue    |
| 2. Reproduce bug |
| 3. Plan fix      |
| 4. Implement     |
| 5. Run pytest    |
| 6. Commit        |
+------------------+
       |
       v
post-script pushes branch, creates PR
```

**Python-specific:** Uses `pylsp` for language intelligence, `python-agent.env`
for virtualenv setup, runs `pytest` for verification.

### 3. Review Agent

```
PR opened/synchronized
       |
       v
+------------------+
| Review Agent     |
|                  |
| Checks:         |
| - PEP 8         |
| - SQLite safety  |
| - Collector ABC  |
| - Config compat  |
| - Security       |
| - AI analyzer    |
+------------------+
       |
       v
Structured review result (JSON)
  action: approve | request-changes | comment | reject
  findings: [{severity, category, file, line, description}]
```

### 4. Fix Agent

```
Review requests changes
       |
       v
+------------------+
| Fix Agent        |
|                  |
| 1. Read review   |
| 2. Verify concern|
| 3. Implement fix |
| 4. Run pytest    |
| 5. Commit        |
+------------------+
       |
       v
post-script pushes, comments on PR
```

Iteration capped at 2 rounds. `/fs-fix-stop` disables.

## Self-Healing Loop

The dashboard detects CI failures and has AI analysis (Vertex AI / Claude).
This creates a feedback loop where detected failures become agent-managed
issues.

```
CI Infrastructure
       |
       v
Dashboard collects test results
       |
       v
AI Analyzer classifies failures
       |
       +-- automation_bug -----> Path A
       +-- product_bug --------> Path B
       +-- system_issue -------> Path C
       +-- transient ----------> Path C
```

### Path A: Automation Bugs (Self-Fix)

Dashboard code or test automation issues that agents can fix directly.

```
AI classifies as automation_bug
       |
       v
GET /api/actionable-failures
       |
       v
self-heal.yml (scheduled GitHub Action)
       |
       v
Creates GitHub Issue (label: automation_bug)
       |
       v
Fullsend triage agent validates
       |
       v
Code agent implements fix
       |
       v
Review agent reviews PR
       |
       v
Human approves and merges
```

### Path B: Product Bugs (Upstream Filing)

Real product bugs that need to be reported to upstream component owners.

```
AI classifies as product_bug
       |
       v
GET /api/actionable-failures
       |
       v
self-heal.yml (scheduled GitHub Action)
       |
       v
Creates GitHub Issue (label: upstream-bug)
       |
       v
Triage agent enriches:
  - Component ownership
  - Affected versions/platforms
  - Reproduction steps from logs
  - AI root cause analysis
       |
       v
Jira Bridge creates upstream bug
  - Project: component's Jira project
  - Fields: root_cause, evidence, suggested_action
  - Links back to dashboard
       |
       v
Dashboard tracks Jira ticket status
```

### Path C: Infrastructure / Flakes

Transient failures or infrastructure issues for investigation.

```
AI classifies as system_issue or transient
       |
       v
Frequency exceeds threshold?
  No --> Log and skip
  Yes --> Continue
       |
       v
Creates GitHub Issue (label: flake or infra)
       |
       v
Triage agent determines:
  - Known flake? --> Link to existing issue
  - New pattern? --> Escalate to DevOps agent
       |
       v
DevOps agent investigates
  - Checks platform-specific patterns
  - Reviews infrastructure health
  - Suggests mitigation
```

## Jira Integration Bridge

Fullsend's native Jira integration is not yet built (roadmap #2263-#2269).
A bridge connects the dashboard's existing Jira integration to fullsend's
GitHub-based workflow.

```
+-------------------+          +-------------------+
| Dashboard         |          | GitHub Issues      |
| (Jira Bridge)     |          | (ci-failure-tracker)|
|                   |          |                   |
| Creates Jira bug  +--------->| Mirror GH Issue   |
| via jira_client   |          | (refs Jira ticket)|
+--------+----------+          +---------+---------+
         |                               |
         v                               v
+-------------------+          +-------------------+
| Jira              |          | Fullsend Agents   |
| (issues.redhat.com)|         |                   |
|                   |          | Work on GH Issue  |
| Tracks upstream   |          | Implement fix     |
| product bugs      |          | Review PR         |
+-------------------+          +---------+---------+
                                         |
                                         v
                               Fix merged --> Comment on Jira
```

When fullsend ships native Jira support, the bridge is replaced with
direct agent-to-Jira operations.

## DevOps Agent

A scheduled agent that monitors the deployed dashboard health.

```
Daily cron (GitHub Actions)
       |
       v
+-------------------+
| DevOps Agent      |
|                   |
| Monitors:         |
| - Pod status      |
| - PVC usage       |
| - Route health    |
| - DB query perf   |
| - Collector health|
| - Data freshness  |
+-------------------+
       |
       v
Problem detected?
  No --> Silent pass
  Yes --> Create GitHub Issue
         (labels: devops, automated)
         |
         v
       Code agent can implement fix
```

## POC-to-Vanilla Sync

```
POC repo (ci-failure-tracker)           Vanilla repo (ci-dashboard-tracker)
       |                                        ^
       | PR merged with                         |
       | "upstream-candidate" label              |
       |                                        |
       v                                        |
upstream-sync.yml (GitHub Action)               |
       |                                        |
       | 1. Strips WINC-specific refs           |
       | 2. Opens PR in vanilla repo            |
       | 3. Fullsend triage validates           |
       |    team-agnostic                       |
       +----------------------------------------+
```

## Security Model

### Credential Isolation

| Credential | Scope | Where |
|------------|-------|-------|
| GH_TOKEN (sandbox) | Read-only (contents, issues, PRs) | Inside sandbox |
| PUSH_TOKEN (runner) | Write (push branches, create PRs) | Runner only, never in sandbox |
| REVIEW_TOKEN (runner) | Write (post review comments) | Runner only |
| GCP credentials | Vertex AI inference | Mounted as file in sandbox |
| Jira API token | Create/search issues | Dashboard env var, not in sandbox |

### Sandbox Isolation

- Agents run in OpenShell containers with L7 network policies
- Network restricted to: Vertex AI, GitHub API, PyPI (code agent only)
- `curl` excluded from allowed binaries (prevents disallowedTools bypass)
- `pull_request_target` prevents PR authors from modifying the shim workflow
- Config always read from base branch, not PR branch

### Agent Constraints

- Code/Fix agents cannot: push, create PRs, edit issues, post comments
- Triage agent cannot: modify code, push, create PRs
- Review agent: read-only, posts comments via post-script
- AGENTS.md and CODEOWNERS are human-owned (agents cannot modify guardrails)

## Infrastructure Requirements

### GitHub

- Fullsend GitHub Apps installed (triage, coder, review roles)
- Repo variables: `FULLSEND_MINT_URL`, `FULLSEND_GCP_REGION`
- Repo secrets: `FULLSEND_GCP_WIF_PROVIDER`, `FULLSEND_GCP_PROJECT_ID`

### Google Cloud Platform

- GCP project with Vertex AI API enabled
- Workload Identity Federation for OIDC token exchange
- Service account with `aiplatform.endpoints.predict` permission

### OpenShift (Dashboard Deployment)

- Namespace with BuildConfig, Deployment, Service, Route, PVC
- 256Mi-512Mi memory per pod
- 1Gi persistent volume for SQLite database
- OAuth proxy for authentication (optional)

## Rollout Plan

| Phase | What | Where |
|-------|------|-------|
| 1 | Enrollment + validation | POC repo (ci-failure-tracker) |
| 2 | Copy validated config | Vanilla repo (ci-dashboard-tracker) |
| 3 | Support + DevOps agents | POC first, then vanilla |
| 4 | Self-healing loop | POC (connected to live dashboard) |
| 5 | Jira bridge | POC (connected to issues.redhat.com) |
