"""Tests for Report a Problem endpoint routed through Jira.

Validates that the /api/github/report-problem endpoint creates Jira issues
using the existing Jira integration instead of GitHub.
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
        url="https://issues.redhat.com",
        project_key="WINC",
    )


@pytest.fixture
def jira(jira_config):
    with patch.dict(os.environ, {'JIRA_API_TOKEN': 'test-token'}):
        return JiraIntegration(jira_config)


class TestCreateReport:
    """Tests for Jira issue creation via create_report."""

    @patch('src.integrations.jira_integration.requests.post')
    def test_creates_task_by_default(self, mock_post, jira):
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'key': 'WINC-100'},
        )

        result = jira.create_report(summary="UI is broken", description="Page fails to load")

        assert result == 'WINC-100'

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get('json') or call_kwargs[1]['json']
        assert body['fields']['summary'] == '[Dashboard] UI is broken'
        assert body['fields']['issuetype']['name'] == 'Task'

    @patch('src.integrations.jira_integration.requests.post')
    def test_summary_has_dashboard_prefix(self, mock_post, jira):
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'key': 'WINC-101'},
        )

        jira.create_report(summary="Chart not rendering", description="Details")

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get('json') or call_kwargs[1]['json']
        assert body['fields']['summary'].startswith('[Dashboard] ')

    @patch('src.integrations.jira_integration.requests.post')
    def test_returns_none_on_auth_failure(self, mock_post, jira):
        mock_post.return_value = MagicMock(
            status_code=401,
            text='Unauthorized',
        )

        result = jira.create_report(summary="Test", description="Desc")
        assert result is None

    @patch('src.integrations.jira_integration.requests.post')
    def test_returns_none_on_network_error(self, mock_post, jira):
        mock_post.side_effect = Exception("Connection refused")

        result = jira.create_report(summary="Test", description="Desc")
        assert result is None

    @patch('src.integrations.jira_integration.requests.post')
    def test_issue_type_override(self, mock_post, jira):
        """Verify issue_type parameter can be overridden from the default Task."""
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'key': 'WINC-102'},
        )

        jira.create_report(summary="Test", description="Desc", issue_type="Bug")

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get('json') or call_kwargs[1]['json']
        assert body['fields']['issuetype']['name'] == 'Bug'

    def test_returns_none_when_disabled(self, jira_config):
        """Verify create_report returns None when credentials are missing."""
        with patch.dict(os.environ, {}, clear=True):
            jira = JiraIntegration(jira_config)
            result = jira.create_report(summary="Test", description="Desc")
            assert result is None


class TestReportProblemEndpoint:
    """Tests for the /api/github/report-problem Flask endpoint."""

    @pytest.fixture
    def app(self, tmp_path):
        """Create a test Flask app."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
        from src.web.server import create_app

        db_path = str(tmp_path / 'test.db')
        app = create_app(db_path=db_path, config={'TESTING': True})
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_returns_disabled_when_jira_not_configured(self, client):
        """Endpoint returns disabled status when Jira is not configured."""
        with patch('integrations.get_jira_integration', return_value=None):
            resp = client.post('/api/github/report-problem',
                               json={'summary': 'Test', 'description': 'Desc'})
            data = resp.get_json()
            assert data['status'] == 'disabled'
            assert 'JIRA_API_TOKEN' in data['message']

    def test_returns_error_when_summary_missing(self, client):
        """Endpoint validates required fields."""
        mock_jira = MagicMock()
        with patch('integrations.get_jira_integration', return_value=mock_jira):
            resp = client.post('/api/github/report-problem',
                               json={'summary': '', 'description': 'Desc'})
            assert resp.status_code == 400

    def test_returns_error_when_body_missing(self, client):
        """Endpoint returns 400 when request body is missing."""
        mock_jira = MagicMock()
        with patch('integrations.get_jira_integration', return_value=mock_jira):
            resp = client.post('/api/github/report-problem',
                               content_type='application/json')
            assert resp.status_code == 400

    def test_returns_created_on_success(self, client):
        """Endpoint returns issue key and URL on successful creation."""
        mock_jira = MagicMock()
        mock_jira.create_report.return_value = 'WINC-200'
        mock_jira.get_issue_url.return_value = 'https://issues.redhat.com/browse/WINC-200'

        with patch('integrations.get_jira_integration', return_value=mock_jira):
            resp = client.post('/api/github/report-problem',
                               json={'summary': 'Bug report', 'description': 'Details'})
            data = resp.get_json()
            assert data['status'] == 'created'
            assert data['issue_key'] == 'WINC-200'
            assert 'WINC-200' in data['issue_url']

    def test_returns_500_on_creation_failure(self, client):
        """Endpoint returns 500 when Jira issue creation fails."""
        mock_jira = MagicMock()
        mock_jira.create_report.return_value = None

        with patch('integrations.get_jira_integration', return_value=mock_jira):
            resp = client.post('/api/github/report-problem',
                               json={'summary': 'Bug', 'description': 'Details'})
            assert resp.status_code == 500
