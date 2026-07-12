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

    def _get_headers(self, token=None):
        return {
            'Authorization': f'token {token or self.config.token}',
            'Accept': 'application/vnd.github.v3+json',
            'Content-Type': 'application/json'
        }

    def create_report(self, summary: str, description: str,
                      reporter_name: str = '', reporter_github: str = '',
                      user_token: str = None) -> Optional[dict]:
        """Create a GitHub issue for a dashboard problem report.

        Args:
            summary: Brief description of the problem.
            description: Detailed description with steps to reproduce.
            reporter_name: Optional name or email of the reporter.
            reporter_github: Optional GitHub username of the reporter.
            user_token: Optional per-user OAuth token. When provided, the
                issue is created under the user's identity instead of the
                server PAT.

        Returns dict with 'number' and 'html_url' on success, None on failure.
        """
        try:
            title = f"[Dashboard] {summary}"

            # Build footer with reporter identity
            footer_parts = []
            if reporter_github:
                # Strip leading @ if user included it
                username = reporter_github.lstrip('@')
                footer_parts.append(f"Reported by: @{username}")
            elif reporter_name:
                footer_parts.append(f"Reported by: {reporter_name}")
            footer_parts.append("*Reported via CI Dashboard*")

            body = f"{description}\n\n---\n" + "\n".join(footer_parts)

            issue_data = {
                'title': title,
                'body': body,
                'labels': ['bug']
            }

            url = f"{self.config.api_url}/repos/{self.config.repo}/issues"
            logger.info(f"Creating GitHub issue: {title}")

            response = requests.post(
                url,
                headers=self._get_headers(token=user_token),
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
