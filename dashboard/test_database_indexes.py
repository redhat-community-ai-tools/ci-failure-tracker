"""Schema checks for indexes used by the collection hot path."""

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
