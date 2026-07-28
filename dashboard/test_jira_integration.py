"""Tests for Jira integration.

Validates issue creation error propagation and configuration.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from src.integrations.jira_integration import (
    JiraIntegration,
    JiraConfig,
    get_jira_integration,
)


@pytest.fixture
def jira_config():
    return JiraConfig(
        url="https://issues.example.com",
        project_key="TEST",
    )


@pytest.fixture
def jira(jira_config):
    with patch.dict(os.environ, {'JIRA_API_TOKEN': 'test-token'}):
        return JiraIntegration(jira_config)


class TestCreateIssueErrorPropagation:
    """Tests that create_issue raises descriptive errors instead of
    returning None, so callers can surface the reason to users."""

    @patch('src.integrations.jira_integration.requests.post')
    def test_raises_on_auth_failure(self, mock_post, jira):
        """401 from Jira API should raise with status and response text."""
        # search returns no existing issue
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            MagicMock(status_code=401, text='Unauthorized'),
        ]

        with pytest.raises(RuntimeError, match="401"):
            jira.create_issue(
                test_name="OCP-1234",
                test_description="desc",
                version="4.22",
            )

    @patch('src.integrations.jira_integration.requests.post')
    def test_raises_on_forbidden(self, mock_post, jira):
        """403 from Jira API should raise with status and response text."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            MagicMock(status_code=403, text='Forbidden'),
        ]

        with pytest.raises(RuntimeError, match="403"):
            jira.create_issue(
                test_name="OCP-1234",
                test_description="desc",
                version="4.22",
            )

    @patch('src.integrations.jira_integration.requests.post')
    def test_raises_on_not_found(self, mock_post, jira):
        """404 from Jira API should raise with status and response text."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            MagicMock(status_code=404, text='Project not found'),
        ]

        with pytest.raises(RuntimeError, match="404"):
            jira.create_issue(
                test_name="OCP-1234",
                test_description="desc",
                version="4.22",
            )

    @patch('src.integrations.jira_integration.requests.post')
    def test_raises_on_network_error(self, mock_post, jira):
        """Network errors should raise RuntimeError with details."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            ConnectionError("Connection refused"),
        ]

        with pytest.raises(RuntimeError, match="Connection refused"):
            jira.create_issue(
                test_name="OCP-1234",
                test_description="desc",
                version="4.22",
            )

    @patch('src.integrations.jira_integration.requests.post')
    def test_error_message_includes_response_text(self, mock_post, jira):
        """Error message should include the Jira API response body."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            MagicMock(status_code=400, text='Field "priority" is required'),
        ]

        with pytest.raises(RuntimeError, match="priority"):
            jira.create_issue(
                test_name="OCP-1234",
                test_description="desc",
                version="4.22",
            )

    @patch('src.integrations.jira_integration.requests.post')
    def test_success_returns_issue_key(self, mock_post, jira):
        """Successful creation should still return the issue key."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            MagicMock(
                status_code=201,
                json=lambda: {'key': 'TEST-42'},
            ),
        ]

        result = jira.create_issue(
            test_name="OCP-1234",
            test_description="desc",
            version="4.22",
        )
        assert result == "TEST-42"

    @patch('src.integrations.jira_integration.requests.post')
    def test_raises_on_redirect_failure(self, mock_post, jira):
        """Failed redirect should raise with details."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            MagicMock(
                status_code=302,
                headers={'Location': 'https://new.example.com/api'},
            ),
            MagicMock(status_code=500, text='Internal Server Error'),
        ]

        with pytest.raises(RuntimeError, match="Redirect failed"):
            jira.create_issue(
                test_name="OCP-1234",
                test_description="desc",
                version="4.22",
            )


class TestSearchExistingIssueDedup:
    """Tests that search_existing_issue finds duplicates when the test ID
    appears in the summary, description, or neither."""

    @patch('src.integrations.jira_integration.requests.post')
    def test_finds_issue_by_summary(self, mock_post, jira):
        """Regression: test ID in summary should still match."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                'issues': [{
                    'key': 'WINC-2010',
                    'fields': {
                        'summary': 'OCP-33612: Test failure on aws 4.22',
                    },
                }],
            },
        )

        result = jira.search_existing_issue("OCP-33612", "4.22")
        assert result is not None
        assert result['key'] == 'WINC-2010'

    @patch('src.integrations.jira_integration.requests.post')
    def test_finds_issue_by_description(self, mock_post, jira):
        """Bug fix: test ID only in description should also match."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                'issues': [{
                    'key': 'WINC-2009',
                    'fields': {
                        'summary': 'Failed to check kubelet version',
                    },
                }],
            },
        )

        result = jira.search_existing_issue("OCP-33612", "4.22")
        assert result is not None
        assert result['key'] == 'WINC-2009'

    @patch('src.integrations.jira_integration.requests.post')
    def test_returns_none_when_no_match(self, mock_post, jira):
        """No match in summary or description should return None."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'issues': []},
        )

        result = jira.search_existing_issue("OCP-99999", "4.22")
        assert result is None

    @patch('src.integrations.jira_integration.requests.post')
    def test_jql_searches_summary_and_description(self, mock_post, jira):
        """JQL must contain both summary~ and description~ with OR."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'issues': []},
        )

        jira.search_existing_issue("OCP-33612", "4.22")

        call_kwargs = mock_post.call_args
        sent_jql = call_kwargs.kwargs.get('json', {}).get('jql', '')
        assert 'summary ~ "OCP-33612"' in sent_jql
        assert 'description ~ "OCP-33612"' in sent_jql
        assert ' OR ' in sent_jql
