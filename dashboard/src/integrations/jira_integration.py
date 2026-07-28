"""
Jira Integration for CI Failure Tracker

Allows creating Jira issues for failing tests with duplicate detection.
"""

import os
import logging
import requests
import json
from typing import Optional, Dict, List
from dataclasses import dataclass
import base64

logger = logging.getLogger(__name__)


@dataclass
class JiraConfig:
    """Jira configuration"""
    url: str
    project_key: str
    issue_type: str = "Bug"
    component: Optional[str] = None
    priority: str = "Major"


class JiraIntegration:
    """
    Jira integration for filing bugs for failing tests.

    Features:
    - Check for existing Jira before creating new one
    - Link test failure to existing Jira if found
    - Create new Jira with test details if none exists
    """

    def __init__(self, config: JiraConfig):
        self.config = config
        self.enabled = self._check_credentials()

    def _check_credentials(self) -> bool:
        """Check if Jira credentials are available"""
        # Check for Jira API token
        self.jira_token = os.environ.get('JIRA_API_TOKEN')
        self.jira_email = os.environ.get('JIRA_EMAIL', 'automation@redhat.com')  # Default email for API calls

        if not self.jira_token:
            logger.warning("Jira integration disabled: Missing JIRA_API_TOKEN environment variable")
            return False

        return True

    def _get_headers(self) -> Dict[str, str]:
        """Get authentication headers for Jira API"""
        # Use Basic Auth with email + API token
        auth_string = f"{self.jira_email}:{self.jira_token}"
        auth_bytes = auth_string.encode('utf-8')
        auth_b64 = base64.b64encode(auth_bytes).decode('utf-8')

        return {
            'Authorization': f'Basic {auth_b64}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

    def search_existing_issue(self, test_name: str, version: str, platform: str = None) -> Optional[Dict]:
        """
        Search for existing Jira issue for this test failure.

        Args:
            test_name: Test ID (e.g., OCP-12345)
            version: OCP version (e.g., 4.22)
            platform: Platform (ignored - issues are searched by test name only)

        Returns:
            Jira issue dict if found, None otherwise
        """
        if not self.enabled:
            return None

        # JQL query to find issues with this test name in summary or description.
        # Search both fields to catch manually-filed issues whose summary uses
        # the error message instead of the test ID (the test ID still appears
        # in the description).
        # Add time restriction to avoid "unbounded query" error.
        jql = (
            f'project = {self.config.project_key}'
            f' AND (summary ~ "{test_name}" OR description ~ "{test_name}")'
            f' AND resolution = Unresolved AND created > -90d'
        )

        try:
            logger.info(f"Searching for existing Jira: {jql}")

            # Call Jira search API (v3) - Use new /search/jql endpoint
            search_url = f"{self.config.url}/rest/api/3/search/jql"
            response = requests.post(
                search_url,
                headers=self._get_headers(),
                json={'jql': jql, 'maxResults': 1, 'fields': ['key', 'summary']},
                timeout=30,
                allow_redirects=False
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('issues'):
                    issue = data['issues'][0]
                    logger.info(f"Found existing Jira: {issue['key']}")
                    return {'key': issue['key'], 'summary': issue['fields']['summary']}
                else:
                    logger.info("No existing Jira found")
                    return None
            elif response.status_code in (301, 302, 303, 307, 308):
                # Handle redirect
                redirect_url = response.headers.get('Location')
                logger.warning(f"Search redirected to: {redirect_url}")
                if redirect_url:
                    response = requests.post(
                        redirect_url,
                        headers=self._get_headers(),
                        json={'jql': jql, 'maxResults': 1, 'fields': ['key', 'summary']},
                        timeout=30,
                        allow_redirects=False
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('issues'):
                            issue = data['issues'][0]
                            logger.info(f"Found existing Jira (after redirect): {issue['key']}")
                            return {'key': issue['key'], 'summary': issue['fields']['summary']}
                        else:
                            logger.info("No existing Jira found (after redirect)")
                            return None
                logger.error(f"Jira search failed after redirect: {response.status_code} - {response.text}")
                return None
            else:
                logger.error(f"Jira search failed: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Error searching Jira: {e}")
            return None

    def create_issue(
        self,
        test_name: str,
        test_description: str,
        version: str,
        platforms: list = None,
        platform: str = None,
        error_message: str = None,
        job_url: str = None,
        failure_rate: float = 0.0,
        runs: int = 0,
        failures: int = 0,
        ai_analysis: dict = None,
        platform_stats: list = None,
        artifacts_url: str = None
    ) -> Optional[str]:
        """
        Create a new Jira issue for test failure.

        Args:
            test_name: Test ID (e.g., OCP-12345)
            test_description: Human-readable test description
            version: OCP version
            platforms: List of affected platforms (e.g., ['aws', 'azure', 'gcp'])
            platform: Single platform (deprecated - use platforms instead)
            error_message: Error message from test failure
            job_url: Link to job
            failure_rate: Failure rate percentage
            runs: Total runs
            failures: Number of failures
            ai_analysis: AI analysis dict with root_cause, failure_type, etc.
            platform_stats: Per-platform breakdown list of dicts
                with keys: platform, failed, total
            artifacts_url: Link to gcsweb test artifacts

        Returns:
            Jira issue key if created, None otherwise
        """
        if not self.enabled:
            logger.warning("Cannot create Jira: Integration not enabled")
            return None

        # Handle backwards compatibility
        if not platforms and platform:
            platforms = [platform]
        elif not platforms:
            platforms = []

        # Check for existing issue first
        existing = self.search_existing_issue(test_name, version)
        if existing:
            logger.info(f"Existing Jira found: {existing.get('key')}")
            return existing.get('key')

        # Build per-platform breakdown string
        if platform_stats:
            parts = []
            for ps in platform_stats:
                parts.append(
                    f"{ps['platform']} ({ps['failed']}/{ps['total']} failed)"
                )
            platforms_str = ', '.join(parts)
        elif platforms:
            platforms_str = ', '.join(platforms)
        else:
            platforms_str = 'multiple platforms'

        # Build summary with error context
        error_summary = self._extract_error_summary(error_message)
        if ai_analysis and ai_analysis.get('root_cause'):
            # Use AI root cause for a more descriptive title
            root_cause_short = ai_analysis['root_cause'][:80]
            summary = (
                f"{test_name}: {root_cause_short} "
                f"on {', '.join(platforms) if platforms else 'multiple platforms'} "
                f"{version}"
            )
        elif error_summary:
            summary = (
                f"{test_name}: {error_summary} "
                f"on {', '.join(platforms) if platforms else 'multiple platforms'} "
                f"{version}"
            )
        else:
            summary = (
                f"{test_name}: Test failure "
                f"on {', '.join(platforms) if platforms else 'multiple platforms'} "
                f"{version}"
            )
        # Jira summary field limit is 255 chars
        if len(summary) > 255:
            summary = summary[:252] + "..."

        # Dashboard link
        dashboard_url = os.environ.get('DASHBOARD_URL', 'https://winc-dashboard-poc-winc-dashboard-poc.apps.build10.ci.devcluster.openshift.com')

        # Truncate error message to first 2000 chars
        error_msg_short = (error_message[:2000] + "...") if error_message and len(error_message) > 2000 else (error_message or "No error message")

        # Build ADF content blocks
        adf_content = []

        # Test metadata paragraph
        metadata_texts = [
            {"type": "text", "text": f"Test: {test_name}\n"},
            {"type": "text", "text": f"Version: {version}\n"},
            {"type": "text", "text": f"Affected Platforms: {platforms_str}\n"},
            {"type": "text", "text": f"Pass Rate: {failure_rate:.1f}% ({runs - failures}/{runs} runs passed)"},
        ]
        adf_content.append({
            "type": "paragraph",
            "content": metadata_texts
        })

        # AI analysis section (if available)
        if ai_analysis and ai_analysis.get('root_cause'):
            ai_texts = [
                {"type": "text", "text": "Root Cause (AI analysis): ",
                 "marks": [{"type": "strong"}]},
                {"type": "text", "text": ai_analysis['root_cause']},
            ]
            if ai_analysis.get('failure_type'):
                ai_texts.append(
                    {"type": "text",
                     "text": f"\nFailure Type: {ai_analysis['failure_type']}"}
                )
            if ai_analysis.get('suggested_action'):
                ai_texts.append(
                    {"type": "text",
                     "text": f"\nSuggested Action: {ai_analysis['suggested_action']}"}
                )
            adf_content.append({
                "type": "paragraph",
                "content": ai_texts
            })

        # Error message paragraph
        adf_content.append({
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "Error: ",
                 "marks": [{"type": "strong"}]},
                {"type": "text", "text": error_msg_short}
            ]
        })

        # Job link paragraph (if available)
        if job_url:
            adf_content.append({
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Prow Job: "},
                    {"type": "text", "text": job_url,
                     "marks": [{"type": "link",
                                "attrs": {"href": job_url}}]}
                ]
            })

        # Artifacts link paragraph (if available)
        if artifacts_url:
            adf_content.append({
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Artifacts: "},
                    {"type": "text", "text": artifacts_url,
                     "marks": [{"type": "link",
                                "attrs": {"href": artifacts_url}}]}
                ]
            })

        # Dashboard link paragraph
        adf_content.append({
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "Dashboard: "},
                {"type": "text", "text": dashboard_url,
                 "marks": [{"type": "link",
                            "attrs": {"href": dashboard_url}}]}
            ]
        })

        description = {
            "version": 1,
            "type": "doc",
            "content": adf_content
        }

        try:
            logger.info(f"Creating Jira: {summary}")

            # Prepare issue data
            issue_data = {
                'fields': {
                    'project': {'key': self.config.project_key},
                    'summary': summary,
                    'description': description,
                    'issuetype': {'name': self.config.issue_type},
                    'priority': {'name': self.config.priority}
                }
            }

            # Add component if configured
            if self.config.component:
                issue_data['fields']['components'] = [{'name': self.config.component}]

            # Call Jira create API (v3)
            create_url = f"{self.config.url}/rest/api/3/issue"
            logger.info(f"POST {create_url}")

            response = requests.post(
                create_url,
                headers=self._get_headers(),
                json=issue_data,
                timeout=30,
                allow_redirects=False  # Handle redirects manually to preserve POST method
            )

            logger.info(f"Response status: {response.status_code}")

            if response.status_code in (200, 201):
                data = response.json()
                issue_key = data.get('key')
                logger.info(f"Created Jira: {issue_key}")
                return issue_key
            elif response.status_code in (301, 302, 303, 307, 308):
                # Handle redirect - get the redirect location and retry
                redirect_url = response.headers.get('Location')
                logger.warning(f"Got redirect to: {redirect_url}")
                if redirect_url:
                    response = requests.post(
                        redirect_url,
                        headers=self._get_headers(),
                        json=issue_data,
                        timeout=30
                    )
                    if response.status_code in (200, 201):
                        data = response.json()
                        issue_key = data.get('key')
                        logger.info(f"Created Jira (after redirect): {issue_key}")
                        return issue_key
                error_msg = f"Redirect failed: {response.status_code} - {response.text}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
            else:
                error_msg = f"Jira creation failed: {response.status_code} - {response.text}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

        except RuntimeError:
            raise
        except Exception as e:
            logger.error(f"Error creating Jira: {e}")
            raise RuntimeError(f"Error creating Jira: {e}") from e

    @staticmethod
    def _extract_error_summary(error_message: str) -> str:
        """Extract a short summary from an error message for use in titles.

        Returns the first meaningful line of the error, trimmed to 80 chars.
        """
        if not error_message:
            return ""
        for line in error_message.splitlines():
            line = line.strip()
            # Skip blank lines and common log prefix lines
            if not line:
                continue
            if line.startswith(("I0", "E0", "W0")):
                # Timestamp-prefixed log lines — skip
                continue
            # Return first substantive line, trimmed
            if len(line) > 80:
                return line[:77] + "..."
            return line
        return ""

    def get_issue_url(self, issue_key: str) -> str:
        """Get URL for a Jira issue"""
        return f"{self.config.url}/browse/{issue_key}"

    def create_report(self, summary: str, description: str) -> Optional[str]:
        """Create a Jira issue for a dashboard problem report."""
        if not self.enabled:
            logger.warning("Cannot create Jira: Integration not enabled")
            return None

        dashboard_url = os.environ.get(
            'DASHBOARD_URL',
            'https://winc-dashboard-poc-winc-dashboard-poc.apps.build10.ci.devcluster.openshift.com'
        )

        adf_description = {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": description}
                    ]
                },
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Reported from: "},
                        {"type": "text", "text": dashboard_url,
                         "marks": [{"type": "link", "attrs": {"href": dashboard_url}}]}
                    ]
                }
            ]
        }

        try:
            issue_data = {
                'fields': {
                    'project': {'key': self.config.project_key},
                    'summary': f"[Dashboard] {summary}",
                    'description': adf_description,
                    'issuetype': {'name': self.config.issue_type},
                    'priority': {'name': self.config.priority}
                }
            }

            if self.config.component:
                issue_data['fields']['components'] = [{'name': self.config.component}]

            create_url = f"{self.config.url}/rest/api/3/issue"
            logger.info(f"Creating dashboard report: [Dashboard] {summary}")

            response = requests.post(
                create_url,
                headers=self._get_headers(),
                json=issue_data,
                timeout=30,
                allow_redirects=False
            )

            if response.status_code in (200, 201):
                issue_key = response.json().get('key')
                logger.info(f"Created dashboard report: {issue_key}")
                return issue_key

            if response.status_code in (301, 302, 303, 307, 308):
                redirect_url = response.headers.get('Location')
                if redirect_url:
                    response = requests.post(
                        redirect_url,
                        headers=self._get_headers(),
                        json=issue_data,
                        timeout=30
                    )
                    if response.status_code in (200, 201):
                        issue_key = response.json().get('key')
                        logger.info(f"Created dashboard report (after redirect): {issue_key}")
                        return issue_key

            logger.error(f"Dashboard report creation failed: {response.status_code} - {response.text}")
            return None

        except Exception as e:
            logger.error(f"Error creating dashboard report: {e}")
            return None


# Global Jira integration instance
_jira_instance: Optional[JiraIntegration] = None


def get_jira_integration() -> Optional[JiraIntegration]:
    """Get or create Jira integration instance"""
    global _jira_instance

    if _jira_instance is None:
        # Load configuration from environment
        jira_url = os.environ.get('JIRA_URL', 'https://issues.redhat.com')
        jira_project = os.environ.get('JIRA_PROJECT', 'WINC')
        jira_component = os.environ.get('JIRA_COMPONENT')

        config = JiraConfig(
            url=jira_url,
            project_key=jira_project,
            component=jira_component
        )

        _jira_instance = JiraIntegration(config)

    return _jira_instance if _jira_instance.enabled else None
