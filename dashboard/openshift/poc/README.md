# POC: Prow MCP Server Integration - Deployment Guide

Deploy WINC Dashboard POC to test prow-mcp-server integration in isolated namespace.

## Overview

This POC deploys:
1. **prow-mcp-server** - MCP server providing Prow/GCS data access
2. **winc-dashboard-poc** - Dashboard using prow_mcp collector

Both deployed to `winc-dashboard-poc` namespace, isolated from production instance.

## Prerequisites

1. Access to OpenShift cluster (build10)
2. `oc` CLI logged in
3. ReportPortal API token (for fallback)

## Quick Deploy

```bash
# 1. Create POC namespace
oc new-project winc-dashboard-poc \
  --display-name="WINC Dashboard POC - Prow MCP" \
  --description="Testing prow-mcp-server integration"

# 2. Create secrets (reuse from production or create new)
oc create secret generic reportportal-token \
  --from-literal=token="YOUR_REPORTPORTAL_API_TOKEN"

oc create secret generic webhook-secret \
  --from-literal=WebHookSecretKey=$(openssl rand -hex 20)

# 3. Deploy prow-mcp-server
oc apply -f mcp-imagestream.yaml
oc apply -f mcp-buildconfig.yaml
oc apply -f mcp-service.yaml
oc apply -f mcp-deployment.yaml

# 4. Start prow-mcp-server build
oc start-build prow-mcp-server

# 5. Wait for MCP server build to complete
oc get builds -w

# 6. Deploy dashboard POC
oc apply -f dashboard-imagestream.yaml
oc apply -f dashboard-buildconfig.yaml
oc apply -f dashboard-configmap.yaml
oc apply -f dashboard-pvc.yaml
oc apply -f dashboard-service.yaml
oc apply -f dashboard-route.yaml
oc apply -f dashboard-deployment.yaml

# 7. Start dashboard POC build
oc start-build winc-dashboard-poc

# 8. Wait for dashboard build to complete
oc get builds -w

# 9. Get POC dashboard URL
oc get route winc-dashboard-poc -o jsonpath='{.spec.host}'
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Namespace: winc-dashboard-poc                               │
│                                                              │
│  ┌────────────────────┐       ┌─────────────────────────┐  │
│  │ prow-mcp-server    │       │ winc-dashboard-poc      │  │
│  │                    │       │                         │  │
│  │ Port: 8000         │←─────│ ProwMCPCollector        │  │
│  │ Service: 3000      │       │ Config: prow_mcp        │  │
│  │                    │       │                         │  │
│  │ FROM: rrasouli/    │       │ FROM: rrasouli/         │  │
│  │  prow-mcp-server   │       │  ci-failure-tracker     │  │
│  │  (main branch)     │       │  (poc-prow-mcp branch)  │  │
│  └────────────────────┘       └─────────────────────────┘  │
│           ↓                              ↓                  │
│    Prow CI / GCS                   SQLite DB (PVC)         │
└─────────────────────────────────────────────────────────────┘
```

## Key Differences from Production

| Component | Production | POC |
|-----------|-----------|-----|
| Namespace | `winc-dashboard` | `winc-dashboard-poc` |
| Collector | ReportPortal | prow_mcp |
| Data Source | ReportPortal API | prow-mcp-server → GCS |
| Branch | `master` | `poc-prow-mcp` |
| URL | `winc-dashboard-winc-dashboard.apps.build10...` | `winc-dashboard-poc-winc-dashboard-poc.apps.build10...` |

## Deployment Details

### prow-mcp-server

**BuildConfig:**
- Source: `https://github.com/rrasouli/prow-mcp-server.git` (fork with amd64 fix)
- Branch: `main`
- Dockerfile: `Containerfile.sse`
- Platform: `linux/amd64` (forced to avoid exec format error)

**Service:**
- Internal port: 8000 (MCP server SSE)
- Service port: 3000 (mapped for dashboard config compatibility)
- Type: ClusterIP (internal only)

**Environment:**
- `MCP_TRANSPORT=sse`
- `MCP_HOST=0.0.0.0`
- `MCP_PORT=8000`

### winc-dashboard-poc

**BuildConfig:**
- Source: `https://github.com/rrasouli/ci-failure-tracker.git` (POC branch)
- Branch: `poc-prow-mcp`
- Context: `dashboard/`

**ConfigMap:**
- Collector type: `prow_mcp`
- MCP server URL: `http://prow-mcp-server:3000`
- Job names: Same WINC periodic jobs as production

**Storage:**
- PVC: 1Gi for SQLite database
- Mount: `/data/dashboard.db`

## Verification

### 1. Check MCP Server Health

```bash
# Port-forward to access MCP server locally
oc port-forward svc/prow-mcp-server 3000:3000

# Test health endpoint
curl http://localhost:3000/health

# Expected response: {"status": "ok"}
```

### 2. Test MCP Tools

```bash
# Call get_latest_job_run tool
curl -X POST http://localhost:3000/mcp/tools/get_latest_job_run \
  -H "Content-Type: application/json" \
  -d '{
    "job_name": "periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-nightly-aws-ipi-ovn-winc-f7"
  }'
```

### 3. Check Dashboard Logs

```bash
# Watch dashboard logs for collection activity
oc logs -f deployment/winc-dashboard-poc

# Expected logs:
# INFO - Using collector type: prow_mcp
# INFO - Collecting job runs...
# INFO - Collected X job runs
# INFO - Collecting test results (fetching logs via MCP)...
```

### 4. Access POC Dashboard

```bash
# Get URL
POC_URL=$(oc get route winc-dashboard-poc -o jsonpath='{.spec.host}')
echo "https://${POC_URL}"

# Open in browser or curl
curl -I https://${POC_URL}
```

### 5. Trigger Data Collection

Visit dashboard and click "Refresh Data" button. Watch logs:

```bash
oc logs -f deployment/winc-dashboard-poc | grep -i "prow_mcp\|collection"
```

## Troubleshooting

### MCP Server Build Fails (Architecture Error)

**Error:** `exec format error` or `platform mismatch`

**Solution:**
The fork already has the fix. Verify Containerfile.sse has:
```dockerfile
FROM --platform=linux/amd64 registry.access.redhat.com/ubi9/python-312
```

### MCP Server Pod CrashLoopBackOff

```bash
# Check logs
oc logs deployment/prow-mcp-server --tail=50

# Check if port 8000 is bound
oc exec deployment/prow-mcp-server -- netstat -tlnp | grep 8000
```

### Dashboard Cannot Connect to MCP Server

```bash
# Verify MCP server service exists
oc get svc prow-mcp-server

# Test connectivity from dashboard pod
oc exec deployment/winc-dashboard-poc -- curl -v http://prow-mcp-server:3000/health

# Expected: HTTP 200 OK
```

### Dashboard Shows No Data

```bash
# Check collection status via API
curl https://$(oc get route winc-dashboard-poc -o jsonpath='{.spec.host}')/api/collection-status

# Manually trigger collection
curl -X POST https://$(oc get route winc-dashboard-poc -o jsonpath='{.spec.host}')/api/trigger-collection \
  -H "Content-Type: application/json" \
  -d '{"days": 7}'
```

### Build Fails

```bash
# Check build logs
oc logs -f build/prow-mcp-server-1
oc logs -f build/winc-dashboard-poc-1

# Retry build
oc start-build prow-mcp-server
oc start-build winc-dashboard-poc
```

## Comparison Testing

### Side-by-Side Comparison

**Production (ReportPortal):**
```bash
PROD_URL="winc-dashboard-winc-dashboard.apps.build10.ci.devcluster.openshift.com"
curl -s "https://${PROD_URL}/api/summary?days=7" | jq
```

**POC (Prow MCP):**
```bash
POC_URL=$(oc get route winc-dashboard-poc -o jsonpath='{.spec.host}')
curl -s "https://${POC_URL}/api/summary?days=7" | jq
```

### Metrics to Compare

1. **Data Collection Speed:**
   - Production: Time to collect from ReportPortal
   - POC: Time to collect via MCP server

2. **Data Accuracy:**
   - Compare test counts
   - Compare pass rates
   - Compare failed test lists

3. **Log Quality:**
   - Production: AI-generated summaries
   - POC: Actual stdout/stderr from build logs

4. **Reliability:**
   - Monitor collection success rates
   - Check for timeouts or errors

## Cleanup

To remove the POC deployment:

```bash
# Delete entire namespace
oc delete project winc-dashboard-poc

# Or delete individual resources
oc delete -f .
```

## Success Criteria

POC is successful if:

- [x] MCP server builds without architecture errors
- [ ] MCP server responds to health checks
- [ ] MCP tools return valid data
- [ ] Dashboard collects test results
- [ ] Dashboard displays OCP-* tests
- [ ] Test logs are actual stdout/stderr (not AI summaries)
- [ ] Collection completes in reasonable time (<5 minutes)
- [ ] Data matches production accuracy

## Next Steps

If POC succeeds:
1. Compare POC vs production data quality
2. Measure performance differences
3. Document benefits and tradeoffs
4. Consider merging prow_mcp support to master
5. Potentially make prow_mcp the default collector

If POC has issues:
1. Debug with prow-mcp-server team
2. Contribute fixes to upstream
3. Fall back to ReportPortal or direct GCS

## Related Documentation

- POC Overview: `../../POC_PROW_MCP.md`
- prow-mcp-server: https://github.com/redhat-community-ai-tools/prow-mcp-server
- Fork with fixes: https://github.com/rrasouli/prow-mcp-server
- Production deployment: `../README.md`
