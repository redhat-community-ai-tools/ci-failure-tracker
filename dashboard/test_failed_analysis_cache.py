"""Tests for failed AI analysis cache exclusion and resolver window.

Validates that:
- Failed analyses (analysis_mode='failed') are excluded from cache
  serving in get_ai_analysis (issue #255, defect 1)
- Successful analyses ('glm-ai', 'anthropic-api', 'vertex-ai') are
  still returned (negative test per AGENTS.md rule 8)
- Failed rows are still persisted and counted by get_analysis_stats
  (regression test per AGENTS.md rule 11)
- The error-message resolver honors the request days parameter
  instead of hardcoding 7 (issue #255, defect 2)
"""

import os
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from conftest import error_message_hash as _hash
from src.storage.database import DashboardDatabase
from web.server import create_app


@pytest.fixture
def db(tmp_path):
    """Create a temporary database for testing."""
    db_path = str(tmp_path / "test.db")
    database = DashboardDatabase(db_path)
    yield database
    database.close()


class TestFailedAnalysisCacheExclusion:
    """Tests for excluding failed analyses from cache serving."""

    def test_failed_analysis_not_returned_from_cache(self, db):
        """A cached row with analysis_mode='failed' must not be served."""
        error_hash = _hash('GLM analysis failed - check ENMAAS_KEY')
        db.save_ai_analysis('OCP-28632', '5.1', analysis={
            'root_cause': 'GLM analysis failed - check ENMAAS_KEY and quota',
            'confidence': 0,
            'failure_type': None,
            'analysis_mode': 'failed',
            'cost': 0.0,
        }, error_message_hash=error_hash)

        result = db.get_ai_analysis(
            'OCP-28632', '5.1',
            current_error_hash=error_hash,
        )
        assert result is None

    def test_failed_analysis_not_returned_without_hash(self, db):
        """A failed row is excluded even when no current_error_hash given."""
        db.save_ai_analysis('OCP-28632', '5.1', analysis={
            'root_cause': 'GLM analysis failed',
            'confidence': 0,
            'analysis_mode': 'failed',
        })

        result = db.get_ai_analysis('OCP-28632', '5.1')
        assert result is None

    def test_glm_ai_success_returned_from_cache(self, db):
        """Successful glm-ai analysis must still be served from cache."""
        error_hash = _hash('some real error')
        db.save_ai_analysis('OCP-28632', '5.1', analysis={
            'root_cause': 'Operator install timeout',
            'confidence': 85,
            'failure_type': 'product_bug',
            'analysis_mode': 'glm-ai',
            'cost': 0.01,
        }, error_message_hash=error_hash)

        result = db.get_ai_analysis(
            'OCP-28632', '5.1',
            current_error_hash=error_hash,
        )
        assert result is not None
        assert result['analysis_mode'] == 'glm-ai'
        assert result['confidence'] == 85

    def test_anthropic_api_success_returned_from_cache(self, db):
        """Legacy anthropic-api analysis must still be served."""
        error_hash = _hash('legacy error')
        db.save_ai_analysis('OCP-11111', '4.21', analysis={
            'root_cause': 'Legacy cause',
            'confidence': 80,
            'failure_type': 'product_bug',
            'analysis_mode': 'anthropic-api',
        }, error_message_hash=error_hash)

        result = db.get_ai_analysis(
            'OCP-11111', '4.21',
            current_error_hash=error_hash,
        )
        assert result is not None
        assert result['analysis_mode'] == 'anthropic-api'

    def test_vertex_ai_success_returned_from_cache(self, db):
        """Legacy vertex-ai analysis must still be served."""
        error_hash = _hash('vertex error')
        db.save_ai_analysis('OCP-22222', '4.22', analysis={
            'root_cause': 'Vertex cause',
            'confidence': 90,
            'failure_type': 'automation_bug',
            'analysis_mode': 'vertex-ai',
        }, error_message_hash=error_hash)

        result = db.get_ai_analysis(
            'OCP-22222', '4.22',
            current_error_hash=error_hash,
        )
        assert result is not None
        assert result['analysis_mode'] == 'vertex-ai'

    def test_pre_classifier_returned_from_cache(self, db):
        """Pre-classifier analysis must still be served."""
        db.save_ai_analysis('OCP-33333', '4.22', analysis={
            'root_cause': 'Known issue',
            'confidence': 100,
            'failure_type': 'known_issue',
            'analysis_mode': 'pre-classifier',
        })

        result = db.get_ai_analysis('OCP-33333', '4.22')
        assert result is not None
        assert result['analysis_mode'] == 'pre-classifier'

    def test_failed_row_still_persisted(self, db):
        """Failed analysis rows must still be saved to the database."""
        db.save_ai_analysis('OCP-28632', '5.1', analysis={
            'root_cause': 'GLM analysis failed',
            'confidence': 0,
            'analysis_mode': 'failed',
        })

        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT analysis_mode FROM ai_analyses WHERE test_name = ?",
            ('OCP-28632',),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == 'failed'

    def test_failed_rows_still_counted_in_stats(self, db):
        """Failed rows must still appear in get_analysis_stats totals."""
        db.save_ai_analysis('OCP-28632', '5.1', analysis={
            'root_cause': 'GLM analysis failed',
            'confidence': 0,
            'analysis_mode': 'failed',
            'cost': 0.0,
        })
        db.save_ai_analysis('OCP-11111', '4.21', analysis={
            'root_cause': 'Real cause',
            'confidence': 85,
            'analysis_mode': 'glm-ai',
            'cost': 0.01,
        })

        stats = db.get_analysis_stats()
        assert stats['total_analyses'] == 2
        assert stats['api_count'] == 1

    def test_legacy_null_analysis_mode_returned_from_cache(self, db):
        """Legacy rows with NULL analysis_mode must still be served.

        The SQL filter ``(analysis_mode IS NULL OR analysis_mode != 'failed')``
        relies on the explicit IS NULL check because ``NULL != 'failed'``
        evaluates to NULL (falsy) in SQL, not TRUE.  This test guards against
        a future simplification to just ``analysis_mode != 'failed'`` which
        would silently exclude legacy cached analyses.
        """
        # Save an analysis without analysis_mode so the column stores NULL
        db.save_ai_analysis('OCP-44444', '4.21', analysis={
            'root_cause': 'Legacy analysis without mode',
            'confidence': 75,
            'failure_type': 'product_bug',
        })

        # Verify the stored row actually has NULL analysis_mode
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT analysis_mode FROM ai_analyses WHERE test_name = ?",
            ('OCP-44444',),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] is None

        result = db.get_ai_analysis('OCP-44444', '4.21')
        assert result is not None
        assert result['confidence'] == 75

    def test_successful_overwrites_failed_in_cache(self, db):
        """After a failed row, a new successful analysis must be served."""
        error_hash = _hash('transient error')

        db.save_ai_analysis('OCP-28632', '5.1', analysis={
            'root_cause': 'GLM analysis failed',
            'confidence': 0,
            'analysis_mode': 'failed',
        }, error_message_hash=error_hash)

        # Simulate successful retry
        db.save_ai_analysis('OCP-28632', '5.1', analysis={
            'root_cause': 'Operator install timeout',
            'confidence': 85,
            'failure_type': 'product_bug',
            'analysis_mode': 'glm-ai',
            'cost': 0.01,
        }, error_message_hash=error_hash)

        result = db.get_ai_analysis(
            'OCP-28632', '5.1',
            current_error_hash=error_hash,
        )
        assert result is not None
        assert result['analysis_mode'] == 'glm-ai'
        assert result['confidence'] == 85


class TestResolverDaysParameter:
    """Tests for the error-message resolver honoring the days parameter."""

    @pytest.fixture
    def app(self, tmp_path, monkeypatch):
        """Create a test Flask app."""
        monkeypatch.setenv('ENABLE_AI_ANALYSIS', 'true')
        db_path = str(tmp_path / "test.db")
        config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
        application = create_app(db_path=db_path, config_file=config_path)
        application.config['TESTING'] = True
        yield application

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_resolver_finds_failure_within_requested_days(self, app, tmp_path, monkeypatch):
        """POST with days=30 must find a failure 15 days old."""
        db_path = str(tmp_path / "test.db")
        db = DashboardDatabase(db_path)

        # Insert a failure 15 days ago
        old_date = datetime.now() - timedelta(days=15)
        cursor = db.conn.cursor()
        cursor.execute("""
            INSERT INTO test_results (
                test_name, version, platform, status,
                timestamp, error_message, job_name, build_id
            ) VALUES (?, ?, ?, 'failed', ?, ?, ?, ?)
        """, ('OCP-28632', '5.1', 'azure', old_date.isoformat(),
              'GLM error message', 'periodic-ci-azure', 'build-1'))
        db.conn.commit()
        db.close()

        client = app.test_client()
        with patch('ai.analyzer.HybridFailureAnalyzer') as mock_cls:
            mock_analyzer = mock_cls.return_value
            mock_analyzer.analyze_failure.return_value = {
                'root_cause': 'Resolved cause',
                'confidence': 85,
                'failure_type': 'product_bug',
                'analysis_mode': 'glm-ai',
                'cost': 0.01,
            }

            resp = client.post('/api/analyze-failure', json={
                'test_name': 'OCP-28632',
                'version': '5.1',
                'platform': 'azure',
                'days': 30,
                'use_cached': False,
            })

        assert resp.status_code == 200

    def test_resolver_404_when_failure_outside_days_window(self, app, tmp_path):
        """POST with days=7 must NOT find a 15-day-old failure."""
        db_path = str(tmp_path / "test.db")
        db = DashboardDatabase(db_path)

        old_date = datetime.now() - timedelta(days=15)
        cursor = db.conn.cursor()
        cursor.execute("""
            INSERT INTO test_results (
                test_name, version, platform, status,
                timestamp, error_message, job_name, build_id
            ) VALUES (?, ?, ?, 'failed', ?, ?, ?, ?)
        """, ('OCP-28632', '5.1', 'azure', old_date.isoformat(),
              'GLM error message', 'periodic-ci-azure', 'build-2'))
        db.conn.commit()
        db.close()

        client = app.test_client()
        resp = client.post('/api/analyze-failure', json={
            'test_name': 'OCP-28632',
            'version': '5.1',
            'platform': 'azure',
            'days': 7,
        })

        assert resp.status_code == 404

    def test_resolver_caps_days_at_30(self, app, tmp_path):
        """POST with days=90 must be bounded to 30 days."""
        db_path = str(tmp_path / "test.db")
        db = DashboardDatabase(db_path)

        # Insert a failure 45 days ago — beyond the 30-day cap
        old_date = datetime.now() - timedelta(days=45)
        cursor = db.conn.cursor()
        cursor.execute("""
            INSERT INTO test_results (
                test_name, version, platform, status,
                timestamp, error_message, job_name, build_id
            ) VALUES (?, ?, ?, 'failed', ?, ?, ?, ?)
        """, ('OCP-28632', '5.1', 'azure', old_date.isoformat(),
              'GLM error message', 'periodic-ci-azure', 'build-3'))
        db.conn.commit()
        db.close()

        client = app.test_client()
        resp = client.post('/api/analyze-failure', json={
            'test_name': 'OCP-28632',
            'version': '5.1',
            'platform': 'azure',
            'days': 90,
        })

        assert resp.status_code == 404
