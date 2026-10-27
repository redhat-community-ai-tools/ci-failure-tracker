"""Tests for analysis statistics aggregation by analysis_mode.

Validates that get_analysis_stats counts GLM ('glm-ai') analyses in
api_count, alongside legacy mode strings ('anthropic-api',
'vertex-ai') from historical rows.
"""

import pytest

from src.storage.database import DashboardDatabase


@pytest.fixture
def db(tmp_path):
    """Create a temporary database for testing."""
    database = DashboardDatabase(str(tmp_path / "test.db"))
    yield database
    database.close()


def _save_one(db, test_name, mode):
    db.save_ai_analysis(test_name, '4.22', analysis={
        'root_cause': 'x',
        'component': 'WMCO',
        'confidence': 80,
        'failure_type': 'to_investigate',
        'analysis_mode': mode,
        'cost': 0.0,
    })


class TestAnalysisStats:
    """Tests for get_analysis_stats mode counting."""

    def test_glm_ai_counted_in_api_count(self, db):
        """Current GLM mode must be counted by api_count."""
        _save_one(db, 'OCP-11111', 'glm-ai')
        stats = db.get_analysis_stats()
        assert stats['api_count'] == 1
        assert stats['total_analyses'] == 1

    def test_legacy_modes_still_counted(self, db):
        """Historical mode strings must remain visible in statistics."""
        _save_one(db, 'OCP-22222', 'anthropic-api')
        _save_one(db, 'OCP-33333', 'vertex-ai')
        stats = db.get_analysis_stats()
        assert stats['api_count'] == 2

    def test_pre_classifier_rows_not_counted_as_api(self, db):
        """Free pre-classifier rows must stay out of api_count."""
        _save_one(db, 'OCP-44444', 'pre-classifier')
        _save_one(db, 'OCP-55555', 'glm-ai')
        stats = db.get_analysis_stats()
        assert stats['api_count'] == 1
        assert stats['total_analyses'] == 2

    def test_savings_reflects_zero_cost_model(self, db):
        """Savings must be zero because GLM-5.3 via enMaas is free."""
        _save_one(db, 'OCP-66666', 'local-claude-code')
        _save_one(db, 'OCP-77777', 'local-claude-code')
        stats = db.get_analysis_stats()
        assert stats['local_count'] == 2
        assert stats['savings'] == 0.0

    def test_savings_zero_when_no_local_analyses(self, db):
        """Savings must be zero when there are no local analyses."""
        _save_one(db, 'OCP-88888', 'glm-ai')
        stats = db.get_analysis_stats()
        assert stats['local_count'] == 0
        assert stats['savings'] == 0.0
