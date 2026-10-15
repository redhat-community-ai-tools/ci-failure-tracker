# AI-Powered Failure Analysis

The dashboard includes AI-powered failure analysis that can help identify root causes and suggest fixes.

## How It Works

The system uses a **3-tier fallback approach**:

1. **Try Local First (FREE)** - If you're running the local AI service, it uses Claude Code for free analysis
2. **Fall Back to API (Small Cost)** - If local service isn't running AND API key is set, uses Anthropic API (~$0.02 per analysis)
3. **Fall Back to Pattern Matching (FREE)** - If API key isn't set, uses built-in pattern matching for common Windows failures

**No API key required!** The pattern matching fallback means analysis always works, even without an Anthropic API key.

## Setup

### No Setup Required (Pattern Matching Mode)

The "AI Analyze" button works out of the box with **built-in pattern matching**:
- Detects common Windows failures (timeouts, connection issues, CSI driver problems, etc.)
- Identifies affected components
- Suggests fixes
- **Completely FREE**
- **No API key needed**

Just click "AI Analyze" and it will work!

### Optional: API Mode (Higher Quality Analysis)

If you want higher quality analysis using Anthropic's Claude API:

1. Get an Anthropic API key from: https://console.anthropic.com/settings/keys

2. Create OpenShift secret in POC:
```bash
oc project winc-dashboard-poc
oc create secret generic claude-api-key \
  --from-literal=api-key=sk-ant-api03-YOUR_KEY_HERE
```

3. Apply deployment config:
```bash
oc apply -f openshift/poc/dashboard-deployment.yaml
```

**Cost:** ~$0.02 per analysis (pattern matching is still FREE fallback)

## Using the Feature

1. Open POC dashboard: https://winc-dashboard-poc-winc-dashboard-poc.apps.build10.ci.devcluster.openshift.com

2. Go to Weekly Report tab

3. Click on a platform (e.g., AWS, Azure)

4. Find a failing test (pass rate < 100%)

5. Click "🤖 Analyze" button

6. Wait a few seconds for analysis

7. Review:
   - Root cause
   - Affected component  
   - Confidence level
   - Evidence from logs
   - Suggested action
   - Auto-generated issue template

8. Click "Copy Issue Template" to copy to clipboard for Jira/GitHub

## Analysis Modes

### Local Claude Code Mode (FREE)
```
🆓 FREE (Local Claude Code)
```
- Uses your local Claude Code instance
- No API costs
- Only works when you're running the local service
- Perfect for development and debugging

### Anthropic API Mode ($0.02)
```
💰 API (~$0.02)
```
- Uses Anthropic's Claude API
- Small cost per analysis (~$0.024)
- Always available
- Works for all team members
- Perfect for production

## Cost Tracking

View analysis statistics at:
```bash
curl https://winc-dashboard-poc-winc-dashboard-poc.apps.build10.ci.devcluster.openshift.com/api/analysis-stats
```

Returns:
```json
{
  "total_analyses": 50,
  "local_count": 30,
  "api_count": 20,
  "total_cost": 0.48,
  "savings": 0.72
}
```

## Example Analysis

```json
{
  "root_cause": "Windows pod failed to mount projected volume due to Azure CSI driver timeout",
  "component": "windows-machine-config-operator",
  "confidence": 85,
  "failure_type": "product_bug",
  "platform_specific": true,
  "affected_platforms": ["azure"],
  "evidence": "Log shows: 'MountVolume.SetUp failed: timed out waiting for Azure disk'",
  "suggested_action": "Increase CSI driver timeout for Azure from 2m to 5m",
  "issue_title": "Bug: Windows pod fails to mount projected volume on Azure",
  "issue_description": "Detailed description..."
}
```

## Troubleshooting

### "Analysis failed" error

**Check:**
1. Is Claude API key set in OpenShift secret?
2. Is the secret mounted to the pod?
3. Are there any errors in pod logs?

```bash
oc project winc-dashboard-poc
oc get secret claude-api-key
oc logs deployment/winc-dashboard-poc -c dashboard | grep -i "claude\|anthropic"
```

### Local service not being used

**Check:**
1. Is local service running? (`http://localhost:5001/health`)
2. Is dashboard trying to connect to it?
3. Check if LOCAL_AI_SERVICE_URL is set correctly

```bash
# Test local service
curl http://localhost:5001/health

# Should return:
# {"status":"ok","mode":"local-claude-code"}
```

### Analyses always show "cached"

The system caches analyses for 7 days to save costs. The cache is also
automatically invalidated when the underlying error message changes
(hash-based invalidation), so a new failure signature triggers a fresh
analysis without manual intervention.

To force a fresh analysis manually, click the **Re-analyze** button that
appears after a successful analysis. This bypasses the cache and requests
a new analysis from the AI service.

Alternatively, you can use the browser console:

```javascript
// In browser console:
fetch('/api/analyze-failure', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    test_name: 'OCP-39030',
    version: '4.22',
    platform: 'azure',
    use_cached: false  // Force fresh analysis
  })
})
```

## Future Enhancements

Planned features:
- Batch analysis for all failing tests
- Weekly automated analysis reports
- Pattern detection across similar failures
- Auto-filing Jira issues
- GitHub code search integration for test source
