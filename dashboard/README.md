# CI Test Pass Rate Dashboard

Independent tool for tracking test pass rates over time from CI test runs. Built with pluggable data source architecture to support multiple backends.

## Features

- **Pluggable Data Sources**: Easily switch between ReportPortal, Prow GCS, Sippy, etc.
- **Historical Tracking**: SQLite database stores test results over time
- **Real-time Dashboard**: Web interface with interactive charts
- **Key Metrics**:
  - Overall pass rate % over time
  - Per-test pass rates (identify flaky/failing tests)
  - Per-version trends (compare 4.21 vs 4.22)
  - Per-platform comparison (AWS vs GCP vs Azure)

## Architecture

```
┌──────────────────┐
│  Data Sources    │  ← ReportPortal, Prow GCS, Sippy (pluggable)
└────────┬─────────┘
         │ Collectors fetch test results
         ↓
┌──────────────────┐
│  SQLite Database │  ← Store historical data
└────────┬─────────┘
         │ Calculate metrics
         ↓
┌──────────────────┐
│  Web Dashboard   │  ← Interactive charts and trends
└──────────────────┘
```

## Quick Start

### Installation

```bash
cd dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configuration

Edit `config.yaml` to configure:
- Data source (ReportPortal, etc.)
- Job patterns to monitor
- Versions and platforms to track
- Database location

```yaml
collector:
  type: "reportportal"
  reportportal:
    url: "https://reportportal.example.com"
    project: "prow"
    job_patterns:
      - "periodic-ci-*-winc-*"

tracking:
  versions:
    - "4.21"
    - "4.22"
  platforms:
    - "aws"
    - "gcp"
```

### Set Environment Variables

```bash
# Required for ReportPortal collector (if using reportportal)
export REPORTPORTAL_API_TOKEN="your-token-here"

# No environment variables needed for Prow GCS collector (publicly accessible)
```

### Collect Data

```bash
# Collect last 30 days of test results
./dashboard.py collect --days 30

# Dry run to see what would be collected
./dashboard.py collect --days 7 --dry-run
```

### Start Dashboard

```bash
# Start web server on http://localhost:8080
./dashboard.py serve

# Custom port
./dashboard.py serve --port 9000
```

### View Statistics

```bash
# Show quick stats in terminal
./dashboard.py stats --days 7
```

## Data Collectors

The tool uses a pluggable collector architecture. Currently implemented:

### Prow GCS Collector (Default)

**Best for:** Direct access to OpenShift CI test histories, including openshift-tests-private (OTP) jobs.

Queries test results directly from Prow's GCS buckets (`gs://origin-ci-test/logs/`). This is the recommended collector for WINC tests as it:
- ✅ Publicly accessible (no authentication needed)
- ✅ Has complete test history from all Prow jobs
- ✅ Includes periodic OTP jobs that Sippy might not track
- ✅ Parses JUnit XML for detailed test results

**Configuration:**
```yaml
collector:
  type: "prow-gcs"
  prow_gcs:
    bucket: "origin-ci-test"
    job_names:
      - "periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-aws-winc-e2e"
      - "periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-aws-winc-e2e"
    max_builds_per_job: 50
    max_workers: 5
```

**Environment:**
- No environment variables required (public GCS bucket)

**Data Sources:**
- `finished.json` - Job status and metadata
- JUnit XML files - Individual test results

### ReportPortal Collector

**Best for:** Teams already using ReportPortal for test reporting.

Fetches test results from ReportPortal API. Useful if you want to leverage ReportPortal's existing data aggregation.

**Configuration:**
```yaml
collector:
  type: "reportportal"
  reportportal:
    url: "https://reportportal.example.com"
    project: "prow"
    page_size: 150
    max_pages: 10
    max_workers: 5
```

**Environment:**
- `REPORTPORTAL_API_TOKEN`: API token for authentication

### Future Collectors

The base collector interface (`src/collectors/base.py`) supports adding:
- **Sippy**: Use OpenShift CI analytics API
- **TestGrid**: Parse TestGrid data
- **Custom**: Implement your own data source

## Dashboard Features

### Main Dashboard View

- **Summary Cards**: Overall metrics and trend indicators
- **Pass Rate Trend**: Line chart showing pass rate over time
- **Version Comparison**: Bar chart comparing versions
- **Test Rankings**: Table of lowest-performing tests

### Interactive Filters

- Time range selection (7/14/30/60/90 days)
- Version filter
- Platform filter (planned)

### API Endpoints

The dashboard exposes REST APIs for custom integrations:

- `GET /api/summary?days=30` - Summary statistics
- `GET /api/trend?days=30&version=4.21` - Pass rate trend
- `GET /api/test-rankings?days=30&limit=20` - Worst performing tests
- `GET /api/version-comparison?days=30` - Compare versions
- `GET /api/platform-comparison?days=30` - Compare platforms

## Database Schema

SQLite database with tables:

- `job_runs`: Overall job statistics
- `test_results`: Individual test results
- `daily_metrics`: Pre-aggregated daily stats
- `test_metrics`: Per-test aggregated stats

## Automated Collection

Set up cron job for daily data collection:

```bash
# Add to crontab
0 9 * * * cd /path/to/dashboard && ./venv/bin/python dashboard.py collect --days 7
```

## Why This Tool?

**Context:** Data Router (ReportPortal integration) is being sunset in Q4 2026. This tool provides:

1. **Future-proof**: Pluggable architecture, not tied to Data Router
2. **Independent**: Direct data source access, no intermediaries
3. **Historical**: Build your own database of test results
4. **Flexible**: Easy to add new data sources or metrics

## Extending

### Adding a New Collector

1. Create new file in `src/collectors/` (e.g., `prow_gcs.py`)
2. Extend `BaseCollector` class
3. Implement required methods:
   - `collect_job_runs()`
   - `collect_test_results()`
   - `health_check()`
4. Update `config.yaml` to use new collector type

Example:
```python
from .base import BaseCollector, JobRun, TestResult

class ProwGCSCollector(BaseCollector):
    @property
    def name(self) -> str:
        return "prow-gcs"

    def collect_job_runs(self, start_date, end_date, ...):
        # Fetch from GCS buckets
        pass
```

### Adding New Metrics

Edit `src/metrics/calculator.py` to add custom metrics:

```python
def get_flaky_tests(self, days=30, threshold=0.5):
    """Find tests with pass rates between 40-60% (flaky)"""
    # Implementation
```

## Troubleshooting

### "Database not found"
Run `dashboard.py collect` first to create and populate database.

### "Failed to connect to data source"
- Check ReportPortal URL in config
- Verify `REPORTPORTAL_API_TOKEN` is set
- Test connectivity: `curl -H "Authorization: Bearer $TOKEN" $URL/api/v1/project`

### "No data available"
- Increase `--days` or `max_pages` to collect more data
- Check job patterns match actual job names
- Verify date range includes recent runs

## License

Part of the CI Failure Tracker tool suite.
