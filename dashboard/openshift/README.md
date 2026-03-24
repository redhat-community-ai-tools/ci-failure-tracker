# OpenShift Deployment

Deploy WINC CI Health Dashboard to OpenShift.

## Prerequisites

1. Access to OpenShift cluster
2. `oc` CLI tool installed and logged in
3. ReportPortal API token

## Quick Deploy

```bash
# 1. Create project
oc new-project winc-dashboard \
  --display-name="WINC CI Health Dashboard" \
  --description="Windows Containers QE CI test health tracking"

# 2. Create secret for ReportPortal token
oc create secret generic reportportal-token \
  --from-literal=token="YOUR_REPORTPORTAL_API_TOKEN"

# 3. Deploy all resources
oc apply -f openshift/

# 4. Get the dashboard URL
oc get route winc-dashboard -o jsonpath='{.spec.host}'
```

## Manual Steps

### 1. Create Secret

```bash
oc create secret generic reportportal-token \
  --from-literal=token="YOUR_TOKEN_HERE"
```

### 2. Deploy Resources

```bash
# Persistent storage
oc apply -f pvc.yaml

# Web application
oc apply -f deployment.yaml
oc apply -f service.yaml
oc apply -f route.yaml
```

### 3. Verify Deployment

```bash
# Check pod status
oc get pods

# Check logs
oc logs -f deployment/winc-dashboard

# Get public URL
oc get route winc-dashboard
```

## Data Collection

Data collection happens automatically on first dashboard access. When you visit the dashboard:

1. **First Access**: Dashboard checks for recent data (last 7 days)
2. **Auto-Collection**: If no recent data exists, collection starts automatically (30 days of data)
3. **Progress Banner**: Blue banner shows real-time progress (e.g., "Collecting job runs...")
4. **Completion**: Green banner appears when done, page auto-refreshes after 3 seconds

No manual intervention or scheduled jobs required - data collection is on-demand.

## Troubleshooting

### Pod not starting

```bash
# Check events
oc get events --sort-by='.lastTimestamp'

# Check pod logs
oc logs deployment/winc-dashboard

# Describe pod
oc describe pod -l app=winc-dashboard
```

### Database issues

If the dashboard shows "No data available", data collection may still be in progress:

```bash
# Check collection status
curl https://$(oc get route winc-dashboard -o jsonpath='{.spec.host}')/api/collection-status

# View pod logs to see collection progress
oc logs -f deployment/winc-dashboard

# Exec into pod to check database directly
oc rsh deployment/winc-dashboard

# Check database contents
ls -la /data/
sqlite3 /data/dashboard.db "SELECT COUNT(*) FROM job_runs;"
```

### Update deployment

```bash
# Edit deployment
oc edit deployment winc-dashboard

# Or apply changes
oc apply -f deployment.yaml

# Force rollout
oc rollout restart deployment/winc-dashboard
```

## Resources

- **Memory**: 256Mi request, 512Mi limit
- **CPU**: 100m request, 500m limit
- **Storage**: 1Gi persistent volume

## Architecture

```
Internet
   ↓
Route (HTTPS)
   ↓
Service (port 8080)
   ↓
Deployment (gunicorn + Flask)
   ├─ On-login data collection (background thread)
   └─ PersistentVolumeClaim (SQLite database)
```

**Data Flow:**
1. User accesses dashboard via HTTPS route
2. Flask app checks database for recent data
3. If needed, background thread collects data from ReportPortal
4. Results stored in SQLite database on persistent volume
5. Dashboard displays analytics and reports
