# AI Service Setup on OpenShift POC

This document previously described deploying a local Claude-based
"ai-service" (MCP queue) alongside the dashboard, wired up with the
`LOCAL_AI_SERVICE_URL` environment variable.

That architecture was removed. The AI failure analyzer now calls the
internal enMaas OpenAI-compatible gateway directly (model
`rits/zai-org/glm-5-3`), configured through the `ENMAAS_KEY`
environment variable.

**See [dashboard/docs/GLM_AI_SETUP.md](../../docs/GLM_AI_SETUP.md)
for the current setup guide**, including:

- Creating the `enmaas-api-key` OpenShift secret
- Applying the deployment and rolling out a restart to load the new
  environment variable
- Verifying the analyzer with pod logs
- Troubleshooting failed analyses
