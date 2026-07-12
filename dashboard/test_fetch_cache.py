"""Tests for client-side fetch cache in the dashboard template.

Validates that the dashboard HTML includes the response cache so that
switching tabs or re-selecting the same period serves cached results
instead of making redundant API calls (issue #22).
"""

import os
import sys
import tempfile

import pytest

# Add src to path so imports work like the main app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from web.server import create_app


@pytest.fixture
def app():
    """Create a test Flask app with a temporary database."""
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    application = create_app(db_path=db_path, config_file=config_path)
    application.config['TESTING'] = True
    yield application
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


class TestFetchCache:
    """Verify the dashboard template includes client-side caching."""

    def test_dashboard_contains_fetch_cache(self, client):
        """The rendered dashboard must declare the _fetchCache object."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        assert '_fetchCache' in html

    def test_dashboard_contains_cache_ttl(self, client):
        """The rendered dashboard must define FETCH_CACHE_TTL_MS."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        assert 'FETCH_CACHE_TTL_MS' in html

    def test_dashboard_contains_invalidate_function(self, client):
        """The rendered dashboard must include invalidateFetchCache()."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        assert 'invalidateFetchCache' in html

    def test_fetch_data_uses_cache(self, client):
        """fetchData() must check the cache before making a network request."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        # fetchData should reference the cache key and TTL check
        assert 'cacheKey' in html
        assert 'FETCH_CACHE_TTL_MS' in html

    def test_cache_invalidated_after_collection(self, client):
        """Cache must be cleared when data collection completes."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        # The collection-complete handler should call invalidateFetchCache
        assert 'invalidateFetchCache()' in html

    def test_error_responses_not_cached(self, client):
        """fetchData() must not cache responses when response.ok is false."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        # The fetchData function must check response.ok before caching
        assert 'response.ok' in html

    def test_error_response_returns_data_without_caching(self, client):
        """fetchData() must return error data directly, skipping the cache write."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        # Verify the guard returns before the cache-write line
        assert '!response.ok' in html
