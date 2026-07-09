"""Tests for GitHub OAuth flow.

Validates OAuth login redirect, callback token exchange, status endpoint,
logout, fallback to PAT when OAuth is not configured, and security
(tokens never exposed in API responses).
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from src.web.server import create_app
from src.integrations.github_integration import GitHubIntegration, GitHubConfig


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary database for testing."""
    from src.storage.database import DashboardDatabase
    path = str(tmp_path / 'test.db')
    db = DashboardDatabase(path)
    db.close()
    return path


@pytest.fixture
def oauth_env():
    """Environment variables for OAuth-configured deployment."""
    return {
        'GITHUB_OAUTH_CLIENT_ID': 'test-client-id',
        'GITHUB_OAUTH_CLIENT_SECRET': 'test-client-secret',
        'FLASK_SECRET_KEY': 'test-secret-key',
        'GITHUB_TOKEN': 'server-pat-token',
        'GITHUB_REPO': 'owner/repo',
    }


@pytest.fixture
def app_with_oauth(db_path, oauth_env):
    """Flask app with OAuth configured."""
    with patch.dict(os.environ, oauth_env):
        app = create_app(db_path)
        app.config['TESTING'] = True
        yield app


@pytest.fixture
def client_with_oauth(app_with_oauth):
    """Test client with OAuth configured."""
    return app_with_oauth.test_client()


@pytest.fixture
def app_without_oauth(db_path):
    """Flask app without OAuth (PAT-only fallback)."""
    env = {
        'GITHUB_TOKEN': 'server-pat-token',
        'GITHUB_REPO': 'owner/repo',
    }
    with patch.dict(os.environ, env, clear=True):
        app = create_app(db_path)
        app.config['TESTING'] = True
        yield app


@pytest.fixture
def client_without_oauth(app_without_oauth):
    """Test client without OAuth."""
    return app_without_oauth.test_client()


class TestOAuthLoginRedirect:
    """Tests for /auth/github/login redirect."""

    def test_redirects_to_github_with_correct_params(self, client_with_oauth):
        response = client_with_oauth.get('/auth/github/login')
        assert response.status_code == 302
        location = response.headers['Location']
        assert 'https://github.com/login/oauth/authorize' in location
        assert 'client_id=test-client-id' in location
        assert 'scope=public_repo' in location
        assert 'state=' in location

    def test_returns_error_when_oauth_not_configured(self, client_without_oauth):
        response = client_without_oauth.get('/auth/github/login')
        assert response.status_code == 400
        data = response.get_json()
        assert 'not configured' in data['error']


class TestOAuthCallback:
    """Tests for /auth/github/callback token exchange."""

    @patch('src.web.server.http_requests.get')
    @patch('src.web.server.http_requests.post')
    def test_exchanges_code_for_token_and_stores_in_session(
        self, mock_post, mock_get, client_with_oauth
    ):
        # Mock token exchange
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'access_token': 'user-oauth-token'},
        )
        # Mock user info fetch
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {'login': 'testuser'},
        )

        # First, visit login to set the state in session
        login_resp = client_with_oauth.get('/auth/github/login')
        location = login_resp.headers['Location']
        # Extract the state parameter
        import re
        state_match = re.search(r'state=([a-f0-9]+)', location)
        state = state_match.group(1)

        # Now call the callback with the code and state
        response = client_with_oauth.get(
            f'/auth/github/callback?code=test-code&state={state}'
        )

        # Should redirect to dashboard
        assert response.status_code == 302
        assert response.headers['Location'] == '/'

        # Verify token exchange was called correctly
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get('json') or call_kwargs[1].get('json')
        assert body['client_id'] == 'test-client-id'
        assert body['client_secret'] == 'test-client-secret'
        assert body['code'] == 'test-code'

        # Verify the session has the token by checking status
        status_resp = client_with_oauth.get('/auth/github/status')
        status_data = status_resp.get_json()
        assert status_data['authenticated'] is True
        assert status_data['username'] == 'testuser'

    def test_rejects_missing_code(self, client_with_oauth):
        response = client_with_oauth.get('/auth/github/callback')
        assert response.status_code == 400

    def test_rejects_invalid_state(self, client_with_oauth):
        # Set up a valid state first
        client_with_oauth.get('/auth/github/login')

        # Call with wrong state
        response = client_with_oauth.get(
            '/auth/github/callback?code=test-code&state=wrong-state'
        )
        assert response.status_code == 400
        data = response.get_json()
        assert 'state' in data['error'].lower()

    def test_returns_error_when_oauth_not_configured(self, client_without_oauth):
        response = client_without_oauth.get(
            '/auth/github/callback?code=test-code&state=test-state'
        )
        assert response.status_code == 400

    @patch('src.web.server.http_requests.post')
    def test_handles_token_exchange_failure(self, mock_post, client_with_oauth):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'error': 'bad_verification_code', 'error_description': 'Bad code'},
        )

        login_resp = client_with_oauth.get('/auth/github/login')
        import re
        state = re.search(r'state=([a-f0-9]+)', login_resp.headers['Location']).group(1)

        response = client_with_oauth.get(
            f'/auth/github/callback?code=bad-code&state={state}'
        )
        assert response.status_code == 400


class TestAuthStatus:
    """Tests for /auth/github/status endpoint."""

    def test_unauthenticated_with_oauth_configured(self, client_with_oauth):
        response = client_with_oauth.get('/auth/github/status')
        data = response.get_json()
        assert data['authenticated'] is False
        assert data['username'] == ''
        assert data['oauth_configured'] is True

    def test_unauthenticated_without_oauth(self, client_without_oauth):
        response = client_without_oauth.get('/auth/github/status')
        data = response.get_json()
        assert data['authenticated'] is False
        assert data['oauth_configured'] is False

    def test_token_not_exposed_in_status_response(self, client_with_oauth):
        """Security: access token must never appear in API responses."""
        # Simulate an authenticated session
        with client_with_oauth.session_transaction() as sess:
            sess['github_access_token'] = 'secret-token-value'
            sess['github_username'] = 'testuser'

        response = client_with_oauth.get('/auth/github/status')
        data = response.get_json()

        assert 'secret-token-value' not in str(data)
        assert 'access_token' not in data
        assert data['authenticated'] is True
        assert data['username'] == 'testuser'


class TestAuthLogout:
    """Tests for /auth/github/logout endpoint."""

    def test_clears_session_on_logout(self, client_with_oauth):
        # Set up an authenticated session
        with client_with_oauth.session_transaction() as sess:
            sess['github_access_token'] = 'user-token'
            sess['github_username'] = 'testuser'

        # Verify authenticated
        status = client_with_oauth.get('/auth/github/status').get_json()
        assert status['authenticated'] is True

        # Logout
        response = client_with_oauth.post('/auth/github/logout')
        assert response.get_json()['status'] == 'logged_out'

        # Verify no longer authenticated
        status = client_with_oauth.get('/auth/github/status').get_json()
        assert status['authenticated'] is False
        assert status['username'] == ''


class TestReportProblemWithOAuth:
    """Tests for report-problem using authenticated user's token."""

    @patch('src.integrations.github_integration.requests.post')
    def test_uses_user_token_when_authenticated(
        self, mock_post, client_with_oauth
    ):
        """When user is OAuth-authenticated, their token is used."""
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'number': 99, 'html_url': 'https://github.com/owner/repo/issues/99'},
        )

        # Simulate authenticated session
        with client_with_oauth.session_transaction() as sess:
            sess['github_access_token'] = 'user-oauth-token'
            sess['github_username'] = 'testuser'

        # Reset the cached instance so it picks up env vars
        import src.integrations.github_integration as mod
        mod._github_instance = None

        response = client_with_oauth.post(
            '/api/github/report-problem',
            json={'summary': 'Test bug', 'description': 'Details'},
            content_type='application/json',
        )

        data = response.get_json()
        assert data['status'] == 'created'

        # Verify the user's token was used in the API call
        call_kwargs = mock_post.call_args
        headers = call_kwargs.kwargs.get('headers') or call_kwargs[1].get('headers')
        assert headers['Authorization'] == 'token user-oauth-token'

        mod._github_instance = None

    @patch('src.integrations.github_integration.requests.post')
    def test_falls_back_to_pat_when_not_authenticated(
        self, mock_post, client_with_oauth
    ):
        """When user is not authenticated, the server PAT is used."""
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'number': 100, 'html_url': 'https://github.com/owner/repo/issues/100'},
        )

        # Reset the cached instance
        import src.integrations.github_integration as mod
        mod._github_instance = None

        response = client_with_oauth.post(
            '/api/github/report-problem',
            json={'summary': 'Test bug', 'description': 'Details'},
            content_type='application/json',
        )

        data = response.get_json()
        assert data['status'] == 'created'

        # Verify the server PAT was used
        call_kwargs = mock_post.call_args
        headers = call_kwargs.kwargs.get('headers') or call_kwargs[1].get('headers')
        assert headers['Authorization'] == 'token server-pat-token'

        mod._github_instance = None


class TestFallbackWithoutOAuth:
    """Tests that PAT-based flow with manual username field still works."""

    @patch('src.integrations.github_integration.requests.post')
    def test_pat_flow_with_manual_username(self, mock_post, client_without_oauth):
        """Without OAuth env vars, the PAT flow with manual fields works."""
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'number': 50, 'html_url': 'https://github.com/owner/repo/issues/50'},
        )

        import src.integrations.github_integration as mod
        mod._github_instance = None

        response = client_without_oauth.post(
            '/api/github/report-problem',
            json={
                'summary': 'Test bug',
                'description': 'Details',
                'reporter_name': 'Test User',
                'reporter_github': 'testuser',
            },
            content_type='application/json',
        )

        data = response.get_json()
        assert data['status'] == 'created'

        # Verify the server PAT was used
        call_kwargs = mock_post.call_args
        headers = call_kwargs.kwargs.get('headers') or call_kwargs[1].get('headers')
        assert headers['Authorization'] == 'token server-pat-token'

        # Verify reporter info was included in the issue body
        body = call_kwargs.kwargs.get('json') or call_kwargs[1].get('json')
        assert '@testuser' in body['body']

        mod._github_instance = None


class TestUserTokenInGitHubIntegration:
    """Tests for the user_token parameter in GitHubIntegration.create_report."""

    @patch('src.integrations.github_integration.requests.post')
    def test_user_token_overrides_config_token(self, mock_post):
        """When user_token is provided, it is used instead of config token."""
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'number': 1, 'html_url': 'https://github.com/o/r/issues/1'},
        )

        config = GitHubConfig(repo='o/r', token='server-pat')
        integration = GitHubIntegration(config)

        result = integration.create_report(
            summary='Bug',
            description='Details',
            user_token='user-personal-token',
        )

        assert result is not None
        call_kwargs = mock_post.call_args
        headers = call_kwargs.kwargs.get('headers') or call_kwargs[1].get('headers')
        assert headers['Authorization'] == 'token user-personal-token'

    @patch('src.integrations.github_integration.requests.post')
    def test_none_user_token_uses_config_token(self, mock_post):
        """When user_token is None, the config (PAT) token is used."""
        mock_post.return_value = MagicMock(
            status_code=201,
            json=lambda: {'number': 2, 'html_url': 'https://github.com/o/r/issues/2'},
        )

        config = GitHubConfig(repo='o/r', token='server-pat')
        integration = GitHubIntegration(config)

        result = integration.create_report(
            summary='Bug',
            description='Details',
        )

        assert result is not None
        call_kwargs = mock_post.call_args
        headers = call_kwargs.kwargs.get('headers') or call_kwargs[1].get('headers')
        assert headers['Authorization'] == 'token server-pat'
