"""Tests for Report a Problem admin notification via @mentions.

Validates that when GITHUB_NOTIFY_USERS is configured, the dashboard
template includes @mentions in the issue body so repo admins are
notified. Also verifies no mentions appear when the variable is unset.
"""

import os
import re
import pytest
from unittest.mock import patch

from src.web.server import create_app


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary database for testing."""
    from src.storage.database import DashboardDatabase
    path = str(tmp_path / 'test.db')
    db = DashboardDatabase(path)
    db.close()
    return path


def _extract_script(html):
    """Extract the inline ``<script>`` block from dashboard HTML."""
    match = re.search(r'<script>(.*?)</script>', html, re.DOTALL)
    assert match, "No inline <script> block found in dashboard HTML"
    return match.group(1)


def _extract_function_body(script, func_name):
    """Return the body text of a named JS function."""
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


class TestReportNotifyUsers:
    """Tests for admin @mention inclusion in Report a Problem body."""

    def test_mentions_included_when_notify_users_set(self, db_path):
        """When GITHUB_NOTIFY_USERS is set, the template renders
        a JS array of usernames that gets appended to the issue body."""
        env = {
            'GITHUB_REPO': 'owner/repo',
            'GITHUB_NOTIFY_USERS': 'alice,bob',
        }
        with patch.dict(os.environ, env, clear=True):
            app = create_app(db_path)
            app.config['TESTING'] = True
            client = app.test_client()
            response = client.get('/')

        html = response.data.decode()
        script = _extract_script(html)
        body = _extract_function_body(script, 'submitReport')

        # The notifyUsers array must contain the configured usernames
        assert '"alice"' in body
        assert '"bob"' in body

        # The /cc mention block must appear after the footer
        cc_match = re.search(r'/cc.*@', body)
        assert cc_match, "submitReport body should contain /cc @mention logic"

    def test_no_mentions_when_notify_users_empty(self, db_path):
        """When GITHUB_NOTIFY_USERS is not set, the notify list is empty
        and no /cc block is appended."""
        env = {
            'GITHUB_REPO': 'owner/repo',
        }
        with patch.dict(os.environ, env, clear=True):
            app = create_app(db_path)
            app.config['TESTING'] = True
            client = app.test_client()
            response = client.get('/')

        html = response.data.decode()
        script = _extract_script(html)
        body = _extract_function_body(script, 'submitReport')

        # The array must be empty so no mentions are appended
        assert '[]' in body

    def test_at_signs_stripped_from_input(self, db_path):
        """Leading @ in GITHUB_NOTIFY_USERS values are stripped so the
        template adds them programmatically (avoiding @@user)."""
        env = {
            'GITHUB_REPO': 'owner/repo',
            'GITHUB_NOTIFY_USERS': '@alice, @bob',
        }
        with patch.dict(os.environ, env, clear=True):
            app = create_app(db_path)
            app.config['TESTING'] = True
            client = app.test_client()
            response = client.get('/')

        html = response.data.decode()
        script = _extract_script(html)
        body = _extract_function_body(script, 'submitReport')

        # Usernames should appear without leading @
        assert '"alice"' in body
        assert '"bob"' in body
        # No doubled @@ should be possible
        assert '@@' not in body

    def test_whitespace_entries_ignored(self, db_path):
        """Blank entries from trailing commas or extra spaces are
        filtered out so the notify list has no empty usernames."""
        env = {
            'GITHUB_REPO': 'owner/repo',
            'GITHUB_NOTIFY_USERS': 'alice, , ,bob,',
        }
        with patch.dict(os.environ, env, clear=True):
            app = create_app(db_path)
            app.config['TESTING'] = True
            client = app.test_client()
            response = client.get('/')

        html = response.data.decode()
        script = _extract_script(html)
        body = _extract_function_body(script, 'submitReport')

        # Only real usernames should appear
        assert '"alice"' in body
        assert '"bob"' in body
        assert '""' not in body

    def test_mention_appended_after_footer(self, db_path):
        """The /cc mention block must appear after the 'Reported via CI
        Dashboard' footer in the constructed body, not before it."""
        env = {
            'GITHUB_REPO': 'owner/repo',
            'GITHUB_NOTIFY_USERS': 'admin1',
        }
        with patch.dict(os.environ, env, clear=True):
            app = create_app(db_path)
            app.config['TESTING'] = True
            client = app.test_client()
            response = client.get('/')

        html = response.data.decode()
        script = _extract_script(html)
        body = _extract_function_body(script, 'submitReport')

        footer_pos = body.find('Reported via CI Dashboard')
        cc_pos = body.find('/cc')
        assert footer_pos != -1, "Footer text missing from submitReport"
        assert cc_pos != -1, "/cc mention block missing from submitReport"
        assert footer_pos < cc_pos, \
            "/cc mention must come after the footer in the body"

    def test_mention_conditional_on_nonempty_array(self, db_path):
        """The /cc block must only be appended when notifyUsers is
        non-empty — verified by checking the if-guard structure."""
        env = {
            'GITHUB_REPO': 'owner/repo',
            'GITHUB_NOTIFY_USERS': 'admin1',
        }
        with patch.dict(os.environ, env, clear=True):
            app = create_app(db_path)
            app.config['TESTING'] = True
            client = app.test_client()
            response = client.get('/')

        html = response.data.decode()
        script = _extract_script(html)
        body = _extract_function_body(script, 'submitReport')

        # There must be a length check guarding the /cc append
        guard = re.search(r'notifyUsers\.length\s*>\s*0', body)
        assert guard, \
            "submitReport must guard /cc append with notifyUsers.length > 0"

        # The /cc append must be inside the guard (after it)
        cc_pos = body.find('/cc')
        assert cc_pos > guard.start(), \
            "/cc append must be inside the notifyUsers length guard"
