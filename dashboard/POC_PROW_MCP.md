# POC: Prow MCP Server Integration

**Branch:** `poc-prow-mcp`

**Status:** Proof of Concept - Testing prow-mcp-server as alternative to ReportPortal

**Production Instance:** https://winc-dashboard-winc-dashboard.apps.build10.ci.devcluster.openshift.com/ (UNTOUCHED - uses ReportPortal)

**POC Instance:** Will be deployed to `winc-dashboard-poc` namespace

## What This POC Tests

Integration with [prow-mcp-server](https://github.com/redhat-community-ai-tools/prow-mcp-server) to:

1. ✅ **Fetch test results directly from Prow/GCS** (no ReportPortal dependency)
2. ✅ **Get actual test logs** via MCP tools
3. ✅ **Parse test failures from build artifacts**
4. ✅ **Keep ReportPortal as fallback** for redundancy

## Architecture

```
┌─────────────────────┐
│  Prow CI / GCS      │  ← Test results + artifacts
└──────────┬──────────┘
           │
           ↓
┌─────────────────────┐
│  prow-mcp-server    │  ← MCP server with tools:
│  (port 3000)        │     - get_latest_job_run
└──────────┬──────────┘     - get_test_failures_from_artifacts
           │                 - get_job_logs
           ↓ HTTP/MCP
┌─────────────────────┐
│  Dashboard          │  ← ProwMCPCollector
│  (ProwMCPCollector) │     calls MCP server HTTP API
└─────────────────────┘
```

## New Components

### 1. ProwMCPCollector

**File:** `src/collectors/prow_mcp.py`

Collector that calls prow-mcp-server MCP tools via HTTP:

```python
class ProwMCPCollector(BaseCollector):
    def collect_job_runs(self, ...):
        # Calls: get_latest_job_run(job_name)

    def collect_test_results(self, ...):
        # Calls: get_test_failures_from_artifacts(job_name, build_id)
        # Calls: get_job_logs(job_name, build_id) for failed tests
```

### 2. Configuration

**File:** `config.yaml`

Added prow_mcp collector config:

```yaml
collector:
  type: "prow_mcp"  # Change from "reportportal" for POC

  prow_mcp:
    server_url: "http://prow-mcp-server:3000"  # MCP server endpoint
    job_names:
      - "periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-nightly-aws-ipi-ovn-winc-f7"
      - ...
    max_workers: 5
```

## Architecture Fix

**Status:** Fixed in fork https://github.com/rrasouli/prow-mcp-server

The original prow-mcp-server had a build issue on OpenShift amd64 nodes:

```
WARNING: image platform (linux/arm64/v8) does not match the expected platform (linux/amd64)
exec /bin/sh: exec format error
```

**Solution:**
Updated `Containerfile.sse` to explicitly use amd64 platform:

```dockerfile
FROM --platform=linux/amd64 registry.access.redhat.com/ubi9/python-312
```

This fix is in the fork and used by POC deployment manifests.

## Deployment Guide

### Prerequisites

1. OpenShift access (build10 cluster)
2. `oc` CLI logged in
3. Fork with architecture fix: https://github.com/rrasouli/prow-mcp-server

### Automated Deployment

**All deployment manifests are ready in `openshift/poc/`**

See complete deployment guide: [openshift/poc/README.md](openshift/poc/README.md)

**Quick deploy:**

```bash
# Navigate to POC manifests
cd dashboard/openshift/poc/

# Create POC namespace
oc new-project winc-dashboard-poc \
  --display-name="WINC Dashboard POC - Prow MCP" \
  --description="Testing prow-mcp-server integration"

# Create secrets
oc create secret generic reportportal-token \
  --from-literal=token="$REPORTPORTAL_API_TOKEN"

oc create secret generic webhook-secret \
  --from-literal=WebHookSecretKey=$(openssl rand -hex 20)

# Deploy prow-mcp-server
oc apply -f mcp-imagestream.yaml
oc apply -f mcp-buildconfig.yaml
oc apply -f mcp-service.yaml
oc apply -f mcp-deployment.yaml

# Start MCP server build
oc start-build prow-mcp-server

# Wait for build (watch in separate terminal)
oc get builds -w

# Deploy dashboard POC
oc apply -f dashboard-imagestream.yaml
oc apply -f dashboard-buildconfig.yaml
oc apply -f dashboard-configmap.yaml
oc apply -f dashboard-pvc.yaml
oc apply -f dashboard-service.yaml
oc apply -f dashboard-route.yaml
oc apply -f dashboard-deployment.yaml

# Start dashboard build
oc start-build winc-dashboard-poc

# Get POC dashboard URL
oc get route winc-dashboard-poc -o jsonpath='{.spec.host}'
```

**Configuration is pre-set:**
- Collector type: `prow_mcp`
- MCP server URL: `http://prow-mcp-server:3000`
- Job names: Same WINC periodic jobs as production
- Branch: `poc-prow-mcp`
- Fork: `rrasouli/prow-mcp-server` (with amd64 fix)

## Testing the POC

### 1. Check MCP Server Health

```bash
# From your local machine
oc port-forward svc/prow-mcp-server 3000:3000

# Test MCP server
curl http://localhost:3000/health

# Try calling an MCP tool
curl -X POST http://localhost:3000/mcp/tools/get_latest_job_run \
  -H "Content-Type: application/json" \
  -d '{"job_name": "periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-nightly-aws-ipi-ovn-winc-f7"}'
```

### 2. Test Dashboard Collection

Visit POC dashboard:
```
https://winc-dashboard-winc-dashboard-poc.apps.build10.ci.devcluster.openshift.com/
```

Click "Refresh Data" and watch logs:

```bash
oc logs -f deployment/winc-dashboard
```

Expected logs:
```
INFO - Starting data collection for 30 days
INFO - Using collector type: prow_mcp
INFO - Collecting job runs...
INFO - Collected X job runs
INFO - Collecting test results (fetching logs via MCP)...
INFO - Collected Y test results
INFO - Collection complete!
```

### 3. Compare with Production

**Production (ReportPortal):**
- https://winc-dashboard-winc-dashboard.apps.build10.ci.devcluster.openshift.com/

**POC (Prow MCP):**
- https://winc-dashboard-winc-dashboard-poc.apps.build10.ci.devcluster.openshift.com/

**Compare:**
- Number of tests collected
- Test logs quality (actual stdout vs AI summaries)
- Collection speed
- Data accuracy

## Expected Benefits

### Compared to ReportPortal:

1. ✅ **No deprecation risk** - Direct Prow/GCS access
2. ✅ **Actual test logs** - Not AI-generated summaries
3. ✅ **Faster** - Fewer API round trips
4. ✅ **Less maintenance** - MCP server handles complexity
5. ✅ **Same team** - redhat-community-ai-tools maintains prow-mcp-server

### Compared to Direct GCS:

1. ✅ **Cleaner code** - MCP handles auth, retry, parsing
2. ✅ **Built-in tools** - get_test_failures_from_artifacts, etc.
3. ✅ **Shared logic** - Other teams can use same MCP server
4. ✅ **Well-tested** - MCP server has its own test suite

## Fallback Strategy

The POC maintains ReportPortal as backup:

```yaml
# If MCP server is down, can switch back instantly:
collector:
  type: "reportportal"  # Switch back to stable

  reportportal:
    url: "https://reportportal-openshift..."
    # Existing config works
```

## Success Criteria

POC is successful if:

- [x] Architecture issue fixed (amd64 forced in Containerfile.sse)
- [x] Fork created with fix (rrasouli/prow-mcp-server)
- [x] Deployment manifests created (openshift/poc/)
- [x] ProwMCPCollector implemented
- [x] ConfigMap with prow_mcp configuration
- [ ] MCP server builds without errors
- [ ] MCP server responds to health checks
- [ ] Test results are collected (OCP-* tests)
- [ ] Actual logs are fetched (not AI summaries)
- [ ] Collection time is reasonable (<5 minutes)
- [ ] Dashboard displays correctly
- [ ] Data matches ReportPortal accuracy
- [ ] MCP server is stable under load

## Troubleshooting

### "Failed to connect to MCP server"

```bash
# Check MCP server is running
oc get pods -l app=prow-mcp-server

# Check logs
oc logs deployment/prow-mcp-server

# Verify service exists
oc get svc prow-mcp-server

# Test connectivity from dashboard pod
oc exec deployment/winc-dashboard -- curl -v http://prow-mcp-server:3000/health
```

### "MCP tool returned error"

```bash
# Check MCP server logs for errors
oc logs deployment/prow-mcp-server --tail=100

# Verify job names are correct
# MCP server needs exact Prow job names
```

### "No test results collected"

```bash
# Check dashboard logs
oc logs deployment/winc-dashboard --tail=100 | grep -i "prow_mcp"

# Verify MCP server can access GCS
# May need credentials for private buckets
```

## Next Steps

If POC is successful:

1. **Refine MCP integration** - Error handling, retries
2. **Performance tuning** - Parallel MCP calls
3. **Merge to main** - Add as option alongside ReportPortal
4. **Document** - Update README with prow_mcp instructions
5. **Consider default** - Make prow_mcp the default collector

If POC has issues:

1. **Debug** - Work with prow-mcp-server team
2. **Improve** - Contribute fixes to MCP server
3. **Alternative** - Fall back to direct GCS implementation
4. **Keep ReportPortal** - Continue using current stable solution

## Related Links

- prow-mcp-server: https://github.com/redhat-community-ai-tools/prow-mcp-server
- MCP Protocol: https://modelcontextprotocol.io/
- Production Dashboard: https://winc-dashboard-winc-dashboard.apps.build10.ci.devcluster.openshift.com/

## Contact

- POC Branch: `poc-prow-mcp`
- Questions: @rrasouli (WinC team)
