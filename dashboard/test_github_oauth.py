"""Tests for GitHub OAuth flow.

Validates OAuth login redirect, callback token exchange, status endpoint,
logout, fallback to PAT when OAuth is not configured, and security
(tokens never exposed in API responses).
"""

import os
import re
import pytest
from unittest.mock import patch, MagicMock

from src.web.server import create_app, _oauth_token_store, _BoundedTokenStore
from src.integrations.github_integration import GitHubIntegration, GitHubConfig


@pytest.fixture(autouse=True)
def clear_token_store():
    """Clear server-side OAuth token store between tests."""
    _oauth_token_store.clear()
    yield
    _oauth_token_store.clear()


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
        assert 'redirect_uri=' in location
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

        # Verify the token is stored server-side, NOT in the session cookie
        assert len(_oauth_token_store) == 1
        assert 'user-oauth-token' in _oauth_token_store.values()

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
        # Simulate an authenticated session with server-side token store
        token_id = 'test-token-id'
        _oauth_token_store[token_id] = 'secret-token-value'
        with client_with_oauth.session_transaction() as sess:
            sess['oauth_token_id'] = token_id
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
        # Set up an authenticated session with server-side token store
        token_id = 'test-token-id'
        _oauth_token_store[token_id] = 'user-token'
        with client_with_oauth.session_transaction() as sess:
            sess['oauth_token_id'] = token_id
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

        # Verify token was removed from server-side store
        assert token_id not in _oauth_token_store


class TestReportProblemRedirect:
    """Tests that the report-problem form redirects to GitHub new-issue URL."""

    def test_dashboard_renders_with_github_repo(self, client_with_oauth):
        """Dashboard template receives github_repo from GITHUB_REPO env var."""
        response = client_with_oauth.get('/')
        assert response.status_code == 200
        html = response.data.decode()
        assert 'owner/repo' in html

    def test_dashboard_renders_without_github_repo(self, db_path):
        """Dashboard still renders when GITHUB_REPO is not set."""
        with patch.dict(os.environ, {}, clear=True):
            app = create_app(db_path)
            app.config['TESTING'] = True
            client = app.test_client()
            response = client.get('/')
            assert response.status_code == 200

    def test_report_problem_route_removed(self, client_with_oauth):
        """The old server-side report-problem API route no longer exists."""
        response = client_with_oauth.post(
            '/api/github/report-problem',
            json={'summary': 'Test', 'description': 'Details'},
            content_type='application/json',
        )
        assert response.status_code == 404


class TestReportProblemAuthGating:
    """Tests that the Report a Problem button is gated by GitHub OAuth.

    Tests verify structural correctness per AGENTS.md rule 13: function
    invocation at page load, conditional branching on auth state, guard
    ordering before form display, and visible user feedback on the
    unauthenticated path.
    """

    @staticmethod
    def _extract_script(html):
        """Extract the inline ``<script>`` block from dashboard HTML."""
        match = re.search(r'<script>(.*?)</script>', html, re.DOTALL)
        assert match, "No inline <script> block found in dashboard HTML"
        return match.group(1)

    @staticmethod
    def _extract_function_body(script, func_name):
        """Return the body text of a named JS function.

        Handles ``async function`` declarations and counts nested braces
        to capture the complete body.
        """
        pattern = (
            r'(?:async\s+)?function\s+'
            + re.escape(func_name)
            + r'\s*\([^)]*\)\s*\{'
        )
        match = re.search(pattern, script)
        assert match, f"Function '{func_name}' not found in script"
        start = match.end()
        depth = 1
        pos = start
        while pos < len(script) and depth > 0:
            if script[pos] == '{':
                depth += 1
            elif script[pos] == '}':
                depth -= 1
            pos += 1
        return script[start:pos - 1]

    def test_auth_check_invoked_on_load(self, client_with_oauth):
        """checkGitHubAuth must be called on page load, not just defined.

        Fails if the function is defined but never invoked, because an
        uncalled auth check cannot gate the report button.
        """
        response = client_with_oauth.get('/')
        html = response.data.decode()
        script = self._extract_script(html)

        # The function must be defined and must fetch auth status
        body = self._extract_function_body(script, 'checkGitHubAuth')
        assert '/auth/github/status' in body, \
            "checkGitHubAuth does not fetch /auth/github/status"

        # It must call updateReportButton to apply the result
        assert re.search(r'updateReportButton\s*\(', body), \
            "checkGitHubAuth does not call updateReportButton()"

        # Locate the function definition span so we can exclude it
        func_def = re.search(
            r'(?:async\s+)?function\s+checkGitHubAuth\s*\(', script,
        )
        body_open = script.index('{', func_def.end() - 1)
        depth, pos = 1, body_open + 1
        while pos < len(script) and depth > 0:
            if script[pos] == '{':
                depth += 1
            elif script[pos] == '}':
                depth -= 1
            pos += 1
        func_end = pos

        # The call must appear OUTSIDE the function definition (i.e. at
        # the script's top-level initialization code that runs on load).
        code_outside = script[:func_def.start()] + script[func_end:]
        assert re.search(r'checkGitHubAuth\s*\(\s*\)', code_outside), \
            "checkGitHubAuth() is defined but never invoked at page load"

    def test_button_state_conditional_on_auth(self, client_with_oauth):
        """updateReportButton must branch on unauthenticated state.

        Fails if the conditional is removed, doesn't check both
        oauth_configured and authenticated, or omits the negation
        on authenticated (which would swap the branches).
        """
        response = client_with_oauth.get('/')
        html = response.data.decode()
        script = self._extract_script(html)

        body = self._extract_function_body(script, 'updateReportButton')

        # Must contain a conditional checking oauth_configured
        assert re.search(r'oauth_configured', body), \
            "updateReportButton does not check oauth_configured"

        # Must negate authenticated (i.e. the branch is for the
        # *unauthenticated* case).  If someone swaps the branches
        # by removing the ``!``, this assertion catches it.
        assert re.search(r'!\s*githubAuthState\.authenticated', body), \
            "updateReportButton does not check for !authenticated"

        # The conditional must change the button text
        assert 'textContent' in body, \
            "updateReportButton does not update button text"

    def test_modal_guard_precedes_form(self, client_with_oauth):
        """Auth guard in openReportModal must precede the form display.

        Fails if the guard is removed or if the modal is displayed
        before the auth check runs.
        """
        response = client_with_oauth.get('/')
        html = response.data.decode()
        script = self._extract_script(html)

        body = self._extract_function_body(script, 'openReportModal')

        # Auth guard must exist with the unauthenticated condition
        guard = re.search(
            r'if\s*\([^)]*oauth_configured[^)]*!\s*\w*\.?authenticated',
            body,
        )
        if not guard:
            guard = re.search(
                r'if\s*\([^)]*!\s*\w*\.?authenticated[^)]*oauth_configured',
                body,
            )
        assert guard, "openReportModal has no auth guard condition"

        # The guard block must redirect unauthenticated users to login
        login_redirect = re.search(r'/auth/github/login', body)
        assert login_redirect, \
            "openReportModal does not redirect to login"

        # Modal display must come AFTER the guard
        modal_show = re.search(
            r'reportModal.*display\s*=\s*[\'"]block[\'"]',
            body, re.DOTALL,
        )
        assert modal_show, \
            "openReportModal does not display the report modal"

        assert guard.start() < modal_show.start(), \
            "Auth guard must precede modal display in openReportModal"

    def test_unauthenticated_path_visible(self, client_with_oauth):
        """Unauthenticated users must see visible feedback.

        Fails if the unauthenticated branch does not produce user-
        visible text or a login redirect, which would silently swallow
        the error.
        """
        response = client_with_oauth.get('/')
        html = response.data.decode()
        script = self._extract_script(html)

        body = self._extract_function_body(script, 'updateReportButton')

        # The negation on authenticated marks the unauthenticated branch
        negation = re.search(r'!\s*githubAuthState\.authenticated', body)
        assert negation, \
            "updateReportButton has no unauthenticated branch"

        # Visible button text must appear after the unauthenticated check
        login_text_pos = body.find('Log in to Report')
        assert login_text_pos != -1, \
            "No 'Log in to Report' text for unauthenticated users"
        assert negation.start() < login_text_pos, \
            "'Log in to Report' must be inside the unauthenticated branch"

        # The unauthenticated path must redirect to GitHub login
        login_url_pos = body.find('/auth/github/login')
        assert login_url_pos != -1, \
            "No login redirect for unauthenticated users"

        # The button must be targeted by its DOM ID so JS can update it
        assert 'reportProblemBtn' in body, \
            "updateReportButton does not reference the button by ID"


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


class TestBoundedTokenStore:
    """Tests for TTL expiry and max-size eviction in _BoundedTokenStore."""

    def test_token_expires_after_ttl(self):
        """Insert a token, advance time past TTL, verify it is gone."""
        current_time = 1000.0

        def fake_clock():
            return current_time

        store = _BoundedTokenStore(max_size=100, max_age=3600, clock=fake_clock)
        store['tok1'] = 'access-token-1'

        # Before TTL: token is present
        assert store.get('tok1') == 'access-token-1'
        assert 'tok1' in store

        # Advance time past TTL
        current_time = 1000.0 + 3601
        assert store.get('tok1') is None
        assert 'tok1' not in store

    def test_token_store_respects_max_size(self):
        """Insert max_size + 1 tokens, verify the oldest is evicted."""
        store = _BoundedTokenStore(max_size=3, max_age=86400)

        store['a'] = 'token-a'
        store['b'] = 'token-b'
        store['c'] = 'token-c'
        assert len(store) == 3

        # Adding a fourth token evicts the oldest (a)
        store['d'] = 'token-d'
        assert len(store) == 3
        assert store.get('a') is None
        assert store.get('b') == 'token-b'
        assert store.get('d') == 'token-d'

    def test_explicit_logout_removes_token_immediately(self):
        """Verify pop() removes token before TTL expires."""
        store = _BoundedTokenStore(max_size=100, max_age=86400)
        store['tok1'] = 'access-token'

        assert store.get('tok1') == 'access-token'
        removed = store.pop('tok1', None)
        assert removed == 'access-token'
        assert store.get('tok1') is None

    def test_clear_removes_all_tokens(self):
        """Verify clear() empties the store."""
        store = _BoundedTokenStore(max_size=100, max_age=86400)
        store['a'] = '1'
        store['b'] = '2'
        assert len(store) == 2

        store.clear()
        assert len(store) == 0
        assert store.get('a') is None

    def test_values_returns_only_live_tokens(self):
        """Expired tokens should not appear in values()."""
        current_time = 1000.0

        def fake_clock():
            return current_time

        store = _BoundedTokenStore(max_size=100, max_age=3600, clock=fake_clock)
        store['old'] = 'old-token'

        current_time = 2000.0
        store['new'] = 'new-token'

        # Advance past TTL for 'old' but not 'new'
        current_time = 1000.0 + 3601
        vals = store.values()
        assert 'old-token' not in vals
        assert 'new-token' in vals

    def test_len_excludes_expired_tokens(self):
        """len() should not count expired entries."""
        current_time = 1000.0

        def fake_clock():
            return current_time

        store = _BoundedTokenStore(max_size=100, max_age=60, clock=fake_clock)
        store['a'] = '1'
        store['b'] = '2'
        assert len(store) == 2

        current_time = 1000.0 + 61
        assert len(store) == 0

    def test_re_insert_refreshes_position(self):
        """Re-inserting a key should move it to most-recent position."""
        store = _BoundedTokenStore(max_size=2, max_age=86400)
        store['a'] = '1'
        store['b'] = '2'

        # Re-insert 'a' so it becomes most-recent
        store['a'] = '1-refreshed'

        # Adding 'c' should evict 'b' (oldest), not 'a'
        store['c'] = '3'
        assert store.get('a') == '1-refreshed'
        assert store.get('b') is None
        assert store.get('c') == '3'

    def test_module_level_store_is_bounded(self):
        """The module-level _oauth_token_store should be a _BoundedTokenStore."""
        assert isinstance(_oauth_token_store, _BoundedTokenStore)
