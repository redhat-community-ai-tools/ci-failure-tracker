"""Tests for surfacing degraded collections in the dashboard.

Commit 744787e made a private-gcsweb outage non-fatal: ``health_check()``
records ``collection_status['warning']`` and collection continues from the
public source. The frontend only rendered ``error``, so a run that stored
zero private job runs was indistinguishable from a healthy "no new data"
run - a dead token stayed invisible for days.

These tests pin two things:

1. The API exposes the warning (it is not suppressed by the stale-error
   TTL, which only applies to ``error``).
2. The template wires that warning into a visible banner, and hides it
   while a collection is running or after a hard failure, where the other
   banners already tell the story.
"""

import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.web.server import create_app, collection_status


@pytest.fixture(autouse=True)
def reset_collection_status():
    """Reset global collection_status between tests."""
    collection_status['running'] = False
    collection_status['progress'] = ''
    collection_status['error'] = None
    collection_status['error_at'] = None
    collection_status['warning'] = None
    collection_status['completed_at'] = None
    yield
    collection_status['running'] = False
    collection_status['progress'] = ''
    collection_status['error'] = None
    collection_status['error_at'] = None
    collection_status['warning'] = None
    collection_status['completed_at'] = None


@pytest.fixture
def app():
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    with patch.dict(os.environ, {'GITHUB_REPO': 'org/test-repo'}):
        application = create_app(db_path=db_path, config_file=config_path)
        application.config['TESTING'] = True
        yield application
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


class TestCollectionStatusWarningAPI:
    """The warning must reach the browser."""

    def test_warning_is_returned(self, client):
        """A degraded run surfaces its warning through the status API."""
        collection_status['warning'] = (
            'GCSWeb returned HTTP 500 - check URL: '
            'https://gcsweb-qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com'
        )

        payload = client.get('/api/collection-status').get_json()

        assert payload['error'] is None
        assert 'HTTP 500' in payload['warning']

    def test_warning_is_not_suppressed_by_error_ttl(self, client):
        """The stale-error TTL applies to errors only, never to warnings."""
        collection_status['error'] = 'GCSWeb returned HTTP 403'
        collection_status['error_at'] = (
            datetime.now() - timedelta(days=30)
        ).isoformat()
        collection_status['warning'] = 'Private source unavailable'

        payload = client.get('/api/collection-status').get_json()

        assert payload['error'] is None
        assert payload['warning'] == 'Private source unavailable'

    def test_warning_absent_when_collection_is_healthy(self, client):
        """A healthy run clears the warning."""
        payload = client.get('/api/collection-status').get_json()

        assert payload['warning'] is None


class TestDegradedBannerWiring:
    """The template must render the warning it receives."""

    @pytest.fixture
    def html(self, client):
        return client.get('/').get_data(as_text=True)

    def test_banner_markup_present(self, html):
        assert 'id="degradedBanner"' in html
        assert 'id="degradedMessage"' in html
        assert 'Partial Data Collection' in html

    def test_render_helper_present(self, html):
        assert 'function renderDegradedWarning(' in html

    def test_status_warning_is_rendered(self, html):
        assert 'renderDegradedWarning(status.warning)' in html

    def test_banner_hidden_while_running_or_error(self, html):
        assert 'if (status.running || status.error)' in html
        assert 'renderDegradedWarning(null)' in html

    def test_warning_text_is_escaped(self, html):
        """Warning strings come from the server and must be escaped."""
        helper = html.split('function renderDegradedWarning(', 1)[1]
        body = helper.split('async function checkCollectionStatus()', 1)[0]
        assert 'escapeHtml(warning)' in body

    def test_auth_warning_points_at_token_renewal(self, html):
        helper = html.split('function renderDegradedWarning(', 1)[1]
        body = helper.split('async function checkCollectionStatus()', 1)[0]
        assert 'token/request' in body
        assert 'Token Renewal' in body
