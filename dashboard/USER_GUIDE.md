# WINC Test Dashboard - User Guide

Simple guide for viewing WINC test health and pass rates.

## What This Dashboard Shows

- **Test Pass Rates**: How many tests are passing vs failing
- **Trends Over Time**: Is test health improving or declining?
- **Version Comparison**: How 4.21 compares to 4.22
- **Problem Tests**: Which tests are failing most often

## Prerequisites

- Python 3.10 or higher
- VPN connection to Red Hat network
- ReportPortal API token

## One-Time Setup

### 1. Install the Dashboard

```bash
cd dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Get Your ReportPortal API Token

1. Go to: https://reportportal-openshift.apps.dno.ocp-hub.prod.psi.redhat.com
2. Click your profile picture (top right)
3. Click "Profile"
4. Copy the API token

### 3. Save Your Token

Add this line to your `~/.zshrc` file:

```bash
export REPORTPORTAL_API_TOKEN="paste-your-token-here"
```

Then reload it:

```bash
source ~/.zshrc
```

## Daily Usage

### Step 1: Connect to VPN

Make sure you're connected to the Red Hat VPN.

### Step 2: Update Data (Do This Once Per Day)

```bash
cd dashboard
source venv/bin/activate
./dashboard.py collect --days 30
```

**What this does:** Downloads the last 30 days of test results from ReportPortal
**How long it takes:** About 30 seconds

### Step 3: Start the Dashboard

```bash
./dashboard.py serve
```

**What this does:** Starts a local web server
**Where to open:** http://localhost:8080

Press **Ctrl+C** when you're done to stop the server.

## Using the Dashboard

### Understanding the Dashboard

When you open http://localhost:8080, you'll see:

#### Top Section - Summary Cards
- **Average Pass Rate**: Overall percentage of passing tests
- **Total Runs**: Number of test runs in the selected period
- **Trend**: Whether things are improving, declining, or stable

#### Middle Section - Charts
- **Pass Rate Trend Over Time**: Line graph showing daily pass rates
- **Pass Rate by Version**: Bar chart comparing 4.21 vs 4.22

#### Bottom Section - Test Rankings
- **Lowest Performing Tests**: Table showing which tests are failing most
- Tests are sorted worst-first (0% at top, 100% at bottom)
- Shows test description, version, number of runs, and pass rate

### Filters

#### Time Range (Top Left)
- **Last 7 days**: Recent trend
- **Last 14 days**: Two-week view
- **Last 30 days**: Monthly overview (recommended)
- **Last 60 days**: Longer trend
- **Last 90 days**: Quarterly view

#### Version Filter (Top Right)
- **All Versions**: Combined data from 4.21 and 4.22
- **4.21**: Only show 4.21 test results
- **4.22**: Only show 4.22 test results

### Common Questions

**Q: How often should I update the data?**
A: Once per day is sufficient. Run `./dashboard.py collect --days 30` each morning.

**Q: What's a good pass rate?**
A: For WINC tests:
- Above 90%: Excellent
- 80-90%: Good
- 70-80%: Needs attention
- Below 70%: Critical, investigate immediately

**Q: Why are some tests at 0%?**
A: They failed every single time in the selected period. These are top priority to fix.

**Q: The numbers look wrong. What should I do?**
A:
1. Make sure you're on VPN
2. Run `./dashboard.py collect --days 30` to refresh data
3. Restart the dashboard with `./dashboard.py serve`

**Q: Can I share this dashboard with others?**
A: No, it's running on your local machine (localhost). Each person needs to run their own instance, or we need to deploy it to a shared server.

## Quick Command Reference

```bash
# 1. Start virtual environment (always do this first)
cd dashboard
source venv/bin/activate

# 2. Collect latest data (once per day)
./dashboard.py collect --days 30

# 3. Start dashboard
./dashboard.py serve

# 4. Quick stats without opening web browser
./dashboard.py stats --days 7

# 5. Stop dashboard
# Press Ctrl+C
```

## Troubleshooting

### Error: "Connection failed"
**Solution:** Connect to Red Hat VPN first

### Error: "API token not provided"
**Solution:** Make sure you set `REPORTPORTAL_API_TOKEN` in ~/.zshrc and ran `source ~/.zshrc`

### Error: "Database not found"
**Solution:** Run `./dashboard.py collect --days 30` first to create the database

### Dashboard shows old data
**Solution:** Run `./dashboard.py collect --days 30` to refresh

### Dashboard won't start
**Solution:**
1. Make sure you ran `source venv/bin/activate` first
2. Try running: `pip install -r requirements.txt`
3. Check if another dashboard is already running: `pkill -f "dashboard.py serve"`

## Tips for Managers

### Weekly Review Workflow

1. **Monday morning:**
   ```bash
   cd dashboard
   source venv/bin/activate
   ./dashboard.py collect --days 30
   ./dashboard.py serve
   ```

2. **Open dashboard:** http://localhost:8080

3. **Check these metrics:**
   - Overall pass rate (should be >85%)
   - Version comparison (is 4.22 worse than 4.21?)
   - Trend arrow (improving vs declining)
   - Top 5 worst tests (what needs immediate attention?)

4. **Take action:**
   - Tests below 50%: File Jira tickets
   - Declining trend: Discuss with team
   - Version differences: Investigate regression

### Monthly Reporting

Use the dashboard to generate monthly test health reports:

1. Set time range to "Last 30 days"
2. Screenshot the summary cards and charts
3. Export test rankings table (copy/paste into email)
4. Include in status report to leadership

### Comparing Sprint Results

To see if last sprint improved test health:

1. **Before sprint:** Note the overall pass rate
2. **After sprint:** Run collect and check new pass rate
3. **Compare:** Did we go from 82% to 88%? Success!

## Configuration

The file `config.yaml` controls what data is collected:

### Add/Remove Versions

```yaml
tracking:
  versions:
    - "4.21"
    - "4.22"
    - "4.23"  # Add new version
```

### Exclude Broken/Removed Tests

```yaml
tracking:
  blocklist:
    - "OCP-60944"  # Removed from test suite
    - "OCP-66352"  # Not a WINC test
```

These tests will be hidden from the dashboard even if they appear in ReportPortal.

## Support

For technical issues or questions:
- **Contact:** Ronnie Rasouli (rrasouli@redhat.com)
- **Team:** WINC QE Team

---

**Last Updated:** March 23, 2026
**Dashboard Version:** 1.0
