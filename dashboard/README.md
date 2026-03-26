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
  lookback_days: 30
  blocklist:
    - "OCP-60944"  # Removed from suite
    - "OCP-66352"  # Not relevant

database:
  path: "/data/dashboard.db"  # Use /data for persistent volume
```

### Set Environment Variables

```bash
# Required for ReportPortal
export REPORTPORTAL_API_TOKEN="your-token-here"
```

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

### API Endpoints

REST APIs for custom integrations:

- `GET /api/summary?days=30&version=4.21` - Summary statistics
- `GET /api/trend?days=30&version=4.21` - Pass rate trend
- `GET /api/test-pass-rates?days=30&version=4.21` - Per-test pass rates with logs
- `GET /api/version-comparison?days=30` - Compare versions
- `POST /api/trigger-collection` - Start data collection
- `GET /api/collection-status` - Check collection progress
- `GET /logs?content=<log>&test=<name>` - View logs page

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
