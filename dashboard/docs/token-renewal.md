# Prow API Token Renewal Guide

When the dashboard shows authentication errors (HTTP 403), you need to renew the Prow API token.

## Which Cluster?

The dashboard can be deployed on different clusters. Identify which cluster your deployment uses before renewing the token:

| Cluster | Web Console | Token Request URL |
|---------|------------|-------------------|
| **l2s4** | `apps.ci.l2s4.p1.openshiftapps.com` | https://oauth-openshift.apps.ci.l2s4.p1.openshiftapps.com/oauth/token/request |
| **build10** | `apps.build10.ci.devcluster.openshift.com` | https://console-openshift-console.apps.build10.ci.devcluster.openshift.com |

Check your dashboard URL to determine the cluster. For example, if your dashboard is at `https://winc-dashboard-winc-dashboard.apps.build10.ci.devcluster.openshift.com/`, you are on **build10**.

## Symptoms

- Dashboard shows "Failed to connect to data source"
- Logs show: `HTTP 403 Forbidden` or `Authentication failed`
- Logs show: `The Prow API token is missing, invalid, or expired`

## How to Renew the Token

> **Tip:** User tokens expire every 24 hours. For production deployments,
> consider using a [service account token](#automated-token-renewal-future)
> which can last up to 1 year, avoiding daily manual renewal.

### Step 1: Get Token from Web UI

Use the web console for your cluster to obtain a token.

**For l2s4 cluster:**

The `oc login` command does not work for the l2s4 cluster (times out). Use the web UI instead:

1. Open https://oauth-openshift.apps.ci.l2s4.p1.openshiftapps.com/oauth/token/request
2. Log in with your Red Hat SSO credentials
3. Click "Display Token"
4. Copy the token value (starts with `sha256~`)

**For build10 cluster:**

1. Open https://console-openshift-console.apps.build10.ci.devcluster.openshift.com
2. Click your profile (top right) → "Copy login command"
3. Click "Display Token"
4. Copy the token value (starts with `sha256~`)

### Step 3: Update the Secret

The deployment names and projects below apply to both clusters (l2s4 and build10). The `oc project` command sets the namespace context; the same project names are used regardless of which cluster you are logged into.

**For POC Dashboard:**

```bash
# Switch to POC project
oc project winc-dashboard-poc

# Create/update the secret
oc create secret generic prow-api-token \
  --from-literal=token=YOUR_TOKEN_HERE \
  --dry-run=client -o yaml | oc apply -f -

# Restart POC deployment
oc rollout restart deployment/winc-dashboard-poc
```

**For Z-Stream Dashboard:**

```bash
# Switch to main dashboard project
oc project winc-dashboard

# Create/update the secret
oc create secret generic prow-api-token \
  --from-literal=token=YOUR_TOKEN_HERE \
  --dry-run=client -o yaml | oc apply -f -

# Restart z-stream deployment
oc rollout restart deployment/winc-dashboard-zstream
```

**For Production Dashboard:**

```bash
# Switch to main dashboard project
oc project winc-dashboard

# Update the secret (if needed - production uses ReportPortal)
oc create secret generic prow-api-token \
  --from-literal=token=YOUR_TOKEN_HERE \
  --dry-run=client -o yaml | oc apply -f -

# Restart production deployment
oc rollout restart deployment/winc-dashboard
```

### Step 4: Verify

```bash
# Watch the pod restart
oc get pods -w

# Check logs for successful connection
oc logs -f deployment/winc-dashboard-zstream | grep "Health check"

# Should see:
# INFO - [prow_gcs] Health check response: status=200
```

### Step 5: Trigger Data Collection

After the deployment restarts:

```bash
# Via UI: Click "Refresh Data" button
# Or via API:
curl -X POST https://YOUR-DASHBOARD/api/trigger-collection \
  -H "Content-Type: application/json" \
  -d '{"days": 30}'
```

## Token Expiration

OpenShift tokens typically expire after:
- **24 hours** - for regular user tokens (from `oc login`)
- **30 days** - for service account tokens
- **Varies** - depending on cluster configuration

## Automated Token Renewal (Future)

Consider using a **service account token** instead of user token for longer validity:

```bash
# Create a service account
oc create serviceaccount dashboard-collector

# Get the service account token (OpenShift 4.11+)
oc create token dashboard-collector --duration=8760h  # 1 year

# Update the secret with service account token
oc create secret generic prow-api-token \
  --from-literal=token=SERVICE_ACCOUNT_TOKEN \
  --dry-run=client -o yaml | oc apply -f -
```

## Troubleshooting

### Error: "oc: command not found"

Install OpenShift CLI:
```bash
# Download from:
https://mirror.openshift.com/pub/openshift-v4/clients/ocp/latest/
```

### Error: "You must be logged in to the server (Unauthorized)"

Your token expired. Get a new one from the web UI for your cluster:

**l2s4:**
1. Open https://oauth-openshift.apps.ci.l2s4.p1.openshiftapps.com/oauth/token/request
2. Click "Display Token"
3. Copy and update the secret (see Step 2 above)

**build10:**
1. Open https://console-openshift-console.apps.build10.ci.devcluster.openshift.com
2. Click your profile → "Copy login command" → "Display Token"
3. Copy and update the secret (see Step 2 above)

### Error: "secret 'prow-api-token' already exists"

Use `oc apply` instead of `oc create`:
```bash
oc create secret generic prow-api-token \
  --from-literal=token=YOUR_TOKEN \
  --dry-run=client -o yaml | oc apply -f -
```

### Dashboard still shows 403 after renewal

1. Verify secret is updated:
   ```bash
   oc get secret prow-api-token -o jsonpath='{.data.token}' | base64 -d
   ```

2. Verify deployment has the secret mounted:
   ```bash
   oc get deployment winc-dashboard-zstream -o yaml | grep -A 3 "prow-api-token"
   ```

3. Verify pod picked up the new secret:
   ```bash
   # Delete pod to force recreation
   oc delete pod -l app=winc-dashboard-zstream
   ```

4. Check if token works manually:

   **l2s4:**
   ```bash
   TOKEN=$(oc get secret prow-api-token -o jsonpath='{.data.token}' | base64 -d)
   curl -H "Authorization: Bearer $TOKEN" \
     "https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com/prowjobs.js?var=allBuilds" \
     | head -10
   ```

   **build10:**
   ```bash
   TOKEN=$(oc get secret prow-api-token -o jsonpath='{.data.token}' | base64 -d)
   curl -H "Authorization: Bearer $TOKEN" \
     "https://qe-private-deck-ci.apps.build10.ci.devcluster.openshift.com/prowjobs.js?var=allBuilds" \
     | head -10
   ```

## Prevention

### Set a Reminder

Add a calendar reminder to renew tokens before they expire:
- User tokens: Every 3 weeks
- Service account tokens: Every 11 months (if using yearly tokens)

### Monitor for Expiration

Dashboard logs will show authentication errors when token expires:
```bash
# Monitor dashboard logs for auth errors
oc logs -f deployment/winc-dashboard-zstream | grep -i "403\|forbidden\|unauthorized"
```

## Related Documentation

- [OpenShift Authentication](https://docs.openshift.com/container-platform/latest/authentication/index.html)
- [Service Accounts](https://docs.openshift.com/container-platform/latest/authentication/using-service-accounts-in-applications.html)
- [Token Management](https://docs.openshift.com/container-platform/latest/authentication/managing_cloud_provider_credentials/about-cloud-credential-operator.html)

## Contact

If token renewal doesn't fix the issue:
1. Check Prow cluster status:
   - l2s4: https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com
   - build10: https://qe-private-deck-ci.apps.build10.ci.devcluster.openshift.com
2. Contact QE infrastructure team
3. File GitHub issue: https://github.com/redhat-community-ai-tools/ci-failure-tracker/issues
