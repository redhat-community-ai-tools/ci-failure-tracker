# Quick Start Guide - Prow GCS Collector

This dashboard tracks WINC test pass rates over time by pulling data directly from Prow's GCS buckets.

## Why Prow GCS?

✅ **Direct access** to all Prow test histories
✅ **No authentication** needed (public bucket)
✅ **Includes OTP jobs** (openshift-tests-private) that Sippy might miss
✅ **Complete data** - JUnit XMLs with individual test results

## Setup (5 minutes)

### 1. Install Dependencies

```bash
cd dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Verify Configuration

The `config.yaml` is pre-configured for WINC tests with Prow GCS:

```yaml
collector:
  type: "prow-gcs"  # Using Prow GCS collector
  prow_gcs:
    job_names:
      # 4.21 WINC jobs
      - "periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-aws-winc-e2e"
      - "periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-gcp-winc-e2e"
      # 4.22 WINC jobs
      - "periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-aws-winc-e2e"
      - "periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-gcp-winc-e2e"
```

### 3. Test Connection

```bash
./test_prow_gcs.py
```

Expected output:
```
✓ PASS - Health Check
✓ PASS - List Job Runs
✓ PASS - Fetch Metadata

🎉 All tests passed!
```

### 4. Collect Data

```bash
# Dry run first to see what will be collected
./dashboard.py collect --days 14 --dry-run

# Collect real data (this may take a few minutes)
./dashboard.py collect --days 14
```

### 5. Start Dashboard

```bash
./dashboard.py serve
```

Open: **http://localhost:8080**

## What You'll See

### Dashboard Features

1. **Summary Cards**
   - Average pass rate over the selected period
   - Total test runs
   - Trend indicator (improving/declining/stable)

2. **Pass Rate Trend Chart**
   - Daily pass rates over time
   - Filter by time range (7/14/30/60/90 days)
   - Filter by version (4.21, 4.22, or all)

3. **Version Comparison**
   - Bar chart comparing pass rates across versions
   - See which version is performing better

4. **Lowest Performing Tests**
   - Table of tests with lowest pass rates
   - Visual progress bars
   - Helps identify flaky or consistently failing tests

## CLI Commands

```bash
# Collect test results from last 14 days
./dashboard.py collect --days 14

# View quick stats in terminal
./dashboard.py stats --days 7

# Start web dashboard
./dashboard.py serve --port 8080
```

## Data Flow

```
Prow GCS Buckets (gs://origin-ci-test/logs/)
    ↓
  Fetch finished.json (job status)
  Fetch JUnit XMLs (test results)
    ↓
  Parse and normalize data
    ↓
  Store in SQLite database (data/dashboard.db)
    ↓
  Calculate metrics and trends
    ↓
  Display in web dashboard
```

## Troubleshooting

### "No builds found"

The job name might not exist or have recent runs. Check job names at:
https://prow.ci.openshift.org/?job=periodic-ci-openshift-openshift-tests-private-release-*

### "Health check failed"

- Check internet connectivity
- Verify GCS bucket is accessible: `curl -I https://storage.googleapis.com/origin-ci-test/`

### "Database not found" when starting dashboard

Run `./dashboard.py collect` first to populate the database.

## Switching Data Sources

To switch back to ReportPortal or try Sippy later, just edit `config.yaml`:

```yaml
collector:
  type: "reportportal"  # or "prow-gcs" or "sippy"
```

## Next Steps

1. **Schedule daily collection**: Add to crontab
   ```
   0 9 * * * cd /path/to/dashboard && ./venv/bin/python dashboard.py collect --days 7
   ```

2. **Customize job list**: Edit `config.yaml` to add/remove jobs

3. **Adjust time ranges**: Modify `lookback_days` in config

4. **Share dashboard**: Deploy Flask app to internal server

## Comparison to Sippy

| Feature | Sippy | This Dashboard |
|---------|-------|---------------|
| Data source | Aggregated from multiple sources | Direct from Prow GCS |
| OTP coverage | Limited/Unknown | Full coverage |
| UI | Dense, complex | Clean, focused |
| Customization | Fixed views | Fully customizable |
| Authentication | None needed | None needed |
| Team focus | All OpenShift | WINC-specific |

## Questions?

- Check `README.md` for full documentation
- Review `src/collectors/prow_gcs.py` to understand data collection
- Modify `config.yaml` to customize behavior
