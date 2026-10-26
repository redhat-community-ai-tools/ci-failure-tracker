"""Tests for force_ai server wiring in /api/analyze-failure.

Validates that:
- use_cached=False in the request body passes force_ai=True to the analyzer
- Non-boolean use_cached values are rejected with HTTP 400
"""

import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.storage.database import DashboardDatabase
from web.server import create_app


class TestServerForceAiWiring:
    """Integration test verifying server translates use_cached=False to force_ai=True."""

    @pytest.fixture
    def app(self, tmp_path, monkeypatch):
        """Create a test Flask app with a seeded database."""
        monkeypatch.setenv('ENABLE_AI_ANALYSIS', 'true')
        db_path = str(tmp_path / "test.db")

        db = DashboardDatabase(db_path)
        recent = datetime.now() - timedelta(days=1)
        cursor = db.conn.cursor()
        cursor.execute("""
            INSERT INTO test_results (
                test_name, version, platform, status,
                timestamp, error_message, job_name, build_id
            ) VALUES (?, ?, ?, 'failed', ?, ?, ?, ?)
        """, ('OCP-77777', '5.1', 'aws', recent.isoformat(),
              'some error', 'periodic-ci-aws', 'build-1'))
        db.conn.commit()
        db.close()

        config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
        application = create_app(db_path=db_path, config_file=config_path)
        application.config['TESTING'] = True
        yield application

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_use_cached_false_passes_force_ai_true(self, client):
        """use_cached: false in request body must result in force_ai=True passed to analyze_failure."""
        with patch('ai.analyzer.HybridFailureAnalyzer') as mock_cls:
            mock_analyzer = mock_cls.return_value
            mock_analyzer.analyze_failure.return_value = {
                'root_cause': 'Test root cause',
                'confidence': 85,
                'failure_type': 'product_bug',
                'analysis_mode': 'glm-ai',
                'cost': 0.0,
            }

            resp = client.post('/api/analyze-failure', json={
                'test_name': 'OCP-77777',
                'version': '5.1',
                'platform': 'aws',
                'error_message': 'some error',
                'use_cached': False,
            })

            assert resp.status_code == 200
            mock_analyzer.analyze_failure.assert_called_once()
            _, kwargs = mock_analyzer.analyze_failure.call_args
            assert kwargs['force_ai'] is True

    def test_use_cached_string_false_returns_400(self, client):
        """use_cached: "false" (string) must be rejected with HTTP 400."""
        resp = client.post('/api/analyze-failure', json={
            'test_name': 'OCP-77777',
            'version': '5.1',
            'platform': 'aws',
            'error_message': 'some error',
            'use_cached': 'false',
        })

        assert resp.status_code == 400
        assert 'use_cached must be a boolean' in resp.get_json()['error']
