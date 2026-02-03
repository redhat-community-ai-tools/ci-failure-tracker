# CI Failure Tracker - ReportPortal to Jira Bridge

## Overview

New automated tool that analyzes CI test failures from ReportPortal and creates Jira tickets for QE teams.

**Key Feature**: Creates **one ticket per unique test case** (e.g., OCP-11111), aggregating failures across all platforms (AWS, GCP, Azure, etc.) with links to each failure instance.

## Architecture

```
┌─────────────────┐
│  ReportPortal   │ ← Prow periodic jobs report results
│   (Data Source) │
└────────┬────────┘
         │ API queries
         ↓
┌─────────────────┐
│ Failure Tracker │ ← Python script running periodically
│     Script      │
└────────┬────────┘
         │
         ├─→ Query ReportPortal API for recent failures
         ├─→ Filter by: version (4.20, 4.21, 4.22), test type (winc)
         ├─→ Analyze failure patterns
         ├─→ Check for existing Jira tickets
         │
         ↓
┌─────────────────┐
│   Jira (WINC)   │ ← Auto-create tickets for new failures
│   Project       │
└─────────────────┘
```

## Features

1. **One Ticket Per Test Case**: Creates a single Jira ticket for each unique test (e.g., OCP-11111)
2. **Multi-Platform Aggregation**: If OCP-11111 fails on AWS, GCP, and Azure, all failures appear in ONE ticket
3. **Comprehensive Failure Links**: Lists every failure instance with direct links to ReportPortal logs
4. **Platform Breakdown**: Shows failure count per platform (AWS: 3 failures, GCP: 2 failures, etc.)
5. **Version Tracking**: Track failures across multiple OpenShift versions (4.19, 4.20, 4.21, 4.22)
6. **Team Configuration**: YAML-based per-team configuration (jobs, platforms, thresholds)
7. **Server-Side Filtering**: Efficient API queries with status and name filtering
8. **Configurable CLI**: Adjust page size, max pages, and workers via command-line options

## Data Flow

### 1. Query ReportPortal
```
GET /api/v1/{projectName}/launch
Filter by:
- Launch name pattern: periodic-ci-openshift-openshift-tests-private-release-*-winc-*
- Status: FAILED
- Time range: Last 7 days
```

### 2. Extract Failed Tests
```
GET /api/v1/{projectName}/item/{launchId}
For each failed launch:
- Get test items with status FAILED
- Extract test name, error message, stack trace
- Identify test file and line number
```

### 3. Analyze Patterns
```python
Failure Pattern = {
    "test_name": "OCP-39451",
    "test_file": "test/extended/winc/winc.go",
    "error_signature": hash(error_message),
    "versions_affected": ["4.20", "4.21", "4.22"],
    "platforms_affected": ["aws", "azure", "gcp"],
    "failure_count": 15,
    "first_seen": "2026-01-25",
    "last_seen": "2026-02-02"
}
```

### 4. Check Existing Tickets
```
JQL Query:
project = WINC AND
labels = "ci-failure" AND
summary ~ "OCP-39451" AND
status NOT IN (Closed, Resolved)
```

### 5. Create/Update Ticket
- If no ticket exists → Create new
- If ticket exists but pattern changed → Add comment
- If ticket is old but failure recurring → Reopen with new data

## Implementation

### Directory Structure
```
ci-failure-tracker/
├── README.md                    # This file
├── requirements.txt             # Python dependencies
├── ci_failure_tracker.py        # Main script (new tool)
├── src/
│   └── core/
│       ├── config_loader.py     # Team configuration loader
│       └── jira_client.py       # Jira API client
├── teams/
│   ├── winc.yaml               # WINC team configuration (example)
│   └── README.md               # Team config documentation
└── venv/                        # Python virtual environment
```

### Configuration (config.yaml)
```yaml
reportportal:
  url: "https://reportportal-openshift.apps.dno.ocp-hub.prod.psi.redhat.com"
  project: "prow"
  api_token: "${REPORTPORTAL_API_TOKEN}"  # Set in environment

jira:
  url: "https://issues.redhat.com"
  project: "WINC"
  parent_story: "WINC-1552"  # Parent epic for CI failures
  # Authentication via environment variables:
  # - JIRA_USER (username or email)
  # - JIRA_API_TOKEN (personal API token)

tracking:
  versions:
    - "4.20"
    - "4.21"
    - "4.22"

  platforms:
    - "aws"
    - "azure"
    - "gcp"
    - "vsphere"
    - "nutanix"

  job_patterns:
    - "periodic-ci-openshift-openshift-tests-private-release-*-winc-*"
    - "periodic-ci-openshift-windows-machine-config-operator-release-*"

  lookback_days: 7  # How far back to search
  failure_threshold: 3  # Minimum failures before creating ticket

labels:
  - "ci-failure"
  - "automated"
  - "phase-1-stabilization"

ticket_template: |
  h2. Automated CI Failure Report

  *Test*: {test_name}
  *Test File*: {{test_file}}:{line_number}
  *Affected Versions*: {versions}
  *Affected Platforms*: {platforms}

  h2. Failure Summary

  * *First Seen*: {first_seen}
  * *Last Seen*: {last_seen}
  * *Failure Count*: {failure_count} failures in {lookback_days} days
  * *Failure Rate*: {failure_rate}%

  h2. Error Message

  {code}
  {error_message}
  {code}

  h2. Recent Failures

  {failure_table}

  h2. ReportPortal Links

  {reportportal_links}

  h2. Recommended Actions

  # Review test code at {{test_file}}:{line_number}
  # Check for recent changes in affected test
  # Verify if issue is platform-specific or version-specific
  # Investigate error logs in ReportPortal

  ---
  _This ticket was automatically created by CI Failure Tracker_
  _Configuration: versions={versions}, threshold={failure_threshold}, lookback={lookback_days}d_
```

## Usage

### Installation
```bash
cd /Users/rrasouli/Documents/GitHub/openshift-tests-private/tools/ci-failure-tracker
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Set Environment Variables
```bash
# Required for all operations
export REPORTPORTAL_API_TOKEN="your-reportportal-token"

# Required only for creating tickets (not needed for --dry-run)
export JIRA_USER="your-jira-username-or-email"
export JIRA_API_TOKEN="your-jira-api-token"
```

**Note**:
- For dry-run mode, only `REPORTPORTAL_API_TOKEN` is needed
- Jira authentication uses REST API with username/token
- Get Jira API token from: https://id.atlassian.com/manage-profile/security/api-tokens

### Run Manually
```bash
# Dry run (no ticket creation, analyze only)
./ci_failure_tracker.py --team winc --dry-run --days 7

# Analyze with custom lookback period
./ci_failure_tracker.py --team winc --dry-run --days 14

# Fetch more launches (if failures are missing)
./ci_failure_tracker.py --team winc --dry-run --days 7 --max-pages 20

# Use larger page sizes for fewer API calls
./ci_failure_tracker.py --team winc --dry-run --page-size 300 --max-pages 3

# Run and create tickets (remove --dry-run)
./ci_failure_tracker.py --team winc --days 7

# Show help
./ci_failure_tracker.py --help
```

### CLI Options
- `--team TEXT`: Team ID (required) - must match a YAML file in `teams/` directory
- `--dry-run`: Analyze failures without creating Jira tickets
- `--verbose`: Enable verbose output for debugging
- `--days N`: Override lookback period from config (default: from team config)
- `--page-size N`: Number of launches per API page (default: 150)
- `--max-pages N`: Maximum API pages to fetch (default: 5)
- `--max-workers N`: Number of parallel workers (default: from team config)

### Schedule as Periodic Job
```bash
# Add to crontab to run daily at 9 AM
0 9 * * * cd /path/to/ci-failure-tracker && ./venv/bin/python ci_failure_tracker.py

# Or create a Prow periodic job (recommended for running in CI cluster)
```

## Ticket Creation Strategy

### One Ticket Per Test Case
The tool creates **one Jira ticket per unique test case**, regardless of:
- How many platforms it failed on (AWS, GCP, Azure, etc.)
- How many times it failed
- What the specific error messages were
- Which OCP versions were affected

**Example:**
- Test: `OCP-11111`
- Failures:
  - AWS: 3 failures across 4.19, 4.20
  - GCP: 2 failures on 4.21
  - Azure: 1 failure on 4.20
- **Result**: ONE ticket with all 6 failures listed, grouped by platform

### Deduplication Logic
```python
# Group by test_name ONLY (not error signature)
for instance in all_failures:
    ticket_key = instance.test_name  # e.g., "OCP-11111"
    grouped_failures[ticket_key].append(instance)

# Check if ticket already exists in Jira
if jira.find_ticket(summary=f"CI Failure: {test_name}"):
    skip  # Ticket already exists
else:
    create_ticket(test_name, all_instances_for_this_test)
```

## Output Examples

### Console Output
```
CI Failure Tracker - Run at 2026-02-02 10:00:00
============================================

Querying ReportPortal for versions: 4.20, 4.21, 4.22
Looking back: 7 days

Found 45 failed periodic jobs:
- 4.20: 12 failures
- 4.21: 18 failures
- 4.22: 15 failures

Analyzing failure patterns...

Pattern 1: OCP-39451 - Windows→Linux ClusterIP failure
  Versions: 4.20, 4.21, 4.22
  Platforms: aws, azure, gcp
  Failures: 15 (23% failure rate)
  Status: Existing ticket WINC-1605 ✓
  Action: Skipped (ticket exists)

Pattern 2: OCP-77777 - WMCO metrics timeout
  Versions: 4.21, 4.22
  Platforms: vsphere
  Failures: 8 (45% failure rate)
  Status: No ticket found
  Action: Creating ticket... WINC-1606 ✓

Pattern 3: OCP-43832 - BYOH zone parsing
  Versions: 4.20
  Platforms: nutanix
  Failures: 2 (5% failure rate)
  Status: Below threshold (3)
  Action: Skipped (not enough failures)

Summary:
- Total patterns found: 12
- Tickets created: 3
- Tickets updated: 1
- Skipped (existing): 5
- Skipped (threshold): 3
```

### Created Jira Ticket Example
See example in the ticket template above.

## API References

- **ReportPortal API**: https://developers.reportportal.io/api-docs/
- **Jira REST API**: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
- **How to get ReportPortal API token**: https://reportportal.io/docs/log-data-in-reportportal/HowToGetAnAccessTokenInReportPortal/

## Future Enhancements

1. **Slack Notifications**: Send daily summary to #windows-containers channel
2. **Trend Analysis**: Track if failures are increasing/decreasing over time
3. **Auto-assignment**: Assign tickets based on test ownership (CODEOWNERS)
4. **Integration with TestGrid**: Cross-reference with OpenShift TestGrid data
5. **ML-based Grouping**: Use machine learning to group similar failures
6. **Auto-close**: Close tickets when test passes consistently for N days
7. **Dashboard**: Web dashboard showing failure trends and ticket status

## Maintenance

### Adding New Versions
Edit `config.yaml`:
```yaml
tracking:
  versions:
    - "4.23"  # Add new version
```

### Adjusting Thresholds
```yaml
tracking:
  failure_threshold: 5  # Require 5 failures instead of 3
  lookback_days: 14     # Look back 14 days instead of 7
```

### Filtering Out Flaky Tests
Create a skip list in `config.yaml`:
```yaml
skip_tests:
  - "OCP-12345"  # Known flaky, tracked elsewhere
  - "OCP-67890"  # Intentionally failing
```

## Troubleshooting

### "Authentication failed"
- Check ReportPortal API token is set: `echo $REPORTPORTAL_API_TOKEN`
- Check Jira credentials are set (only needed for creating tickets):
  - `echo $JIRA_USER`
  - `echo $JIRA_API_TOKEN`
- For dry-run mode, only `REPORTPORTAL_API_TOKEN` is needed

### "No failures found"
- Verify job name patterns in config match actual Prow job names
- Check date range (expand lookback_days)
- Verify ReportPortal project name is correct

### "Duplicate tickets created"
- Check JQL query is working: `jira issue list -q "project=WINC AND labels=ci-failure"`
- Verify error signature generation is stable

## Contributing

1. Test changes locally with `--dry-run`
2. Add unit tests for new features
3. Update this README with configuration changes
4. Submit PR to openshift-tests-private repo
