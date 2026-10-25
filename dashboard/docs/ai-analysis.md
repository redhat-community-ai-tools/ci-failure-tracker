# AI-Powered Failure Analysis

The dashboard includes AI-powered failure analysis that can help identify root causes and suggest fixes.

## How It Works

The system uses a **2-tier approach**:

1. **Pattern Matching (FREE, instant)** - Known infrastructure failure
   patterns (SSH flakes, DNS failures, cloud quota errors, timeout
   flake patterns, cross-platform failure correlation) are caught by
   built-in pre-classifiers with no AI call
2. **GLM AI (enMaas)** - Failures that are not pre-classified are
   analyzed with GLM-5.3 via the internal enMaas OpenAI-compatible
   gateway (model `rits/zai-org/glm-5-3`)

**No API key required!** Pattern matching always works, even without
`ENMAAS_KEY` set. Failures that are not pre-classified are flagged
`needs_human_review` in that case.

## Setup

### No Setup Required (Pattern Matching Mode)

The "AI Analyze" button works out of the box with **built-in pattern matching**:
- Detects common Windows failures (timeouts, connection issues, CSI driver problems, etc.)
- Identifies affected components
- Suggests fixes
- **Completely FREE**
- **No API key needed**

Just click "AI Analyze" and it will work!

### Optional: GLM Mode (Full Analysis for New Failure Patterns)

For higher-quality analysis of failures the pre-classifiers don't recognize:

1. Get an enMaas API key with access to the RITS GLM model (see
   [GLM_AI_SETUP.md](GLM_AI_SETUP.md))

2. Create OpenShift secret in POC:
```bash
oc project winc-dashboard-poc
oc create secret generic enmaas-api-key \
  --from-literal=api-key=YOUR_ENMAAS_API_KEY
```

3. Apply deployment config and restart the pod to load the new env var:
```bash
oc apply -f openshift/poc/dashboard-deployment.yaml
oc rollout restart deployment/winc-dashboard-poc
```

**Verify:** check the logs for `GLM client initialized (model: rits/zai-org/glm-5-3, endpoint: https://api.enmaas.devshift.net/v1/chat/completions)`.

## Using the Feature

1. Open POC dashboard: https://winc-dashboard-poc-winc-dashboard-poc.apps.build10.ci.devcluster.openshift.com

2. Go to Weekly Report tab

3. Click on a platform (e.g., AWS, Azure)

4. Find a failing test (pass rate < 100%)

5. Click "AI Analyze" button

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

### Pattern Matching Mode (FREE)
- Runs first on every first-time analysis; bypassed when Re-analyze
  is used (see below)
- Pre-classifiers catch SSH/DNS/quota/timeout flakes and
  cross-platform correlations
- Results carry `analysis_mode: "pre-classifier"`
- Perfect default for CI triage

### GLM Mode (enMaas)
```
analysis_mode: "glm-ai"
```
- Uses GLM-5.3 via the internal enMaas gateway
- Used for failures the pre-classifiers cannot classify, and also
  invoked unconditionally during explicit re-analysis (Re-analyze
  bypasses pre-classifiers)
- Structured prompts with historical pass rate and test repo context
- Low-confidence results (below 60) are flagged
  `needs_human_review: true`

## Analysis Statistics

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
  "total_cost": 0.0,
  "savings": 0.72
}
```

Note: `api_count` includes all AI analyses regardless of backend,
including legacy mode strings (`anthropic-api`, `vertex-ai`) and the
current `glm-ai`.

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
1. Is `ENMAAS_KEY` set in the OpenShift secret?
2. Is the secret mounted to the pod?
3. Are there any errors in pod logs?

```bash
oc project winc-dashboard-poc
oc get secret enmaas-api-key
oc logs deployment/winc-dashboard-poc -c dashboard | grep -i glm
```

You can also verify the gateway is reachable with:
```bash
curl -Ss https://api.enmaas.devshift.net/v1/models \
  -H "Authorization: Bearer $ENMAAS_KEY"
```

### Analyses always show "cached"

The system caches analyses for 7 days to save latency. The cache is
also automatically invalidated when the underlying error message
changes (hash-based invalidation), so a new failure signature triggers
a fresh analysis without manual intervention.

**Note:** Analyses that failed (e.g., due to API errors or missing
credentials, stored with `analysis_mode='failed'`) are excluded from
cache serving. If a previous analysis attempt failed, the next request
for that test will trigger a fresh analysis automatically rather than
returning the failed result.

To force a fresh analysis manually, click the **Re-analyze** button
that appears after a successful analysis. This bypasses the cache,
skips all offline pre-classifiers (SSH, DNS, timeout, known-flaky,
cross-platform), and sends the failure directly to GLM for a fresh
AI analysis.

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
