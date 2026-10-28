"""Tests for database index creation.

Verifies that performance-critical indexes are created during
database initialization.
"""

import pytest

from src.storage.database import DashboardDatabase


@pytest.fixture
def db(tmp_path):
    """Create a temporary database for testing."""
    db_path = str(tmp_path / "test.db")
    database = DashboardDatabase(db_path)
    yield database
    database.close()


def test_idx_test_results_job_build_exists(db):
    """Verify the (job_name, build_id) compound index is created on init.

    This index is required for the correlated subqueries in server.py
    that update test counts in job_runs.  Without it, each of the ~1725
    job-run rows performs a full scan of ~30420 test-result rows,
    causing the save step to hang indefinitely.

    See: https://github.com/redhat-community-ai-tools/ci-failure-tracker/issues/275
    """
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name='idx_test_results_job_build'"
    )
    rows = cursor.fetchall()
    assert len(rows) == 1, "idx_test_results_job_build index should exist after init"
