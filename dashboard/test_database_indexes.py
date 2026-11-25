"""Schema checks for indexes used by the collection hot path."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.storage.database import DashboardDatabase


def test_job_build_index_exists(tmp_path):
    """Collection test-count updates filter on (job_name, build_id)."""
    database = DashboardDatabase(str(tmp_path / "test.db"))

    indexes = {
        row[0]
        for row in database.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }

    assert "idx_test_results_job_build" in indexes


def test_idx_test_results_job_build_exists(tmp_path):
    """Verify the (job_name, build_id) compound index is created on init.

    This index is required for the correlated subqueries in server.py
    that update test counts in job_runs.  Without it, each of the ~1725
    job-run rows performs a full scan of ~30420 test-result rows,
    causing the save step to hang indefinitely.

    See: https://github.com/redhat-community-ai-tools/ci-failure-tracker/issues/275
    """
    db = DashboardDatabase(str(tmp_path / "test.db"))
    rows = db.conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND name='idx_test_results_job_build'"
    ).fetchall()
    assert len(rows) == 1, "idx_test_results_job_build index should exist after init"
    db.close()
