---
name: dashboard-context
description: >-
  Provides architectural context for the CI Dashboard Tracker project.
  Covers the Flask app structure, pluggable collector system, SQLite storage,
  AI analysis pipeline, Jira integration, and configuration model.
---

# CI Dashboard Tracker Architecture

## Overview

The CI Dashboard Tracker is a Python/Flask web application that monitors,
analyzes, and tracks CI test failures in OpenShift environments. It provides
real-time dashboards showing test failure metrics, historical trends, and
platform-specific statistics.

## Repository Layout

```
ci-failure-tracker/
  ci_failure_tracker.py          # Jira Bridge CLI (separate tool)
  teams/                         # Team-specific Jira Bridge configs
  examples/                      # Example configs for adopting teams
  dashboard/                     # Main dashboard application
    dashboard.py                 # CLI entry point (Click)
    wsgi.py                      # WSGI entry for Gunicorn
    config.yaml                  # Team-customizable configuration
    requirements.txt             # Python dependencies
    Dockerfile                   # Container image
    src/
      collectors/                # Pluggable data collectors
        base.py                  # BaseCollector ABC + data classes
        reportportal.py          # ReportPortal API collector
        gcsweb.py                # QE-Private-Deck GCS collector
        prow_gcs.py              # Direct GCS bucket collector
        prow_mcp.py              # MCP server collector
      storage/
        database.py              # SQLite schema, CRUD, migrations
      web/
        server.py                # Flask app, routes, background tasks
        templates/
          dashboard.html         # Single-page app (vanilla JS)
      ai/
        analyzer.py              # Vertex AI failure analysis (Claude)
      integrations/
        jira_integration.py      # Jira issue creation/search
      metrics/
        calculator.py            # Pass rate and trend calculations
      reports/
        weekly_report.py         # Weekly platform breakdown reports
    openshift/                   # OpenShift deployment manifests
```

## Tech Stack

- **Web Framework:** Flask 3.1.3 + Gunicorn (single worker)
- **Database:** SQLite with WAL mode for concurrency
- **Frontend:** Vanilla JavaScript in a single HTML template
- **AI:** Claude Sonnet 4 via Google Vertex AI (`anthropic[vertex]`)
- **HTTP Client:** requests 2.32.4
- **Config:** PyYAML 6.0
- **Export:** OpenPyXL 3.1.0 (XLSX), CSV, Markdown
- **CLI:** Click 8.1.0 + Rich 13.0.0

## Data Flow

1. User clicks "Refresh Data" or cron triggers collection
2. Configured collector fetches job runs and test results from CI
3. Results normalized to `TestResult`/`JobRun` dataclasses
4. Stored in SQLite (indexed for fast queries)
5. Flask serves dashboard with real-time metrics
6. User can trigger AI analysis per test failure
7. User can create Jira tickets from analyzed failures

## Database Schema

Five tables:
- `job_runs` -- job name, build ID, status, version, platform, pass rate
- `test_results` -- test name, status, error message, version, platform
- `daily_metrics` -- pre-aggregated daily pass rates
- `test_metrics` -- per-test aggregated statistics
- `ai_analyses` -- cached AI analysis results

## Configuration Model

`config.yaml` sections:
- `collector.type` -- which collector to use (reportportal|gcsweb|prow_gcs|prow_mcp)
- `tracking.versions` -- OpenShift versions to track
- `tracking.platforms` -- cloud platforms (aws, azure, gcp, vsphere, etc.)
- `tracking.test_suite_filter` -- filter tests by suite name
- `tracking.blocklist` -- test IDs to exclude
- `database.path` -- SQLite file location
- `web.host/port` -- server binding

## Key Design Patterns

- **Pluggable collectors:** All implement `BaseCollector` ABC with normalized data classes
- **Manual collection:** On-demand via UI button (no automatic scheduling by default)
- **Lazy AI analysis:** Users click to analyze (not automatic) to control costs
- **Shared analysis:** AI results are platform-agnostic, cached in DB
- **Config-driven:** Teams customize via `config.yaml`, no code changes needed
