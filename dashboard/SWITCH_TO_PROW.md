# How to Switch to Prow Artifacts (gcsweb)

## Step 1: Get Exact WINC Job Names

You need the **exact job names** from Prow. Try these methods:

### Method A: Run the search script
```bash
./find_winc_jobs.sh
```

### Method B: Check Prow manually
1. Go to: https://prow.ci.openshift.org/
2. Search for "winc" or "windows"
3. Look for periodic jobs like:
   - `periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-aws-winc-e2e`
   - `periodic-ci-openshift-windows-machine-config-operator-release-4.22-*`

### Method C: Ask your team
Message Ronnie or the WINC QE team for the complete list of periodic job names.

---

## Step 2: Update config.yaml

Edit `config.yaml` and change:

```yaml
collector:
  type: "gcsweb"  # Change from "reportportal" to "gcsweb"

  gcsweb:
    url: "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com"
    bucket: "test-platform-results"

    # ADD YOUR EXACT JOB NAMES HERE:
    job_names:
      # 4.21 jobs
      - "periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-aws-winc-e2e"
      - "periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-gcp-winc-e2e"
      - "periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-azure-winc-e2e"
      - "periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-nutanix-winc-e2e"

      # 4.22 jobs
      - "periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-aws-winc-e2e"
      - "periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-gcp-winc-e2e"
      - "periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-azure-winc-e2e"
      - "periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-nutanix-winc-e2e"

      # WMCO jobs (if applicable)
      - "periodic-ci-openshift-windows-machine-config-operator-release-4.21-*"
      - "periodic-ci-openshift-windows-machine-config-operator-release-4.22-*"

    max_builds_per_job: 50
    max_workers: 5
```

---

## Step 3: Clear Sample Data (Optional)

If you want to remove the sample data and start fresh:

```bash
# Backup current database
mv data/dashboard.db data/dashboard.db.sample

# Or just delete it (will be recreated)
rm data/dashboard.db
```

---

## Step 4: Collect Real Data from Prow

```bash
# Activate virtual environment
source venv/bin/activate

# Test gcsweb connection
./test_gcsweb.py

# Collect data (dry run first)
./dashboard.py collect --days 14 --dry-run

# Collect real data
./dashboard.py collect --days 14
```

---

## Step 5: Restart Dashboard

```bash
# Stop current server
pkill -f "dashboard.py serve"

# Start with real data
./dashboard.py serve
```

Open: http://localhost:8080

---

## Advantages of gcsweb vs ReportPortal

| Feature | gcsweb | ReportPortal |
|---------|--------|--------------|
| **Authentication** | None (public) | API token required |
| **VPN Required** | No | Yes |
| **Data Source** | Prow artifacts directly | ReportPortal aggregation |
| **Coverage** | All Prow jobs | Only jobs reported to RP |
| **Setup** | Just need job names | Need token + VPN |

---

## Troubleshooting

### "No builds found"
- Check job names are exact (no wildcards)
- Try with just one job first to test
- Verify job exists at: https://prow.ci.openshift.org/

### "Connection timeout"
- gcsweb might be slow with large directories
- Reduce `max_builds_per_job` to 20-30
- Focus on recent versions only (4.22)

### "Need exact job names"
- Cannot use wildcards like `*-winc-*`
- Must be complete job name
- Ask team or check Prow UI

---

## Quick Test

Once you have job names, test a single job:

```bash
# Edit config.yaml to have just ONE job name for testing
# Then:
./dashboard.py collect --days 7 --dry-run
```

This will show you if the job name is correct before collecting full data.
