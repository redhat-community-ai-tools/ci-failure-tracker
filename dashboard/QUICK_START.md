# WINC Test Dashboard - Quick Start

One-page guide to get started quickly.

## Setup (First Time Only)

```bash
# 1. Install
cd dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Get ReportPortal token from:
# https://reportportal-openshift.apps.dno.ocp-hub.prod.psi.redhat.com
# (Profile > API Token)

# 3. Save token
echo 'export REPORTPORTAL_API_TOKEN="your-token"' >> ~/.zshrc
source ~/.zshrc
```

## Daily Use (3 Simple Steps)

```bash
# 1. Connect to VPN (must be on Red Hat VPN)

# 2. Collect data (30 seconds)
cd dashboard
source venv/bin/activate
./dashboard.py collect --days 30

# 3. View dashboard
./dashboard.py serve
# Open: http://localhost:8080
# Press Ctrl+C when done
```

## What You'll See

- **Average Pass Rate** - Overall test health (aim for >85%)
- **Trend** - Improving/Declining/Stable
- **Version Comparison** - How 4.21 vs 4.22 compare
- **Worst Tests** - Which tests need attention

## Filters

- **Time Range:** 7/14/30/60/90 days
- **Version:** All / 4.21 / 4.22

## Quick Stats (No Browser)

```bash
./dashboard.py stats --days 7
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Connection failed | Connect to VPN |
| API token error | Set REPORTPORTAL_API_TOKEN in ~/.zshrc |
| Database not found | Run collect first |
| Old data showing | Run collect again |

## Contact

Ronnie Rasouli - rrasouli@redhat.com
