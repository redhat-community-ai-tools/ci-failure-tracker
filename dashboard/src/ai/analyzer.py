"""
REAL AI analyzer - uses only Claude (local or Vertex AI), NO pattern matching
"""

import os
import re
import requests
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class HybridFailureAnalyzer:
    """
    Real AI failure analyzer using Google Vertex AI.

    Uses Claude via Vertex AI API (~$0.02 per analysis).
    NO pattern matching - real AI only.
    """

    def __init__(self):
        self.vertex_project_id = os.getenv('ANTHROPIC_VERTEX_PROJECT_ID')
        self.vertex_region = os.getenv('ANTHROPIC_VERTEX_REGION')

        # Initialize Anthropic client (for fallback)
        # Supports both direct API and Vertex AI
        if self.vertex_project_id and self.vertex_region:
            try:
                import anthropic
                self.claude_client = anthropic.AnthropicVertex(
                    project_id=self.vertex_project_id,
                    region=self.vertex_region
                )
                logger.info(f"Vertex AI client initialized (project: {self.vertex_project_id}, region: {self.vertex_region})")
            except ImportError:
                self.claude_client = None
                logger.warning("anthropic package not installed - API fallback unavailable")
            except Exception as e:
                self.claude_client = None
                logger.warning(f"Failed to initialize Vertex AI client: {e}")
        elif self.claude_api_key:
            try:
                import anthropic
                self.claude_client = anthropic.Anthropic(api_key=self.claude_api_key)
                logger.info("Anthropic API client initialized (fallback available)")
            except ImportError:
                self.claude_client = None
                logger.warning("anthropic package not installed - API fallback unavailable")
        else:
            self.claude_client = None
            logger.warning("Neither Vertex AI credentials nor CLAUDE_API_KEY set - API fallback unavailable")

    def analyze_failure(
        self,
        test_name: str,
        error_message: str,
        log_url: str,
        platform: str,
        version: str
    ) -> Dict[str, Any]:
        """
        Analyze failure using Vertex AI (Claude via Google Cloud).

        NO pattern matching - REAL AI only.
        Cost: ~$0.02 per analysis

        Args:
            test_name: Test identifier (e.g., OCP-39030)
            error_message: Error message from test failure
            log_url: URL to build logs
            platform: Platform (aws, azure, gcp, etc.)
            version: OpenShift version

        Returns:
            Analysis dictionary with root_cause, component, confidence, etc.
        """

        # Use Vertex AI for analysis
        logger.info(f"Analyzing {test_name} with Vertex AI")
        api_result = self._try_api_analysis(
            test_name, error_message, log_url, platform, version
        )

        if api_result:
            logger.info(f"✓ Used Vertex AI (cost: ~$0.024) for {test_name}")
            api_result['cost'] = 0.024  # Approximate cost with Sonnet
            api_result['analysis_mode'] = 'vertex-ai'
            return api_result

        # Vertex AI failed - return error
        logger.error(f"✗ Vertex AI analysis failed for {test_name}")
        return {
            'error': 'Vertex AI analysis failed',
            'root_cause': 'Vertex AI analysis failed - check credentials and quota',
            'component': 'vertex-ai',
            'confidence': 0,
            'analysis_mode': 'failed',
            'cost': 0.0
        }

    def _try_api_analysis(
        self,
        test_name: str,
        error_message: str,
        log_url: str,
        platform: str,
        version: str
    ) -> Optional[Dict[str, Any]]:
        """Fall back to Vertex AI API"""
        try:
            if not self.claude_client:
                logger.error("No Claude API client available")
                return None

            # Fetch logs (truncated for cost optimization)
            logs = ""
            if log_url:
                try:
                    response = requests.get(log_url, timeout=10)
                    if response.status_code == 200:
                        logs = response.text
                except Exception as e:
                    logger.warning(f"Failed to fetch logs from {log_url}: {e}")

            logs_excerpt = logs[-3000:] if len(logs) > 3000 else logs

            # Build prompt
            prompt = f"""Analyze this Windows Containers test failure in OpenShift CI.

**Test:** {test_name}
**Platform:** {platform}
**Version:** {version}
**Error:** {error_message}

**Build Logs (last 3000 chars):**
```
{logs_excerpt}
```

Provide analysis as JSON with these exact fields:
{{
  "root_cause": "1-2 sentence description of what caused the failure",
  "component": "affected component name (e.g., windows-machine-config-operator, kubelet, csi-driver)",
  "confidence": 85,
  "failure_type": "product_bug",
  "platform_specific": true,
  "affected_platforms": ["azure"],
  "evidence": "Key log lines that show the problem",
  "suggested_action": "What should be done next",
  "issue_title": "Bug: [brief description]",
  "issue_description": "Detailed description for Jira/GitHub issue"
}}

Only return the JSON, no additional text.
"""

            # Call Claude API
            response = self.claude_client.messages.create(
                model="claude-sonnet-4",  # Cheaper, still capable
                max_tokens=2048,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )

            # Parse response
            response_text = response.content[0].text

            # Extract JSON from response
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                analysis = json.loads(json_match.group(1))
                logger.debug(f"API analysis succeeded: {analysis.get('root_cause', '')[:100]}")
                return analysis

            # Try parsing entire response as JSON
            try:
                analysis = json.loads(response_text)
                logger.debug(f"API analysis (direct JSON): {analysis.get('root_cause', '')[:100]}")
                return analysis
            except json.JSONDecodeError:
                # Fallback: return raw text
                logger.warning("Could not parse API response as JSON, returning raw text")
                return {
                    'root_cause': response_text[:200],
                    'raw_analysis': response_text,
                    'component': 'unknown',
                    'confidence': 50,
                    'failure_type': 'unknown',
                    'platform_specific': False,
                    'affected_platforms': [platform],
                    'evidence': 'See raw_analysis',
                    'suggested_action': 'Manual investigation needed'
                }

        except Exception as e:
            logger.error(f"API analysis error: {e}")
            return None

