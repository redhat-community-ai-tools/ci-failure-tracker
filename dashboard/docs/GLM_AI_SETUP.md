# GLM AI Setup for AI-Powered Failure Analysis

The dashboard supports AI-powered failure analysis using GLM-5.3
served via RITS through the internal enMaas OpenAI-compatible gateway.

## Prerequisites

1. An enMaas API key with access to `rits/zai-org/glm-5-3`

## Setup Steps

### 1. Get an enMaas API Key

Request access to the internal enMaas AI gateway
(`api.enmaas.devshift.net`) and obtain an API key. You can verify your
key and the available models with:

```bash
# Set ENMAAS_KEY first if it is not already exported, without
# leaking the key to shell history:
read -rsp "enMaas API key: " ENMAAS_KEY && export ENMAAS_KEY && echo

curl -Ss https://api.enmaas.devshift.net/v1/models \
  -H "Authorization: Bearer $ENMAAS_KEY"
```

The model used by the dashboard is `rits/zai-org/glm-5-3`.

### 2. Create OpenShift Secret

Create the following secret in your OpenShift namespace. Store the
key in a permission-restricted file instead of passing it on the
command line, so it does not leak into shell history or process
arguments, and remove the file after the secret is created:

```bash
umask 077  # ensure the key file is readable by the owner only (0600)
printf '%s' 'YOUR_ENMAAS_API_KEY' > "$HOME/.enmaas-api-key"
oc create secret generic enmaas-api-key \
  --from-file=api-key="$HOME/.enmaas-api-key"
rm -f "$HOME/.enmaas-api-key"
```

Then apply the deployment config and restart the pod so the new
environment variable is loaded (a pod started before the secret
existed runs without `ENMAAS_KEY`):

```bash
oc apply -f openshift/poc/dashboard-deployment.yaml
oc rollout restart deployment/winc-dashboard-poc
```

### 3. Verify Configuration

The deployment will automatically pick up this secret if it exists. Check the logs:

```bash
oc logs deployment/winc-dashboard-poc -c dashboard | grep -i glm
```

You should see:
```
GLM client initialized (model: rits/zai-org/glm-5-3, endpoint: https://api.enmaas.devshift.net/v1/chat/completions)
```

## How It Works

The analyzer uses a 2-tier system:

1. **Pattern Matching (FREE, instant)** - Pre-classifiers catch known
   infrastructure patterns (SSH flakes, DNS failures, quota errors,
   timeouts, cross-platform correlation) with no AI call
2. **GLM AI (enMaas)** - Failures that reach the AI are analyzed
   with structured prompts

## Environment Variables

The following environment variable controls GLM access:

- `ENMAAS_KEY` - Your enMaas API key

## Configuration

The gateway endpoint and model are optional settings in
`dashboard/config.yaml` under the top-level `ai` section:

```yaml
ai:
  endpoint: "https://api.enmaas.devshift.net/v1/chat/completions"
  model: "rits/zai-org/glm-5-3"
```

Built-in code defaults match these values and are used when the
section is missing, so the keys can be freely removed. The endpoint
must be an https URL on the enMaas gateway host
(`api.enmaas.devshift.net`); any other value is ignored and the
built-in default endpoint is used, so a tampered ConfigMap cannot
redirect the API key to an external server. The model name is not
restricted.

## Migration Note

- The `analysis_mode` value for GLM results is `glm-ai`
  (previously `vertex-ai` under Vertex AI).
- Historical rows in the `ai_analyses` table are not reclassified.
  The `/api/analysis-stats` endpoint counts `glm-ai` alongside the
  legacy `anthropic-api` and `vertex-ai` mode strings in
  `api_count`, so older rows remain visible in statistics.
- Any external consumers filtering on `analysis_mode` must use the
  new value `glm-ai`.

## Testing

To test if GLM AI is working:

```bash
# Check if client initialized
oc logs deployment/winc-dashboard-poc -c dashboard | grep "GLM client initialized"

# Trigger an analysis
curl -X POST https://winc-dashboard-poc-winc-dashboard-poc.apps.build10.ci.devcluster.openshift.com/api/analyze-failure \
  -H "Content-Type: application/json" \
  -d '{
    "test_name": "OCP-71173",
    "platform": "vsphere",
    "version": "4.22",
    "error_message": "test error",
    "use_cached": false
  }'
```

Look for `"analysis_mode": "glm-ai"` in the response.

## Cost Estimation

- GLM calls go through the internal enMaas gateway (internal usage,
  no per-analysis Z.ai billing)
- Analyses are cached in the database anyway, saving latency
- Most failures are pre-classified by pattern matching (free) before
  reaching the AI

## Troubleshooting

### "enMaas API key not set"

The secret is optional. The system will work with pattern matching
alone, and non-pre-classified failures get flagged
`needs_human_review: true`.

### "GLM analysis failed"

Check:
1. Secret `enmaas-api-key` contains a valid enMaas API key
2. `curl https://api.enmaas.devshift.net/v1/models` succeeds with the
   same key
3. The key has not expired or exceeded rate limits
