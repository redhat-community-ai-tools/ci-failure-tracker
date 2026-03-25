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

# 2. Create secrets
# ReportPortal API token
oc create secret generic reportportal-token \
  --from-literal=token="YOUR_REPORTPORTAL_API_TOKEN"

# Webhook secret for GitHub integration
oc create secret generic webhook-secret \
  --from-literal=WebHookSecretKey=$(openssl rand -hex 20)

# 3. Deploy all resources
oc apply -f openshift/

# 4. Trigger initial build
oc start-build winc-dashboard

# 5. Wait for build to complete
oc get builds -w

# 6. Deploy the CronJob for scheduled data collection (optional but recommended)
oc apply -f openshift/cronjob.yaml

# 7. Get the dashboard URL
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

# Scheduled data collection (optional but recommended)
oc apply -f cronjob.yaml
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

The dashboard uses a **hybrid collection strategy** for best performance:

### 1. On-Login Collection (Immediate)
When you visit the dashboard for the first time (or if data is stale):

1. **Auto-Check**: Dashboard checks for recent data (last 7 days)
2. **Auto-Collection**: If no recent data exists, collection starts automatically (30 days of data)
3. **Progress Banner**: Blue banner shows real-time progress
4. **Completion**: Green banner appears when done, page auto-refreshes after 3 seconds

### 2. Scheduled Collection (Background)
A CronJob runs daily to keep data fresh:

- **Schedule**: Daily at 9 AM UTC (configurable in `cronjob.yaml`)
- **Collection**: Automatically fetches last 30 days of test results
- **No user interaction needed**: Runs in background

**Deploy the CronJob:**
```bash
oc apply -f openshift/cronjob.yaml
```

**Check CronJob status:**
```bash
# View CronJob
oc get cronjob

# View recent job runs
oc get jobs -l app=winc-dashboard

# View logs from latest collection
oc logs -l component=data-collector --tail=50
```

**Trigger manual collection:**
```bash
# Create one-time job from CronJob
oc create job manual-collect-$(date +%s) --from=cronjob/dashboard-collector

# Watch the job
oc logs -f job/manual-collect-XXXXX
```

**Why both approaches?**
- On-login: Ensures data is available immediately when needed
- CronJob: Keeps dashboard fresh for users, reduces initial load time
- Best of both worlds: Fast access + always up-to-date data

## GitHub Webhook - Automatic Deployments

The BuildConfig is configured to automatically rebuild and deploy when you push to GitHub.

### Setup Webhook

**1. Get the webhook URL with secret:**
```bash
WEBHOOK_SECRET=$(oc get secret webhook-secret -o jsonpath='{.data.WebHookSecretKey}' | base64 -d)
NAMESPACE=$(oc project -q)
WEBHOOK_URL="https://api.build10.ci.devcluster.openshift.com:6443/apis/build.openshift.io/v1/namespaces/${NAMESPACE}/buildconfigs/winc-dashboard/webhooks/${WEBHOOK_SECRET}/github"

echo $WEBHOOK_URL
```

**2. Configure webhook in GitHub:**

Using GitHub CLI:
```bash
# Create webhook payload
cat > /tmp/webhook.json << EOF
{
  "name": "web",
  "active": true,
  "events": ["push"],
  "config": {
    "url": "${WEBHOOK_URL}",
    "content_type": "json",
    "insecure_ssl": "1"
  }
}
EOF

# Add webhook to your repository
gh api repos/YOUR_ORG/ci-failure-tracker/hooks -X POST --input /tmp/webhook.json
```

Or manually in GitHub UI:
1. Go to: `https://github.com/YOUR_ORG/ci-failure-tracker/settings/hooks`
2. Click "Add webhook"
3. Paste the webhook URL
4. Content type: `application/json`
5. SSL verification: Disable (for internal OpenShift clusters)
6. Events: Just the push event
7. Click "Add webhook"

**3. Test the webhook:**
```bash
# Make a change and push to master
git commit -m "Test webhook"
git push origin master

# Watch the build trigger automatically
oc get builds -w
```

### How It Works

```
GitHub (push to master)
    ↓ webhook notification
OpenShift BuildConfig
    ↓ pulls latest code
Docker build (using dashboard/Dockerfile)
    ↓ builds image
ImageStream (stores image)
    ↓ auto-triggers deployment
Deployment (rolling update)
    ↓
Dashboard updated with latest code!
```

### Verify Webhook

```bash
# Check recent builds
oc get builds

# Check build logs
oc logs -f build/winc-dashboard-3

# Verify webhook deliveries in GitHub
gh api repos/YOUR_ORG/ci-failure-tracker/hooks | jq '.[].ping_url'
```

### Manual Build Trigger

If webhook isn't working, trigger manually:
```bash
oc start-build winc-dashboard
```

## Troubleshooting

### Build pods showing 0/1 (NORMAL)

If you see build pods with status "Completed" and 0/1 containers, **this is expected**:

```bash
NAME                        READY   STATUS      RESTARTS   AGE
winc-dashboard-1-build      0/1     Completed   0          1h
winc-dashboard-2-build      0/1     Completed   0          50m
winc-dashboard-77ff58-xxx   1/1     Running     0          10m  ← This is your app
```

**Why 0/1 is normal for build pods:**
- Build pods are **one-time jobs** that build Docker images
- After building, they stop (0/1 containers)
- Status "Completed" means build succeeded
- OpenShift keeps them for debugging
- Your actual app pod shows 1/1 Running

**To see only running pods:**
```bash
oc get pods -l app=winc-dashboard --field-selector=status.phase=Running
```

**To clean up old build pods:**
```bash
# OpenShift automatically cleans based on BuildConfig settings
# Or manually delete old builds:
oc delete builds --field-selector=status=Complete
```

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
