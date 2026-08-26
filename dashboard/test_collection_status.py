"""Tests for collection status error TTL (issue #164).

Validates that stale errors from previous collection runs are
suppressed after the configured TTL, so users don't see a scary
'token expired' banner from a long-ago transient failure.
"""

import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.web.server import create_app, collection_status, _ERROR_TTL_SECONDS
from src.storage.database import DashboardDatabase


@pytest.fixture(autouse=True)
def reset_collection_status():
    """Reset global collection_status between tests."""
    collection_status['running'] = False
    collection_status['progress'] = ''
    collection_status['error'] = None
    collection_status['error_at'] = None
    collection_status['completed_at'] = None
    yield
    collection_status['running'] = False
    collection_status['progress'] = ''
    collection_status['error'] = None
    collection_status['error_at'] = None
    collection_status['completed_at'] = None


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / 'test.db')
    db = DashboardDatabase(path)
    db.close()
    return path


@pytest.fixture
def client(db_path):
    app = create_app(db_path)
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


class TestStaleErrorSuppression:
    """Tests for suppressing stale collection errors via TTL."""

    def test_fresh_error_is_returned(self, client):
        """An error recorded recently is returned to the frontend."""
        collection_status['error'] = (
            'GCSWeb returned HTTP 403 - API token expired or missing'
        )
        collection_status['error_at'] = datetime.now().isoformat()

        response = client.get('/api/collection-status')
        data = response.get_json()
        assert data['error'] is not None
        assert '403' in data['error']

    def test_stale_error_is_suppressed(self, client):
        """An error older than TTL is suppressed on page load."""
        collection_status['error'] = (
            'GCSWeb returned HTTP 403 - API token expired or missing'
        )
        collection_status['error_at'] = (
            datetime.now() - timedelta(seconds=_ERROR_TTL_SECONDS + 60)
        ).isoformat()

        response = client.get('/api/collection-status')
        data = response.get_json()
        assert data['error'] is None

    def test_error_not_suppressed_while_running(self, client):
        """Errors are never suppressed while a collection is running,
        regardless of age."""
        collection_status['running'] = True
        collection_status['error'] = 'Some error'
        collection_status['error_at'] = (
            datetime.now() - timedelta(hours=2)
        ).isoformat()

        response = client.get('/api/collection-status')
        data = response.get_json()
        assert data['error'] == 'Some error'

    def test_no_error_returns_none(self, client):
        """When there is no error, the field is None."""
        response = client.get('/api/collection-status')
        data = response.get_json()
        assert data['error'] is None

    def test_error_without_error_at_is_returned(self, client):
        """Backwards compatibility: if error_at is missing (e.g. from
        an old code path), the error is still returned."""
        collection_status['error'] = 'Legacy error without timestamp'
        collection_status['error_at'] = None

        response = client.get('/api/collection-status')
        data = response.get_json()
        assert data['error'] == 'Legacy error without timestamp'

    def test_error_just_under_ttl_is_returned(self, client):
        """An error just under the TTL threshold is still shown."""
        collection_status['error'] = 'Recent error'
        collection_status['error_at'] = (
            datetime.now() - timedelta(seconds=_ERROR_TTL_SECONDS - 30)
        ).isoformat()

        response = client.get('/api/collection-status')
        data = response.get_json()
        assert data['error'] == 'Recent error'

    def test_error_just_over_ttl_is_suppressed(self, client):
        """An error just over the TTL threshold is suppressed."""
        collection_status['error'] = 'Expired error'
        collection_status['error_at'] = (
            datetime.now() - timedelta(seconds=_ERROR_TTL_SECONDS + 1)
        ).isoformat()

        response = client.get('/api/collection-status')
        data = response.get_json()
        assert data['error'] is None


class TestTriggerCollectionClearsErrorAt:
    """Tests that triggering a new collection clears error_at."""

    def test_trigger_clears_error_at(self, client):
        """Starting a new collection clears both error and error_at."""
        collection_status['error'] = 'Old error'
        collection_status['error_at'] = datetime.now().isoformat()

        with patch(
            'src.web.server.run_collection_background'
        ):
            response = client.post(
                '/api/trigger-collection',
                json={'days': 7},
            )

        assert response.status_code == 200
        assert collection_status['error'] is None
        assert collection_status['error_at'] is None


class TestErrorAtTracking:
    """Tests that error_at is set/cleared alongside error."""

    def test_error_at_set_when_error_present(self, client):
        """Verify that when an error and error_at are both set,
        the API returns the error (within TTL)."""
        now = datetime.now()
        collection_status['error'] = (
            'GCSWeb returned HTTP 403 - token expired'
        )
        collection_status['error_at'] = now.isoformat()

        response = client.get('/api/collection-status')
        data = response.get_json()
        assert data['error'] is not None
        assert '403' in data['error']

    def test_error_at_cleared_on_success(self, client):
        """After a successful collection, error and error_at are
        both None."""
        collection_status['error'] = None
        collection_status['error_at'] = None
        collection_status['completed_at'] = datetime.now().isoformat()

        response = client.get('/api/collection-status')
        data = response.get_json()
        assert data['error'] is None

    def test_error_at_cleared_on_new_trigger(self, client):
        """Triggering a new collection clears error_at."""
        collection_status['error'] = 'Previous error'
        collection_status['error_at'] = datetime.now().isoformat()

        with patch('src.web.server.run_collection_background'):
            client.post('/api/trigger-collection', json={'days': 7})

        assert collection_status['error_at'] is None
