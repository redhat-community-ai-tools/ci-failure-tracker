"""
AI analyzer with SSH infrastructure pre-classifier.

Pre-classifies SSH connectivity flakes as transient infrastructure issues
before sending to Vertex AI, saving API cost and improving accuracy.
"""

import os
import re
import requests
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

SSH_PATTERNS = [
    re.compile(r'SSH attempt \d+ failed', re.IGNORECASE),
    re.compile(r'exit status 255'),
    re.compile(r'ssh:.*connection refused', re.IGNORECASE),
    re.compile(r'ssh:.*connection timed out', re.IGNORECASE),
    re.compile(r'ssh:.*no route to host', re.IGNORECASE),
    re.compile(r'bastion.*failed', re.IGNORECASE),
    re.compile(r'bastion.*timed? out', re.IGNORECASE),
    re.compile(r'failed to connect.*ssh', re.IGNORECASE),
    re.compile(r'dial tcp.*:22.*connection refused', re.IGNORECASE),
    re.compile(r'kex_exchange_identification', re.IGNORECASE),
    re.compile(r'connection reset by.*port 22', re.IGNORECASE),
]

ASSERTION_PATTERNS = [
    re.compile(r'Expected\s*$|o\.Expect\(', re.MULTILINE),
    re.compile(r'e2e\.Failf\('),
    re.compile(r'gomega.*to.*equal|gomega.*to.*contain', re.IGNORECASE),
    re.compile(r'FAIL!.*Expected'),
    re.compile(r'Unexpected error:'),
]


def _fetch_logs(log_url: str) -> str:
    """Fetch build logs from URL, return empty string on failure."""
    if not log_url:
        return ''
    try:
        headers = {}
        api_token = os.environ.get('API_KEY')
        if api_token and 'qe-private-deck' in log_url:
            headers['Authorization'] = f'Bearer {api_token}'
        response = requests.get(log_url, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.text
    except Exception:
        pass
    return ''


def detect_ssh_flake(error_message: str, pass_rate: Optional[float] = None, log_url: str = None) -> Optional[Dict[str, Any]]:
    """
    Pre-classify SSH infrastructure flakes.

    Checks both the error_message and the build logs for SSH patterns.
    Returns a pre-built analysis dict if SSH flake detected, None otherwise.
    """
    if not error_message:
        return None

    combined_text = error_message
    logs = ''
    ssh_matches = [p.pattern for p in SSH_PATTERNS if p.search(error_message)]

    if not ssh_matches and log_url:
        logs = _fetch_logs(log_url)
        if logs:
            combined_text = error_message + '\n' + logs[-3000:]
            ssh_matches = [p.pattern for p in SSH_PATTERNS if p.search(combined_text)]

    if not ssh_matches:
        return None

    assertion_in_error = any(p.search(error_message) for p in ASSERTION_PATTERNS)
    ssh_in_error = any(p.search(error_message) for p in SSH_PATTERNS)

    if assertion_in_error and not ssh_in_error:
        return None

    if pass_rate is not None and pass_rate < 65.0:
        return None

    ssh_only_in_logs = not ssh_in_error and len(ssh_matches) > 0
    logger.info(f"Pre-classified as SSH infrastructure flake (pass_rate={pass_rate}, ssh_patterns={len(ssh_matches)}, ssh_in_logs_only={ssh_only_in_logs})")

    root_cause = ('SSH connectivity failure to Windows node via bastion host. '
                  'Test logic never reached an assertion -- the failure is purely infrastructure.')
    if ssh_only_in_logs:
        root_cause = ('SSH connectivity failures found in build logs caused downstream test assertion to fail. '
                      'The test assertion failed because SSH to the Windows node was unstable, not due to a product bug.')

    return {
        'root_cause': root_cause,
        'component': 'test-infrastructure (SSH connectivity)',
        'confidence': 92,
        'failure_type': 'transient',
        'classification': 'transient',
        'platform_specific': False,
        'affected_platforms': [],
        'evidence': '; '.join(ssh_matches[:3]),
        'suggested_action': 'Retry. Track under WINC-1931 for SSH elimination.',
        'issue_title': 'Transient: SSH connectivity flake to Windows node',
        'issue_description': 'SSH connection to Windows node failed before test logic executed. '
                             'This is a known transient infrastructure issue, not a product bug.',
        'is_product_bug': False,
        'pre_classified': True,
        'pre_classifier': 'ssh_flake_detector',
        'cost': 0.0,
        'analysis_mode': 'pre-classifier',
    }


class HybridFailureAnalyzer:
    """
    Real AI failure analyzer using Google Vertex AI.

    Uses Claude via Vertex AI API (~$0.02 per analysis).
    NO pattern matching - real AI only.
    """

    def __init__(self):
        self.vertex_project_id = os.getenv('ANTHROPIC_VERTEX_PROJECT_ID')
        self.vertex_region = os.getenv('ANTHROPIC_VERTEX_REGION')

        # Initialize Vertex AI client
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
                logger.warning("anthropic[vertex] package not installed - run: pip install 'anthropic[vertex]'")
            except Exception as e:
                self.claude_client = None
                logger.warning(f"Failed to initialize Vertex AI client: {e}")
        else:
            self.claude_client = None
            logger.warning("Vertex AI credentials not set. Set ANTHROPIC_VERTEX_PROJECT_ID and ANTHROPIC_VERTEX_REGION environment variables.")

    def analyze_failure(
        self,
        test_name: str,
        error_message: str,
        log_url: str,
        platform: str,
        version: str,
        pass_rate: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Analyze failure using pre-classifier + Vertex AI (Claude via Google Cloud).

        Step 1: Check for known infrastructure patterns (SSH flakes) -- free, instant
        Step 2: If not pre-classified, use Vertex AI (~$0.02 per analysis)

        Args:
            test_name: Test identifier (e.g., OCP-39030)
            error_message: Error message from test failure
            log_url: URL to build logs
            platform: Platform (aws, azure, gcp, etc.)
            version: OpenShift version
            pass_rate: Test pass rate (used by pre-classifier to confirm transient)

        Returns:
            Analysis dictionary with root_cause, component, confidence, etc.
        """

        # Step 1: Pre-classify known infrastructure patterns
        pre_result = detect_ssh_flake(error_message, pass_rate, log_url)
        if pre_result:
            logger.info(f"Pre-classified {test_name} as SSH flake (skipping Vertex AI, saved ~$0.024)")
            return pre_result

        # Step 2: Use Vertex AI for analysis
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
            logs = _fetch_logs(log_url)

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
  "failure_type": "product_bug OR automation_bug OR system_issue OR transient OR to_investigate",
  "platform_specific": true,
  "affected_platforms": ["azure"],
  "evidence": "Key log lines that show the problem",
  "suggested_action": "What should be done next",
  "issue_title": "Bug: [brief description]",
  "issue_description": "Detailed description for Jira/GitHub issue"
}}

**Classification guidelines:**
- product_bug: Bug in OpenShift/Windows Container product code
- automation_bug: Bug in the test automation code itself (e.g., wrong assertions, test setup issues)
- system_issue: Infrastructure/environment issues (network, storage, DNS, etc.)
- transient: Flaky/intermittent issues, timing problems, resource contention
- to_investigate: Not enough information to classify

**SSH infrastructure flake detection:**
If the logs show SSH connectivity failures (exit status 255, bastion timeouts, connection refused on port 22)
AND the test never reached a real assertion (no Expect/Failf triggered), classify as "transient" with
component "test-infrastructure (SSH connectivity)" -- NOT as a product bug in WMCO or hybrid-overlay.
SSH flakes are infrastructure issues, not product bugs.

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
                # Map failure_type to classification for frontend compatibility
                if 'failure_type' in analysis:
                    analysis['classification'] = analysis['failure_type']
                logger.debug(f"API analysis succeeded: {analysis.get('root_cause', '')[:100]}")
                return analysis

            # Try parsing entire response as JSON
            try:
                analysis = json.loads(response_text)
                # Map failure_type to classification for frontend compatibility
                if 'failure_type' in analysis:
                    analysis['classification'] = analysis['failure_type']
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
                    'classification': 'to_investigate',
                    'failure_type': 'to_investigate',
                    'platform_specific': False,
                    'affected_platforms': [platform],
                    'evidence': 'See raw_analysis',
                    'suggested_action': 'Manual investigation needed'
                }

        except Exception as e:
            logger.error(f"API analysis error: {e}")
            return None

