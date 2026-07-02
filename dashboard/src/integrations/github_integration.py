"""
GitHub Integration for CI Dashboard

Creates GitHub issues for dashboard problem reports.
"""

import os
import logging
import requests
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class GitHubConfig:
    """GitHub configuration"""
    repo: str  # owner/repo format
    token: str
    api_url: str = "https://api.github.com"


class GitHubIntegration:
    """
    GitHub integration for filing dashboard problem reports as GitHub issues.
    """

    def __init__(self, config: GitHubConfig):
        self.config = config

    def _get_headers(self):
        return {
            'Authorization': f'token {self.config.token}',
            'Accept': 'application/vnd.github.v3+json',
            'Content-Type': 'application/json'
        }

    def create_report(self, summary: str, description: str) -> Optional[dict]:
        """Create a GitHub issue for a dashboard problem report.

        Returns dict with 'number' and 'html_url' on success, None on failure.
        """
        try:
            title = f"[Dashboard] {summary}"
            body = f"{description}\n\n---\n*Reported via CI Dashboard*"

            issue_data = {
                'title': title,
                'body': body,
                'labels': ['bug']
            }

            url = f"{self.config.api_url}/repos/{self.config.repo}/issues"
            logger.info(f"Creating GitHub issue: {title}")

            response = requests.post(
                url,
                headers=self._get_headers(),
                json=issue_data,
                timeout=30
            )

            if response.status_code in (200, 201):
                data = response.json()
                issue_number = data.get('number')
                issue_url = data.get('html_url')
                logger.info(f"Created GitHub issue: #{issue_number}")
                return {'number': issue_number, 'html_url': issue_url}

            logger.error(f"GitHub issue creation failed: {response.status_code} - {response.text}")
            return None

        except Exception as e:
            logger.error(f"Error creating GitHub issue: {e}")
            return None


# Global GitHub integration instance
_github_instance: Optional[GitHubIntegration] = None


def get_github_integration() -> Optional[GitHubIntegration]:
    """Get or create GitHub integration instance"""
    global _github_instance

    if _github_instance is None:
        token = os.environ.get('GITHUB_TOKEN')
        repo = os.environ.get('GITHUB_REPO')

        if not token or not repo:
            logger.debug("GitHub integration not configured (missing GITHUB_TOKEN or GITHUB_REPO)")
            return None

        api_url = os.environ.get('GITHUB_API_URL', 'https://api.github.com')

        config = GitHubConfig(
            repo=repo,
            token=token,
            api_url=api_url
        )

        _github_instance = GitHubIntegration(config)
        logger.info(f"GitHub integration initialized for {repo}")

    return _github_instance
