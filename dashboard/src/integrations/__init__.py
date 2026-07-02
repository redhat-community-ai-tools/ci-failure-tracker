"""Integration modules for external services"""

from .jira_integration import JiraIntegration, JiraConfig, get_jira_integration
from .github_integration import GitHubIntegration, GitHubConfig, get_github_integration

__all__ = [
    'JiraIntegration', 'JiraConfig', 'get_jira_integration',
    'GitHubIntegration', 'GitHubConfig', 'get_github_integration',
]
