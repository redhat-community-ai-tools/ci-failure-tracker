"""
Jira Client using REST API

Direct Jira REST API integration for creating and searching tickets.

Prerequisites:
- Jira API token (get from: https://id.atlassian.com/manage-profile/security/api-tokens)
- Set environment variable: JIRA_API_TOKEN
"""

import os
import requests
import urllib3
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from rich.console import Console

# Disable SSL warnings for internal Red Hat services
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

console = Console()


@dataclass
class JiraTicket:
    """Represents a created Jira ticket"""
    key: str
    url: str
    summary: str


class JiraClient:
    """
    Jira client using REST API

    Directly calls Jira REST API for creating and searching tickets.
    No MCP or Claude Code required.
    """

    def __init__(self, jira_url: str, project: str, username: str, api_token: str):
        """
        Initialize Jira client

        Args:
            jira_url: Jira server URL (e.g., "https://issues.redhat.com")
            project: Jira project key (e.g., "WINC")
            username: Jira username/email
            api_token: Jira API token
        """
        self.jira_url = jira_url.rstrip('/')
        self.project = project
        self.username = username
        self.api_token = api_token
        self.auth = (username, api_token)
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        console.print(f"[blue]Jira client initialized (REST API mode)[/blue]")
        console.print(f"[dim]Project: {project}[/dim]")
        console.print(f"[dim]Server: {jira_url}[/dim]")

    def search_issues(self, jql: str, max_results: int = 50) -> List[Dict[str, Any]]:
        """
        Search for Jira issues using JQL

        Args:
            jql: JQL query string
            max_results: Maximum number of results to return

        Returns:
            List of issue dictionaries
        """
        try:
            url = f"{self.jira_url}/rest/api/2/search"
            params = {
                "jql": jql,
                "maxResults": max_results,
                "fields": "key,summary,status,labels,description"
            }

            response = requests.get(
                url,
                auth=self.auth,
                headers=self.headers,
                params=params,
                timeout=30,
                verify=False  # Disable SSL verification for internal Red Hat Jira
            )

            if response.status_code == 200:
                data = response.json()
                return data.get('issues', [])
            else:
                console.print(f"[red]Jira search failed: {response.status_code}[/red]")
                console.print(f"[dim]{response.text}[/dim]")
                return []

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Error searching Jira: {e}[/red]")
            return []

    def create_issue(
        self,
        project: str,
        issue_type: str,
        summary: str,
        description: str,
        parent: Optional[str] = None,
        labels: Optional[List[str]] = None,
        priority: str = "Normal",
        component: Optional[str] = None,
        dry_run: bool = False
    ) -> Optional[JiraTicket]:
        """
        Create a Jira issue using REST API

        Args:
            project: Jira project key
            issue_type: Issue type (e.g., "Sub-task", "Bug", "Story")
            summary: Issue summary/title
            description: Issue description (Jira wiki markup)
            parent: Parent issue key (required for Sub-task)
            labels: List of labels to add
            priority: Issue priority
            component: Component name
            dry_run: If True, don't actually create ticket

        Returns:
            JiraTicket if successful, None otherwise
        """
        if dry_run:
            console.print(f"[yellow][DRY RUN] Would create: {summary}[/yellow]")
            return JiraTicket(
                key="DRY-RUN-123",
                url=f"{self.jira_url}/browse/DRY-RUN-123",
                summary=summary
            )

        try:
            # Prepare fields
            fields = {
                'project': {'key': project},
                'issuetype': {'name': issue_type},
                'summary': summary,
                'description': description,
                'priority': {'name': priority}
            }

            # Add parent if specified (required for Sub-task)
            if parent:
                fields['parent'] = {'key': parent}

            # Add labels
            if labels:
                fields['labels'] = labels

            # Add component
            if component:
                fields['components'] = [{'name': component}]

            # Create issue via REST API
            url = f"{self.jira_url}/rest/api/2/issue"
            payload = {"fields": fields}

            response = requests.post(
                url,
                auth=self.auth,
                headers=self.headers,
                json=payload,
                timeout=30,
                verify=False  # Disable SSL verification for internal Red Hat Jira
            )

            if response.status_code == 201:
                data = response.json()
                ticket_key = data['key']
                ticket_url = f"{self.jira_url}/browse/{ticket_key}"

                console.print(f"[green]✓ Created ticket {ticket_key}[/green]")

                return JiraTicket(
                    key=ticket_key,
                    url=ticket_url,
                    summary=summary
                )
            else:
                console.print(f"[red]Failed to create ticket: {response.status_code}[/red]")
                console.print(f"[dim]{response.text}[/dim]")
                return None

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Error creating ticket: {e}[/red]")
            return None

    def check_for_duplicate(
        self,
        test_name: str,
        error_signature: str
    ) -> Optional[str]:
        """
        Check if a ticket already exists for this failure

        Args:
            test_name: Test identifier (e.g., "OCP-39451")
            error_signature: Error signature hash

        Returns:
            Existing ticket key if found, None otherwise
        """
        # Search for tickets with matching test name and not closed
        jql = (
            f'project = {self.project} AND '
            f'labels = "ci-failure" AND '
            f'summary ~ "{test_name}" AND '
            f'status NOT IN (Closed, Resolved)'
        )

        issues = self.search_issues(jql, max_results=10)

        # TODO: More sophisticated matching using error_signature
        # For now, just check if test name exists
        if issues:
            return issues[0]['key']

        return None


def get_jira_client(jira_url: str, project: str, username: Optional[str] = None, api_token: Optional[str] = None) -> JiraClient:
    """
    Factory function to get a Jira client

    Args:
        jira_url: Jira server URL
        project: Jira project key
        username: Jira username (defaults to JIRA_USER env var)
        api_token: Jira API token (defaults to JIRA_API_TOKEN env var)

    Returns:
        Configured JiraClient instance

    Raises:
        ValueError: If credentials are not provided
    """
    # Get credentials from environment if not provided
    if username is None:
        username = os.environ.get('JIRA_USER')
        if not username:
            raise ValueError("JIRA_USER environment variable not set")

    if api_token is None:
        api_token = os.environ.get('JIRA_API_TOKEN')
        if not api_token:
            raise ValueError("JIRA_API_TOKEN environment variable not set")

    return JiraClient(jira_url, project, username, api_token)
