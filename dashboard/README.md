# CI Test Pass Rate Dashboard

Web dashboard for tracking OpenShift CI test pass rates over time. Focuses on actual test failures (OCP-* tests) with inline log viewing.

**Live Dashboard:** https://winc-dashboard-winc-dashboard.apps.build10.ci.devcluster.openshift.com/

## Features

- **On-Demand Data Collection**: Manual refresh button to pull latest test results
- **Actual Test Logs**: Fetches real stdout/stderr from ReportPortal (not AI summaries)
- **View Logs in New Tab**: Click to open full test output in clean terminal-style page
- **OCP Test Focus**: Automatically filters to show only OCP-* tests (excludes infrastructure failures)
- **Historical Tracking**: SQLite database with 30 days of test history
- **Real-time Dashboard**: Interactive charts and test rankings
- **Report a Problem**: File GitHub issues directly from the dashboard UI
- **Key Metrics**:
  - Overall pass rate % over time
  - Per-test pass rates (identify flaky/failing tests)
  - Per-version trends (compare 4.21 vs 4.22)
  - Per-platform comparison (AWS, GCP, Azure, vSphere, Nutanix)

## Architecture

```
┌──────────────────────┐
│  ReportPortal API    │  ← Fetch test results + actual logs
└──────────┬───────────┘
           │ On-demand collection (manual refresh)
           ↓
┌──────────────────────┐
│  SQLite Database     │  ← Store 30 days history + logs
│  (Persistent Volume) │     WAL mode for concurrency
└──────────┬───────────┘
           │ Calculate metrics (OCP-* tests only)
           ↓
┌──────────────────────┐
│  Flask + Gunicorn    │  ← Web dashboard (1 worker)
│  OpenShift Route     │     https://winc-dashboard...
└──────────────────────┘
```

## Quick Start (Local Development)

### Installation

```bash
cd dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Edit `config.yaml` for your team:

```yaml
collector:
  type: "reportportal"
  reportportal:
    url: "https://reportportal-openshift.apps.dno.ocp-hub.prod.psi.redhat.com"
    project: "prow"
    job_patterns:
      - "periodic-ci-openshift-openshift-tests-private-release-{version}-*-winc-*"
      - "periodic-ci-openshift-windows-machine-config-operator-release-{version}-*"

tracking:
  versions: ["4.21", "4.22"]
  platforms: ["aws", "azure", "gcp", "nutanix"]
  test_suite_filter: "Windows_Containers"  # Filter to specific test suite
  lookback_days: 30
  blocklist:
    - "OCP-60944"  # Removed from suite
    - "OCP-66352"  # Not relevant

database:
  path: "/data/dashboard.db"  # Use /data for persistent volume
```

### Set Environment Variables

```bash
# Required for ReportPortal collector
export REPORTPORTAL_API_TOKEN="your-token-here"

# Required for Prow GCS collector (qe-private-deck)
export API_KEY="your-prow-token-here"
```

### Prow GCS Token Setup

The Prow GCS collector requires an OpenShift token from the l2s4 cluster to authenticate against qe-private-deck.

**How to retrieve the token:**

1. Open https://oauth-openshift.apps.ci.l2s4.p1.openshiftapps.com/oauth/token/request
2. Log in with your Red Hat SSO credentials
3. Click "Display Token"
4. Copy the token value

Note: `oc login` to the l2s4 cluster does not work (times out). The web UI method above is the only way to get the token.

**Set as environment variable (local):**
```bash
export API_KEY="sha256~your-token-value"
```

**Set as OpenShift secret (deployment):**
```bash
oc create secret generic prow-token --from-literal=token="sha256~your-token-value"
```

Then reference it in your deployment config as the `API_KEY` environment variable.

**Token renewal:** Tokens expire periodically. If data collection fails with 401/403 errors, repeat the steps above to get a new token. See [docs/token-renewal.md](docs/token-renewal.md) for details.

### Start Dashboard Locally

```bash
# Start web server on http://localhost:8080
./dashboard.py serve

# Or use Gunicorn (production-like)
gunicorn -w 1 -b 0.0.0.0:8080 wsgi:app
```

Visit http://localhost:8080 and click "Refresh Data" to collect test results.

## OpenShift Deployment

See [openshift/README.md](openshift/README.md) for full deployment guide.

**Quick deploy:**
```bash
# 1. Login to OpenShift
oc login https://api.build10.ci.devcluster.openshift.com:6443

# 2. Create project
oc new-project winc-dashboard

# 3. Create secrets
oc create secret generic reportportal-token --from-literal=token="YOUR_TOKEN"
oc create secret generic webhook-secret --from-literal=WebHookSecretKey=$(openssl rand -hex 20)

# 4. Deploy
oc apply -f openshift/
oc start-build winc-dashboard

# 5. Get URL
oc get route winc-dashboard -o jsonpath='{.spec.host}'
```

## Dashboard Features

### Main View

- **Summary Cards**: Average pass rate, total tests, trend indicators
- **Pass Rate Trend**: Line chart showing daily pass rates
- **Version Comparison**: Compare 4.21 vs 4.22 performance
- **Test Rankings**: Lowest performing tests with pass rates

### Test Logs

Each failing test shows a **"View logs"** link that:
- Opens in new tab
- Displays actual test stdout/stderr (fetched from ReportPortal API)
- Dark terminal-style formatting for readability
- Includes test name and description in title

**No more AI summaries!** Logs are the real test output.

### On-Demand Collection

- Click **"Refresh Data"** button to collect latest results
- Progress shown in blue banner
- Collection takes 2-3 minutes for 30 days of data
- Dashboard refreshes automatically when complete
- No scheduled CronJobs (due to cluster resource constraints)

### Filtering

- **Time Range**: 7/14/30/60/90 days
- **Version**: All, 4.21, 4.22
- **Automatic**: Only shows OCP-* tests (infrastructure failures filtered out)

### Report a Problem

Click the **"Report a Problem"** button in the dashboard header to file a GitHub issue directly from the UI.

**How it works:**

1. User clicks "Report a Problem" in the header
2. If GitHub OAuth is configured and user is not logged in, redirects to GitHub login first
3. A modal opens with Summary and Description fields
4. User fills in details and clicks Submit
5. Dashboard constructs a pre-filled GitHub issue URL and opens it in a new tab
6. User reviews the pre-filled issue on GitHub and clicks "Submit new issue"

The issue is created under the user's own GitHub account -- they are automatically subscribed to notifications on any follow-up comments or status changes.

**Issue format:**
- Title: `[Dashboard] <summary>`
- Body: `<description>` + "Reported via CI Dashboard" footer
- Label: `bug`
- Admin notifications: `/cc @user1 @user2` appended if `GITHUB_NOTIFY_USERS` is configured

**Configuration (`config.yaml`):**

```yaml
github:
  repo: "org/repo-name"
  notify_users: ["admin1", "admin2"]
```

| Key | Description |
|-----|-------------|
| `github.repo` | Target repo for issues (e.g. `redhat-community-ai-tools/ci-failure-tracker`) |
| `github.notify_users` | GitHub usernames to @mention on every new issue (admin notifications) |

Environment variables `GITHUB_REPO` and `GITHUB_NOTIFY_USERS` (comma-separated) override config.yaml values when set.

**GitHub OAuth (optional):**

When configured, the button shows "Log in to Report" for unauthenticated users and redirects to GitHub login before opening the modal. Without OAuth, the button works immediately (useful for deployments already behind SSO/oauth-proxy).

| Variable | Description |
|----------|-------------|
| `GITHUB_OAUTH_CLIENT_ID` | GitHub OAuth App client ID |
| `GITHUB_OAUTH_CLIENT_SECRET` | GitHub OAuth App client secret |
| `FLASK_SECRET_KEY` | Stable secret for session persistence across pod restarts |

**OAuth setup:**
1. Create a GitHub OAuth App at https://github.com/settings/developers
2. Set the callback URL to `https://<dashboard-url>/auth/github/callback`
3. Set `GITHUB_OAUTH_CLIENT_ID` and `GITHUB_OAUTH_CLIENT_SECRET` env vars
4. Set `FLASK_SECRET_KEY` to a stable random value

**OAuth endpoints:**
- `GET /auth/github/login` -- Redirects to GitHub authorization
- `GET /auth/github/callback` -- Handles OAuth callback
- `GET /auth/github/status` -- Returns JSON auth state
- `POST /auth/github/logout` -- Clears session

**Security:** CSRF protection via random state parameter. Access tokens stored server-side in a bounded token store (8h TTL, 1024 max entries) -- only a random token ID goes into the signed session cookie. OAuth scope: `public_repo`.

### API Endpoints

REST APIs for custom integrations:

- `GET /api/summary?days=30&version=4.21` - Summary statistics
- `GET /api/trend?days=30&version=4.21` - Pass rate trend
- `GET /api/test-pass-rates?days=30&version=4.21` - Per-test pass rates with logs
- `GET /api/version-comparison?days=30` - Compare versions
- `POST /api/trigger-collection` - Start data collection
- `GET /api/collection-status` - Check collection progress
- `GET /logs?content=<log>&test=<name>` - View logs page
- `GET /auth/github/login` - Start GitHub OAuth login flow
- `GET /auth/github/status` - GitHub OAuth auth state

### Test Suite Filtering

**New in April 2026**: The dashboard now supports filtering to specific test suites, making it reusable across teams.

Configure in `config.yaml`:

```yaml
tracking:
  test_suite_filter: "Windows_Containers"  # Filter to specific test suite
```

**How it works:**
- Checks if the filter string appears in the raw test name/description
- Applied before any test name processing
- Empty string or omit to collect all tests

**Examples for different teams:**

| Team                  | Filter Value           | What it collects                              |
|-----------------------|------------------------|----------------------------------------------|
| Windows Containers    | `"Windows_Containers"` | Only WINC tests                              |
| Networking            | `"Networking"`         | Only Networking tests                        |
| Storage               | `"Storage"`            | Only Storage tests                           |
| Security & Compliance | `"Security"`           | Security-related tests                       |
| All teams             | `""`                   | All tests (no filter)                        |

This prevents unrelated test suites (like Security_and_Compliance, File Integrity) from appearing in your dashboard.

## Database Schema

SQLite database (`/data/dashboard.db`) with WAL mode enabled:

- **`job_runs`**: Overall job statistics (36 runs per refresh)
- **`test_results`**: Individual test results with actual logs (~3000 results)
  - Includes `error_message` field with full stdout/stderr for failed tests
  - `log_url` for ReportPortal UI link (backup)
- **`daily_metrics`**: Pre-aggregated daily stats (not currently used)
- **`test_metrics`**: Per-test aggregated stats (not currently used)

**Key Query:**
```sql
SELECT test_name, pass_rate,
       (SELECT error_message FROM test_results tr2
        WHERE tr2.test_name = test_results.test_name
        AND tr2.status = 'failed'
        ORDER BY tr2.timestamp DESC LIMIT 1) as sample_error
FROM test_results
WHERE test_name LIKE 'OCP-%'
GROUP BY test_name, version
ORDER BY pass_rate ASC
```

## Data Collection

### ReportPortal Collector (Current)

Fetches test results and **actual logs** from ReportPortal API:

1. **Job Runs**: Query `/api/v1/prow/launch` for periodic WINC jobs
2. **Test Items**: Query `/api/v1/prow/item` for test steps (OCP-* tests)
3. **Logs**: For each failed test, fetch from `/api/v1/prow/log?filter.eq.item=<id>`
   - Combines all log messages (ERROR, INFO levels)
   - Stores in `error_message` field
   - Real stdout/stderr, not AI summaries

**Configuration:**
```yaml
collector:
  type: "reportportal"
  reportportal:
    url: "https://reportportal-openshift.apps.dno.ocp-hub.prod.psi.redhat.com"
    project: "prow"
    page_size: 150
    max_pages: 10
    max_workers: 5
```

### Future: Prow GCS Collector

Plan to switch to direct GCS access once authentication is configured:

```yaml
collector:
  type: "prow_gcs"
  prow_gcs:
    bucket: "origin-ci-test"
    job_names:
      - "periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-aws-winc-e2e"
    max_builds_per_job: 50
```

## Production Setup

### Gunicorn Configuration

Running with **1 worker** (not 4) due to in-memory collection status tracking:

```dockerfile
CMD ["python3", "-m", "gunicorn", "-w", "1", "-b", "0.0.0.0:8080",
     "--access-logfile", "-", "--error-logfile", "-", "wsgi:app"]
```

**Why 1 worker?**
- Global `collection_status` dict not shared across worker processes
- Multiple workers caused race conditions (status lost between requests)
- Alternative: Use Redis/database for shared state (future improvement)

### SQLite WAL Mode

Database uses Write-Ahead Logging for better concurrency:

```python
conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
conn.execute('PRAGMA journal_mode=WAL')  # Allow concurrent reads during writes
```

### OpenShift Resources

- **Memory**: 256Mi request, 512Mi limit
- **CPU**: 100m request, 500m limit
- **Storage**: 1Gi PVC for database
- **Replicas**: 1 (single instance due to SQLite)

## Troubleshooting

### "No data available"
Click "Refresh Data" button to collect test results. Wait 2-3 minutes.

### "Collection failed: attempt to write a readonly database"
- Check PVC is mounted at `/data`
- Verify `config.yaml` has `database.path: /data/dashboard.db`
- Check `wsgi.py` reads path from config (not hardcoded)

### "Page keeps reloading"
Fixed in latest version. Collection now refreshes data in-place, no full page reload.

### View logs shows "Bad Request"
Fixed. Now uses JavaScript `window.open()` instead of URL parameters (avoids 8KB limit).

### Build cluster scheduling issues
Dashboard uses minimal resources (256Mi) but build10 cluster is constrained:
- CronJobs may not schedule (solution: use manual refresh instead)
- GPU webhook issues can block deployments (wait for cluster team fixes)

## Why This Tool?

**Context:** ReportPortal may be deprecated. This tool provides:

1. **Self-contained**: Fetches and stores actual test logs
2. **Future-proof**: Can switch data sources (ReportPortal → GCS → Sippy)
3. **Focused**: Only OCP-* tests, not infrastructure noise
4. **Actionable**: Direct log access for debugging failures
5. **Historical**: Build your own test quality database

## Customization for Your Team

1. Edit `config.yaml`:
   - Update `job_patterns` for your periodic jobs
   - Set `versions` and `platforms` to track
   - Add test IDs to `blocklist` to exclude

2. Deploy to OpenShift:
   - Follow [openshift/README.md](openshift/README.md)
   - Use your team's project namespace
   - Set your ReportPortal token

3. Share dashboard URL with team

## License

Part of the CI Failure Tracker tool suite.
