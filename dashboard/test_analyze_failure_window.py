"""Tests for /api/analyze-failure resolving older failures.

A modal opened on an older build has no log text, so the endpoint falls
back to the database. It used to look back only 7 days and answered 404
"No recent failure found for this test" for older builds that were
plainly in the database.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from storage.database import DashboardDatabase
from web.server import create_app

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.yaml')
ANALYSIS = {
    'root_cause': 'stub root cause',
    'component': 'WMCO',
    'confidence': 80,
    'failure_type': 'product_bug',
    'analysis_mode': 'glm-ai',
    'cost': 0.0,
}


@pytest.fixture
def app_and_db():
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    application = create_app(db_path=db_path, config_file=CONFIG_PATH)
    application.config['TESTING'] = True
    db = DashboardDatabase(db_path)  # creates the schema in __init__
    yield application, db, db_path
    db.close()
    os.close(db_fd)
    os.unlink(db_path)


def _insert_failure(db, days_ago):
    """Insert one failed test result dated `days_ago` days in the past."""
    timestamp = (datetime.now() - timedelta(days=days_ago)).isoformat()
    db.conn.execute(
        "INSERT INTO test_results (test_name, version, platform, status, "
        "timestamp, error_message, log_url, job_name, build_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ('OCP-87809', '5.1', 'vsphere', 'failed', timestamp,
         'fail winc.go:84', 'https://example.com/build-log.txt',
         'branch-ci-...-fbc-main-vsphere-ipi-ovn-winc', '2119881961806761984'))
    db.conn.commit()


class TestAnalyzeFailureWindow:
    """The DB resolution window must cover builds the UI can open."""

    def test_failure_older_than_seven_days_is_found(self, app_and_db):
        application, db, _ = app_and_db
        _insert_failure(db, days_ago=12)
        client = application.test_client()

        with patch('ai.analyzer.HybridFailureAnalyzer') as analyzer_cls:
            analyzer_cls.return_value.analyze_failure.return_value = dict(ANALYSIS)
            response = client.post('/api/analyze-failure', json={
                'test_name': 'OCP-87809', 'version': '5.1',
                'platform': 'vsphere', 'use_cached': True})

        assert response.status_code == 200, response.get_json()
        assert response.get_json()['root_cause'] == 'stub root cause'

    def test_failure_older_than_the_cap_is_not_found(self, app_and_db):
        """Negative: the window is bounded, so an ancient failure is not
        analyzed as if it were current."""
        application, db, _ = app_and_db
        _insert_failure(db, days_ago=200)
        client = application.test_client()

        with patch('ai.analyzer.HybridFailureAnalyzer') as analyzer_cls:
            analyzer_cls.return_value.analyze_failure.return_value = dict(ANALYSIS)
            response = client.post('/api/analyze-failure', json={
                'test_name': 'OCP-87809', 'version': '5.1',
                'platform': 'vsphere', 'use_cached': True})

        assert response.status_code == 404
        assert 'No recent failure found' in response.get_json()['error']

    def test_explicit_error_message_skips_the_lookup(self, app_and_db):
        """When the modal supplies log text, a DB row is not required at
        all, even for a much older failure."""
        application, db, _ = app_and_db
        client = application.test_client()

        with patch('ai.analyzer.HybridFailureAnalyzer') as analyzer_cls:
            analyzer_cls.return_value.analyze_failure.return_value = dict(ANALYSIS)
            response = client.post('/api/analyze-failure', json={
                'test_name': 'OCP-87809', 'version': '5.1',
                'platform': 'vsphere', 'use_cached': True,
                'error_message': 'fail winc.go:84 from a pasted log'})

        assert response.status_code == 200


class TestAnalyzeFailureUiMessages:
    """The panel must explain a failure instead of showing a bare
    "Failed to analyze"."""

    @staticmethod
    def _template():
        with open('src/web/templates/dashboard.html') as handle:
            return handle.read()

    def test_progress_text_does_not_promise_five_seconds(self):
        template = self._template()
        assert '~5 seconds' not in template
        assert 'this can take up to a minute' in template

    def test_http_status_is_surfaced(self):
        assert 'Failed to analyze (HTTP ${response.status})' in self._template()

    def test_missing_root_cause_offers_a_retry(self):
        assert 'Analysis returned no result. Click Re-analyze to retry.' \
            in self._template()
