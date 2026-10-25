# CI Dashboard Tracker -- Fullsend Adoption Summary

**Date:** 2026-06-28
**Status:** Planning complete, enrollment files created, not yet activated
**Author:** Reza Rasouli

---

## Executive Summary

This document captures the complete effort to plan agent-powered SDLC for
the CI Dashboard Tracker using fullsend. It covers what was explored, what
was decided, what was built, what remains, risks, and open concerns.

The CI Dashboard Tracker monitors OpenShift CI test failures. The goal is
to enroll it in fullsend so AI agents can triage support tickets, implement
fixes, review PRs, maintain the deployment, and create a self-healing loop
where detected CI failures automatically feed back into the development
process -- including filing upstream Jira bugs.

---

## 1. Project Landscape

### Repositories

| Repo | Role | Location | GitHub |
|------|------|----------|--------|
| ci-failure-tracker | POC/Staging (active) | `/Users/rrasouli/Documents/GitHub/ci-failure-tracker` | `rrasouli/ci-failure-tracker` |
| ci-dashboard-tracker | Vanilla (upstream) | `/Users/rrasouli/Documents/GitHub/ci-dashboard-tracker` | `redhat-community-ai-tools/ci-dashboard-tracker` |
| fullsend | Agent SDLC platform | `/Users/rrasouli/Documents/GitHub/fullsend` | `fullsend-ai/fullsend` |

### CI Dashboard Tracker -- Tech Stack

- **Backend:** Python 3.10, Flask 3.1.3, Gunicorn, SQLite (WAL mode)
- **Frontend:** Vanilla JavaScript, single HTML template
- **AI:** Claude Sonnet 4 via Google Vertex AI (`anthropic[vertex]`)
- **Data Sources:** Pluggable collectors (ReportPortal, gcsweb, Prow GCS, Prow MCP)
- **Integrations:** Jira (issues.redhat.com), OpenPyXL exports
- **Deployment:** Docker, OpenShift (BuildConfig + Deployment + PVC)
- **Live URL:** `winc-dashboard-poc-winc-dashboard-poc.apps.build10.ci.devcluster.openshift.com`

### Fullsend Platform -- Key Capabilities

- 5 core agents: triage, code, review, fix, retro
- Per-repo enrollment mode (ADR-0033) -- no org-wide setup needed
- GitHub-native coordination (issues, PRs, labels, CODEOWNERS)
- Sandbox isolation (OpenShell containers with L7 network policies)
- Credential isolation (read-only tokens in sandbox, push tokens on runner only)
- Jira integration: on the roadmap (#2263-#2269) but not yet built

### Existing AI Agent

The POC dashboard already has an AI agent handling it (Claude Code for
development assistance). Fullsend would add a complementary layer of
automated agents operating through GitHub workflows, not replacing the
existing development workflow.

---

## 2. Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Enrollment order | POC first, then vanilla | Validate on live deployment before applying to upstream |
| Self-healing scope | Full (dashboard + collector + upstream bugs) | Maximize value: agents fix their own bugs AND file product bugs to Jira |
| Progress tracking | GitHub Issues in fullsend repo | Centralized tracking with `ci-dashboard` label |
| Installation mode | Per-repo (ADR-0033) | Single repo, no org-wide rollout needed |
| Agent model | Claude Opus via Vertex AI | Matches fullsend defaults |
| Auto-merge | Disabled | Human approval required for all agent PRs |

---

## 3. What Was Built

### Files Created in ci-failure-tracker (20 files, not yet committed)

**Enrollment config:**
- `.fullsend/config.yaml` -- per-repo config enabling triage, coder, review, fix, retro

**Agent harnesses (Python-adapted, replacing Go defaults):**
- `.fullsend/customized/harness/code.yaml` -- pylsp plugin, Python env, pytest verification
- `.fullsend/customized/harness/triage.yaml` -- dashboard-context and config-validation skills
- `.fullsend/customized/harness/review.yaml` -- Python/Flask review criteria
- `.fullsend/customized/harness/fix.yaml` -- Python env, dashboard-context skill

**Agent prompts (dashboard-specific):**
- `.fullsend/customized/agents/code.md` -- Python/Flask implementation specialist
- `.fullsend/customized/agents/triage.md` -- dashboard-aware issue classifier
- `.fullsend/customized/agents/review.md` -- Python code quality + collector compliance
- `.fullsend/customized/agents/fix.md` -- review feedback fixer

**Domain knowledge skills:**
- `.fullsend/customized/skills/dashboard-context/SKILL.md` -- project architecture
- `.fullsend/customized/skills/collector-patterns/SKILL.md` -- BaseCollector ABC guide
- `.fullsend/customized/skills/config-validation/SKILL.md` -- config.yaml validation rules

**Sandbox policies:**
- `.fullsend/customized/policies/code.yaml` -- PyPI access, Python binaries
- `.fullsend/customized/policies/triage.yaml` -- GitHub API + Vertex AI only

**Python environment:**
- `.fullsend/customized/env/python-agent.env` -- virtualenv, PYTHONDONTWRITEBYTECODE
- `.fullsend/customized/plugins/pylsp/plugin.json` -- Python Language Server
- `.fullsend/customized/plugins/pylsp/.lsp.json` -- LSP configuration

**GitHub workflow:**
- `.github/workflows/fullsend.yaml` -- shim routing events to fullsend dispatch

**Repo-level:**
- `AGENTS.md` -- agent instructions (conventions, commit format, no attribution)

**Architecture documentation:**
- `docs/FULLSEND_ARCHITECTURE.md` -- full system architecture with ASCII diagrams

---

## 4. Planned Phases (Not Yet Implemented)

### Phase 1: Adoption Readiness (Vanilla Repo)
- Issue templates (.github/ISSUE_TEMPLATE/)
- GitHub Actions CI (lint, pytest, Docker build)
- Makefile for common operations
- Team onboarding kit (example configs, adoption guide)
- Remove hardcoded WINC references from vanilla

### Phase 2: Fullsend Enrollment (This Document)
- [DONE] Per-repo enrollment files created
- [TODO] GitHub repo variables/secrets configuration
- [TODO] Fullsend GitHub Apps installation on repo
- [TODO] Validation: run agents on 5-10 real issues
- [TODO] Apply validated config to vanilla repo

### Phase 3: Support and DevOps Agents
- Extended triage for adoption support (teams configuring their own dashboards)
- DevOps agent for deployment health monitoring
- Self-healing loop with three paths:
  - automation_bug --> agent self-fix
  - product_bug --> Jira filing with AI analysis
  - system_issue/transient --> flake investigation

### Phase 4: POC-to-Vanilla Sync
- GitHub Action for automated upstream PR creation
- Label-driven sync (`upstream-candidate`)
- Agent validation of team-agnostic PRs

### Phase 5: Jira Integration Bridge
- Mirror GitHub Issues to Jira for upstream product bugs
- Replace with native fullsend Jira when available (#2263-#2269)

---

## 5. Risk Assessment

### Low Risk (GitHub-layer only)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Agent opens a bad PR | PR sits unmerged | auto_merge disabled; human review required |
| Agent mislabels an issue | Cosmetic | Labels can be corrected; triage can be re-run |
| Agent posts an incorrect comment | Noise on issue | Comments can be deleted |
| Shim workflow fails | No agent runs | Dashboard unaffected; fail-open |

**Why this is low risk:** Fullsend agents operate entirely at the GitHub
layer. They cannot access the OpenShift deployment, cannot push to
protected branches without review, and cannot modify their own guardrails
(AGENTS.md, CODEOWNERS are human-owned). The running dashboard on
`build10.ci.devcluster.openshift.com` is completely unaffected by agent
activity until a human merges an agent's PR.

### Medium Risk (Cost and Quota)

| Risk | Impact | Mitigation |
|------|--------|------------|
| GitHub Actions minute consumption | Quota usage | Monitor usage; set per-repo limits |
| Vertex AI inference costs | ~$0.02-0.10 per agent run | Budget alerts on GCP project |
| Agent loop (fix -> review -> fix cycle) | Wasted compute | Fix iteration capped at 2 rounds |
| Noisy agents on busy repos | Developer fatigue | Start with manual `/fs-*` commands only |

### Higher Risk (Future Phases Only)

| Risk | Impact | Mitigation | Phase |
|------|--------|------------|-------|
| DevOps agent modifies deployment | Could break running dashboard | Separate staging namespace | Phase 3 |
| Self-healing files wrong Jira bug | Noise in upstream project | Dedup logic + human review gate | Phase 3 |
| Auto-merge enabled prematurely | Bad code reaches production | Keep disabled until high confidence | Future |

---

## 6. Open Concerns

### Concern 1: Regression Risk on Working Environment

**Status:** Addressed by design.

The fullsend enrollment does not interact with the OpenShift deployment.
Agents work through GitHub (issues/PRs), not through `oc` commands or
deployment manifests. The dashboard continues running unchanged until a
human merges an agent-created PR.

For Phase 3 (DevOps agent), a **separate OpenShift namespace** is
recommended for testing agent-initiated deployment changes before applying
to the POC namespace:
```
winc-dashboard-poc           <-- Current POC (untouched by agents)
winc-dashboard-fullsend-test <-- New namespace for DevOps agent testing
```

### Concern 2: Coexistence with Existing AI Agent

The POC dashboard already has an AI agent (Claude Code) handling
development. Fullsend agents are complementary, not competing:

- **Claude Code (existing):** Interactive development assistant used by
  the developer in real-time
- **Fullsend agents (new):** Automated GitHub workflow agents that respond
  to events (issue opened, PR created) without developer involvement

They operate in different contexts and do not conflict. The developer
continues using Claude Code for interactive work; fullsend handles the
automated SDLC pipeline.

### Concern 3: Readiness for Implementation

Current blockers before activation:
1. Fullsend GitHub Apps must be installed on `rrasouli/ci-failure-tracker`
2. GCP project needs Workload Identity Federation configured
3. Repo variables/secrets must be set in GitHub Settings
4. The fullsend team should review the customized harness/agent files

**Recommendation:** Start with a dry run -- commit the `.fullsend/`
directory but do not install the GitHub Apps yet. This lets the files be
reviewed and iterated on before any agents activate.

### Concern 4: Cost Visibility

Each agent invocation costs:
- GitHub Actions: ~2-10 minutes of runner time per agent
- Vertex AI: ~$0.02-0.10 per inference call (Claude Opus)
- Estimated monthly: depends on issue volume; 20 issues/month ~ $5-10

Set up GCP budget alerts and GitHub Actions usage monitoring before
activating.

---

## 7. Recommended Next Steps

1. **Review created files** -- examine the `.fullsend/` directory and
   `AGENTS.md` in the POC repo before committing
2. **Dry run** -- commit files to a branch, review PR, but do not install
   GitHub Apps yet
3. **Set up infrastructure** -- GCP WIF, GitHub Apps, repo variables/secrets
4. **Manual validation** -- activate agents and test with `/fs-triage` on
   a few real issues
5. **Iterate** -- adjust agent prompts and skills based on triage quality
6. **Expand** -- once confident, apply to vanilla repo and enable
   automated triggers

---

## 8. Reference Links

- [Fullsend Architecture Doc](FULLSEND_ARCHITECTURE.md) -- detailed system diagrams
- [Multi-Team Architecture](MULTI_TEAM_ARCHITECTURE.md) -- team adoption model
- [Full Plan File](file:///Users/rrasouli/.claude/plans/joyful-churning-newt.md) -- original implementation plan
- [Fullsend ADR-0033](https://github.com/fullsend-ai/fullsend/blob/main/docs/ADRs/0033-per-repo-installation-mode.md) -- per-repo installation spec
- [Fullsend Jira Roadmap](https://github.com/fullsend-ai/fullsend/issues/2263) -- native Jira integration
