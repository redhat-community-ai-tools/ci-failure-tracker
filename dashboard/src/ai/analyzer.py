"""
Hybrid AI analyzer that tries local Claude Code first, then falls back to API
"""

import os
import requests
import json
import re
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class HybridFailureAnalyzer:
    """
    Hybrid AI analyzer with 3-tier fallback:
    1. Try local Claude Code service first (FREE)
    2. Fall back to Anthropic API if local not available (~$0.02)
    3. Fall back to built-in pattern matching if API not available (FREE)
    """

    def __init__(self):
        self.local_service_url = os.getenv('LOCAL_AI_SERVICE_URL', 'http://localhost:5001')
        self.claude_api_key = os.getenv('CLAUDE_API_KEY')
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

    def _check_local_service(self) -> bool:
        """Check if local Claude Code service is running"""
        try:
            response = requests.get(
                f"{self.local_service_url}/health",
                timeout=2  # Quick timeout
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"Local service check failed: {e}")
            return False

    def analyze_failure(
        self,
        test_name: str,
        error_message: str,
        log_url: str,
        platform: str,
        version: str
    ) -> Dict[str, Any]:
        """
        Analyze failure using 2-tier REAL AI approach:
        1. Try local Claude Code service first (FREE, requires me to be actively processing queue)
        2. Fall back to Vertex AI API (cost: ~$0.02 per analysis)

        NO pattern matching fallback - REAL AI only per user requirement.

        Args:
            test_name: Test identifier (e.g., OCP-39030)
            error_message: Error message from test failure
            log_url: URL to build logs
            platform: Platform (aws, azure, gcp, etc.)
            version: OpenShift version

        Returns:
            Analysis dictionary with root_cause, component, confidence, etc.
        """

        # Try local service first (queue-based, requires Claude Code to be monitoring)
        logger.info(f"Attempting local Claude Code analysis for {test_name}")
        local_result = self._try_local_analysis(
            test_name, error_message, log_url, platform, version
        )

        if local_result:
            logger.info(f"✓ Used local Claude Code (FREE) for {test_name}")
            local_result['cost'] = 0.0
            local_result['analysis_mode'] = 'local-claude-code'
            return local_result

        # Fall back to Vertex AI API
        logger.info(f"Local service unavailable, using Vertex AI API for {test_name}")
        api_result = self._try_api_analysis(
            test_name, error_message, log_url, platform, version
        )

        if api_result:
            logger.info(f"✓ Used Vertex AI (cost: ~$0.024) for {test_name}")
            api_result['cost'] = 0.024  # Approximate cost with Sonnet
            api_result['analysis_mode'] = 'vertex-ai'
            return api_result

        # No AI available - fail with clear error
        logger.error(f"✗ No AI analysis available for {test_name} - check Vertex AI credentials")
        return {
            'error': 'AI analysis unavailable - check Vertex AI configuration',
            'root_cause': 'Real AI analysis failed - verify ANTHROPIC_VERTEX_PROJECT_ID and credentials',
            'component': 'ai-service',
            'confidence': 0,
            'analysis_mode': 'failed',
            'cost': 0.0
        }

    def _try_local_analysis(
        self,
        test_name: str,
        error_message: str,
        log_url: str,
        platform: str,
        version: str
    ) -> Optional[Dict[str, Any]]:
        """Try to use local Claude Code service"""
        try:
            # Check if service is running
            if not self._check_local_service():
                logger.debug("Local service not running")
                return None

            # Call local service
            response = requests.post(
                f"{self.local_service_url}/analyze",
                json={
                    'test_name': test_name,
                    'error_message': error_message,
                    'log_url': log_url,
                    'platform': platform,
                    'version': version
                },
                timeout=10  # Quick check - fall back to pattern matching if no immediate response
            )

            if response.status_code == 200:
                result = response.json()
                logger.debug(f"Local analysis succeeded: {result.get('root_cause', '')[:100]}")
                return result
            elif response.status_code == 202:
                # Analysis queued - service will wait internally
                result = response.json()
                logger.info(f"Analysis queued: {result.get('message', '')}")
                # The /analyze endpoint waits up to 60s, so if we got 202 it timed out
                # Return None to fall back to pattern analysis
                return None
            else:
                logger.warning(f"Local service returned {response.status_code}: {response.text[:200]}")
                return None

        except requests.exceptions.Timeout:
            logger.warning("Local service timeout")
            return None
        except Exception as e:
            logger.debug(f"Local service error: {e}")
            return None

    def _try_api_analysis(
        self,
        test_name: str,
        error_message: str,
        log_url: str,
        platform: str,
        version: str
    ) -> Optional[Dict[str, Any]]:
        """Fall back to Anthropic API"""
        try:
            if not self.claude_client:
                logger.error("No Claude API client available")
                return None

            # Fetch logs (truncated for cost optimization)
            logs = self._fetch_logs(log_url)
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

    def _fetch_logs(self, log_url: str) -> str:
        """Fetch build logs from URL"""
        try:
            response = requests.get(log_url, timeout=10)
            if response.status_code == 200:
                return response.text
        except Exception as e:
            logger.warning(f"Failed to fetch logs from {log_url}: {e}")
        return ""

    def _pattern_analysis(
        self,
        test_name: str,
        error_message: str,
        log_content: str,
        platform: str,
        version: str
    ) -> Optional[Dict[str, Any]]:
        """
        Analyze Windows failure using pattern matching
        This is a free fallback when API and local service are unavailable
        """
        root_cause = "Unknown failure"
        component = "windows-machine-config-operator"
        confidence = 60
        failure_type = "needs_investigation"
        platform_specific = False
        affected_platforms = [platform]
        evidence = ""
        suggested_action = "Further investigation needed"

        # Combine error message and logs for analysis
        combined_text = f"{error_message}\n{log_content[-2000:]}".lower()

        # Pattern matching for common Windows failures

        # SSH/Connection failures (exit 255) - usually transient
        if "exit status 255" in combined_text or "ssh" in combined_text and "failed" in combined_text:
            root_cause = "SSH connection failure to Windows node - likely transient infrastructure issue"
            component = "infrastructure"
            confidence = 50  # Low confidence - likely flake
            failure_type = "system_issue"
            platform_specific = False
            evidence = "Exit status 255 indicates SSH connection failure"
            suggested_action = "Retry test - likely transient network/SSH issue, not a product bug"

        elif "timeout" in combined_text or "timed out" in combined_text:
            if "azure" in combined_text or "disk" in combined_text:
                root_cause = "Azure CSI driver timeout when mounting volumes to Windows pod"
                component = "azure-csi-driver"
                confidence = 85
                failure_type = "product_bug"
                platform_specific = True
                affected_platforms = ["azure"]
                evidence = "Logs show timeout waiting for Azure disk mount"
                suggested_action = "Increase CSI driver timeout from 2m to 5m for Azure Windows nodes"
            else:
                root_cause = "Operation timeout - likely network or storage issue"
                confidence = 70
                failure_type = "system_issue"
                evidence = "Timeout error detected in logs"
                suggested_action = "Check network connectivity and storage performance"

        elif "connection refused" in combined_text or "connection reset" in combined_text:
            root_cause = "Network connectivity issue - connection refused or reset"
            component = "networking"
            confidence = 75
            failure_type = "system_issue"
            evidence = "Connection errors in logs"
            suggested_action = "Check network policies and firewall rules for Windows nodes"

        elif "image pull" in combined_text or "imagepullbackoff" in combined_text:
            root_cause = "Container image pull failure"
            component = "container-runtime"
            confidence = 90
            failure_type = "system_issue"
            evidence = "Image pull errors in logs"
            suggested_action = "Verify image registry accessibility and credentials"

        elif "permission denied" in combined_text or "access denied" in combined_text:
            root_cause = "Permission or access denied error"
            component = "rbac"
            confidence = 80
            failure_type = "configuration"
            evidence = "Permission errors in logs"
            suggested_action = "Review RBAC policies and Windows node permissions"

        elif "pod" in combined_text and ("not ready" in combined_text or "failed" in combined_text):
            root_cause = "Windows pod failed to reach ready state"
            component = "windows-machine-config-operator"
            confidence = 75
            failure_type = "product_bug"
            evidence = "Pod readiness failure in logs"
            suggested_action = "Check pod events and Windows node kubelet logs"

        elif "wmco" in combined_text or "windows-machine-config" in combined_text:
            root_cause = "Windows Machine Config Operator issue"
            component = "windows-machine-config-operator"
            confidence = 80
            failure_type = "product_bug"
            evidence = "WMCO errors in logs"
            suggested_action = "Review WMCO operator logs and Windows node configuration"

        elif "kubelet" in combined_text and ("ca" in combined_text or "certificate" in combined_text):
            root_cause = "Kubelet certificate or CA rotation issue"
            component = "kubelet"
            confidence = 85
            failure_type = "product_bug"
            evidence = "Kubelet CA or certificate errors in logs"
            suggested_action = "Verify kubelet CA bundle and certificate rotation configuration"

        elif "containerd" in combined_text and "service" in combined_text:
            if "exit status 255" in combined_text:
                root_cause = "Failed to check containerd service status - SSH connection failure (transient)"
                component = "infrastructure"
                confidence = 40
                failure_type = "system_issue"
                evidence = "SSH failure when checking containerd service"
                suggested_action = "Retry test - SSH connection issue, not containerd problem"
            else:
                root_cause = "Containerd service issue on Windows node"
                component = "containerd"
                confidence = 75
                failure_type = "system_issue"
                evidence = "Containerd service errors in logs"
                suggested_action = "Check containerd service status and logs on Windows node"

        elif "unable to connect" in combined_text or "connection timed out" in combined_text:
            root_cause = "Network connection timeout - likely transient network issue"
            component = "networking"
            confidence = 45
            failure_type = "system_issue"
            evidence = "Connection timeout in logs"
            suggested_action = "Retry test - likely transient network issue"

        # Build issue template
        if failure_type == "system_issue" and confidence < 60:
            issue_title = f"System Issue: {test_name} - {root_cause[:50]}"
            flake_note = f"\n**⚠️ LIKELY TRANSIENT SYSTEM ISSUE (Confidence: {confidence}%)** - This appears to be infrastructure/network related, not a product bug. Consider retrying before filing.\n"
        elif failure_type == "system_issue":
            issue_title = f"System Issue: {test_name} - {root_cause[:50]}"
            flake_note = ""
        else:
            issue_title = f"{test_name} fails on {platform} - {root_cause[:50]}"
            flake_note = ""

        issue_description = f"""## Test Failure Analysis
{flake_note}
**Test:** {test_name}
**Platform:** {platform}
**Version:** {version}
**Confidence:** {confidence}%

## Root Cause
{root_cause}

## Component
{component}

## Evidence
{evidence}

## Failure Classification
- Type: {failure_type}
- Platform Specific: {"Yes" if platform_specific else "No"}
- Affected Platforms: {', '.join(affected_platforms)}

## Suggested Action
{suggested_action}

## Error Details
```
{error_message[:500]}
```

## Analysis Method
Pattern matching (built-in fallback)
"""

        return {
            "root_cause": root_cause,
            "component": component,
            "confidence": confidence,
            "failure_type": failure_type,
            "platform_specific": platform_specific,
            "affected_platforms": affected_platforms,
            "evidence": evidence,
            "suggested_action": suggested_action,
            "issue_title": issue_title,
            "issue_description": issue_description
        }
