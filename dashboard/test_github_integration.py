"""Tests for GitHub integration.

Validates issue creation, error handling, and configuration.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from src.integrations.github_integration import (
    GitHubIntegration,
    GitHubConfig,
    get_github_integration,
)


@pytest.fixture
def github_config():
    return GitHubConfig(
        repo="owner/repo",
        token="test-token",
        api_url="https://api.github.com",
    )


@pytest.fixture
def github(github_config):
    return GitHubIntegration(github_config)


class TestCreateReport:
    """Tests for GitHub issue creation via create_report."""

    @patch('src.integrations.github_integration.requests.post')
    def test_creates_issue_successfully(self, mock_post, github):
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'number': 42, 'html_url': 'https://github.com/owner/repo/issues/42'},
        )

        result = github.create_report(summary="Test bug", description="Something broke")

        assert result is not None
        assert result['number'] == 42
        assert result['html_url'] == 'https://github.com/owner/repo/issues/42'

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs['json'] if 'json' in call_kwargs.kwargs else call_kwargs[1]['json']
        assert body['title'] == '[Dashboard] Test bug'
        assert 'Something broke' in body['body']
        assert 'bug' in body['labels']

    @patch('src.integrations.github_integration.requests.post')
    def test_returns_none_on_auth_failure(self, mock_post, github):
        mock_post.return_value = MagicMock(
            status_code=401,
            text='Bad credentials',
        )

        result = github.create_report(summary="Test", description="Desc")
        assert result is None

    @patch('src.integrations.github_integration.requests.post')
    def test_returns_none_on_network_error(self, mock_post, github):
        mock_post.side_effect = Exception("Connection refused")

        result = github.create_report(summary="Test", description="Desc")
        assert result is None

    @patch('src.integrations.github_integration.requests.post')
    def test_returns_none_on_403(self, mock_post, github):
        mock_post.return_value = MagicMock(
            status_code=403,
            text='Forbidden',
        )

        result = github.create_report(summary="Test", description="Desc")
        assert result is None

    @patch('src.integrations.github_integration.requests.post')
    def test_includes_github_username_mention(self, mock_post, github):
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'number': 10, 'html_url': 'https://github.com/owner/repo/issues/10'},
        )

        result = github.create_report(
            summary="Bug", description="Details",
            reporter_github="dholler"
        )

        assert result is not None
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs['json'] if 'json' in call_kwargs.kwargs else call_kwargs[1]['json']
        assert 'Reported by: @dholler' in body['body']
        assert 'Reported via CI Dashboard' in body['body']

    @patch('src.integrations.github_integration.requests.post')
    def test_includes_reporter_name_when_no_github(self, mock_post, github):
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'number': 11, 'html_url': 'https://github.com/owner/repo/issues/11'},
        )

        result = github.create_report(
            summary="Bug", description="Details",
            reporter_name="Dominik Holler"
        )

        assert result is not None
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs['json'] if 'json' in call_kwargs.kwargs else call_kwargs[1]['json']
        assert 'Reported by: Dominik Holler' in body['body']
        assert 'Reported via CI Dashboard' in body['body']

    @patch('src.integrations.github_integration.requests.post')
    def test_github_username_takes_precedence_over_name(self, mock_post, github):
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'number': 12, 'html_url': 'https://github.com/owner/repo/issues/12'},
        )

        result = github.create_report(
            summary="Bug", description="Details",
            reporter_name="Dominik Holler",
            reporter_github="dholler"
        )

        assert result is not None
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs['json'] if 'json' in call_kwargs.kwargs else call_kwargs[1]['json']
        assert 'Reported by: @dholler' in body['body']
        assert 'Dominik Holler' not in body['body']

    @patch('src.integrations.github_integration.requests.post')
    def test_strips_at_sign_from_github_username(self, mock_post, github):
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'number': 13, 'html_url': 'https://github.com/owner/repo/issues/13'},
        )

        result = github.create_report(
            summary="Bug", description="Details",
            reporter_github="@dholler"
        )

        assert result is not None
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs['json'] if 'json' in call_kwargs.kwargs else call_kwargs[1]['json']
        assert 'Reported by: @dholler' in body['body']
        assert '@@' not in body['body']

    @patch('src.integrations.github_integration.requests.post')
    def test_no_reporter_fields_preserves_original_footer(self, mock_post, github):
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'number': 14, 'html_url': 'https://github.com/owner/repo/issues/14'},
        )

        result = github.create_report(summary="Bug", description="Details")

        assert result is not None
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs['json'] if 'json' in call_kwargs.kwargs else call_kwargs[1]['json']
        assert 'Reported by:' not in body['body']
        assert 'Reported via CI Dashboard' in body['body']


class TestGetGithubIntegration:
    """Tests for get_github_integration factory function."""

    def setup_method(self):
        import src.integrations.github_integration as mod
        mod._github_instance = None

    @patch.dict(os.environ, {'GITHUB_TOKEN': 'tok', 'GITHUB_REPO': 'o/r'})
    def test_returns_instance_when_configured(self):
        instance = get_github_integration()
        assert instance is not None
        assert instance.config.repo == 'o/r'

    @patch.dict(os.environ, {}, clear=True)
    def test_returns_none_when_not_configured(self):
        instance = get_github_integration()
        assert instance is None

    @patch.dict(os.environ, {'GITHUB_TOKEN': 'tok'}, clear=True)
    def test_returns_none_when_repo_missing(self):
        instance = get_github_integration()
        assert instance is None

    def teardown_method(self):
        import src.integrations.github_integration as mod
        mod._github_instance = None
