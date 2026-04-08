"""
Jira Integration for CI Failure Tracker

Allows creating Jira issues for failing tests with duplicate detection.
"""

import os
import logging
import requests
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

    def search_existing_issue(self, test_name: str, version: str, platform: str) -> Optional[Dict]:
        """
        Search for existing Jira issue for this test failure.

        Args:
            test_name: Test ID (e.g., OCP-12345)
            version: OCP version (e.g., 4.22)
            platform: Platform (e.g., aws)

        Returns:
            Jira issue dict if found, None otherwise
        """
        if not self.enabled:
            return None

        # JQL query to find issues with this test name
        jql = f'project = {self.config.project_key} AND summary ~ "{test_name}" AND resolution = Unresolved'

        try:
            logger.info(f"Searching for existing Jira: {jql}")

            # Call Jira search API (v3) - use GET with query params
            search_url = f"{self.config.url}/rest/api/3/search"
            response = requests.get(
                search_url,
                headers=self._get_headers(),
                params={'jql': jql, 'maxResults': 1, 'fields': 'key,summary'}
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
        platform: str,
        error_message: str,
        job_url: str,
        failure_rate: float,
        runs: int,
        failures: int
    ) -> Optional[str]:
        """
        Create a new Jira issue for test failure.

        Args:
            test_name: Test ID (e.g., OCP-12345)
            test_description: Human-readable test description
            version: OCP version
            platform: Platform
            error_message: Error message from test failure
            job_url: Link to job
            failure_rate: Failure rate percentage
            runs: Total runs
            failures: Number of failures

        Returns:
            Jira issue key if created, None otherwise
        """
        if not self.enabled:
            logger.warning("Cannot create Jira: Integration not enabled")
            return None

        # Check for existing issue first
        existing = self.search_existing_issue(test_name, version, platform)
        if existing:
            logger.info(f"Existing Jira found: {existing.get('key')}")
            return existing.get('key')

        # Create issue summary and description
        summary = f"{test_name}: Test failure on {platform} {version}"

        # Build links section
        links_content = []

        # Add job URL link if available
        if job_url:
            links_content.append({
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Failed Job: ", "marks": [{"type": "strong"}]},
                    {"type": "text", "text": job_url, "marks": [{"type": "link", "attrs": {"href": job_url}}]}
                ]
            })

        # Add dashboard link
        dashboard_url = os.environ.get('DASHBOARD_URL', 'https://winc-dashboard-poc-winc-dashboard-poc.apps.build10.ci.devcluster.openshift.com')
        links_content.append({
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "CI Dashboard: ", "marks": [{"type": "strong"}]},
                {"type": "text", "text": dashboard_url, "marks": [{"type": "link", "attrs": {"href": dashboard_url}}]}
            ]
        })

        # Atlassian Document Format (ADF) for description
        description = {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 2},
                    "content": [{"type": "text", "text": "Test Failure Report"}]
                },
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Test: ", "marks": [{"type": "strong"}]},
                        {"type": "text", "text": test_name}
                    ]
                },
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Description: ", "marks": [{"type": "strong"}]},
                        {"type": "text", "text": test_description or "N/A"}
                    ]
                },
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Version: ", "marks": [{"type": "strong"}]},
                        {"type": "text", "text": version}
                    ]
                },
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Platform: ", "marks": [{"type": "strong"}]},
                        {"type": "text", "text": platform}
                    ]
                },
                {
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": [{"type": "text", "text": "Failure Statistics"}]
                },
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [{
                                "type": "paragraph",
                                "content": [{"type": "text", "text": f"Failure Rate: {failure_rate:.1f}%"}]
                            }]
                        },
                        {
                            "type": "listItem",
                            "content": [{
                                "type": "paragraph",
                                "content": [{"type": "text", "text": f"Total Runs: {runs}"}]
                            }]
                        },
                        {
                            "type": "listItem",
                            "content": [{
                                "type": "paragraph",
                                "content": [{"type": "text", "text": f"Failures: {failures}"}]
                            }]
                        }
                    ]
                },
                {
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": [{"type": "text", "text": "Error Message"}]
                },
                {
                    "type": "codeBlock",
                    "content": [{"type": "text", "text": error_message if error_message else "No error message available"}]
                },
                {
                    "type": "heading",
                    "attrs": {"level": 3},
                    "content": [{"type": "text", "text": "Links"}]
                },
                *links_content,
                {
                    "type": "rule"
                },
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "This issue was automatically created by CI Failure Tracker", "marks": [{"type": "em"}]}
                    ]
                }
            ]
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
            response = requests.post(
                create_url,
                headers=self._get_headers(),
                json=issue_data
            )

            if response.status_code in (200, 201):
                data = response.json()
                issue_key = data.get('key')
                logger.info(f"Created Jira: {issue_key}")
                return issue_key
            else:
                logger.error(f"Jira creation failed: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Error creating Jira: {e}")
            return None

    def get_issue_url(self, issue_key: str) -> str:
        """Get URL for a Jira issue"""
        return f"{self.config.url}/browse/{issue_key}"


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
