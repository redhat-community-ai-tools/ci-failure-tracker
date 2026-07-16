"""Tests for operator_version backfill feature.

Covers:
- Database methods for querying and updating NULL operator versions
- Background backfill function with mocked collector
- POST /api/backfill-versions endpoint triggering the backfill
- GET /api/backfill-versions endpoint returning status
- Edge cases: no NULL runs, failed extraction, already running
"""

import threading
import time
from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from src.collectors.base import JobRun, TestStatus
from src.storage.database import DashboardDatabase
from src.web.server import create_app, backfill_status


# ---------------------------------------------------------------------------
# Database method tests
# ---------------------------------------------------------------------------

class TestGetRunsWithoutOperatorVersion:
    """Tests for DashboardDatabase.get_runs_without_operator_version."""

    @pytest.fixture
    def db(self, tmp_path):
        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)
        yield database
        database.close()

    def test_returns_null_operator_version_runs(self, db):
        """Runs with NULL operator_version are returned."""
        runs = [
            JobRun(
                job_name='job-a', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version=None,
            ),
            JobRun(
                job_name='job-b', build_id='2',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='gcp',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-abc123',
            ),
        ]
        db.insert_job_runs(runs)

        result = db.get_runs_without_operator_version()
        assert len(result) == 1
        assert result[0]['job_name'] == 'job-a'
        assert result[0]['build_id'] == '1'

    def test_returns_empty_when_all_have_version(self, db):
        """Returns empty list when all runs have operator_version set."""
        runs = [
            JobRun(
                job_name='job-a', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-abc123',
            ),
        ]
        db.insert_job_runs(runs)

        result = db.get_runs_without_operator_version()
        assert result == []

    def test_returns_empty_on_empty_db(self, db):
        """Returns empty list when database has no job runs."""
        result = db.get_runs_without_operator_version()
        assert result == []


class TestUpdateOperatorVersion:
    """Tests for DashboardDatabase.update_operator_version."""

    @pytest.fixture
    def db(self, tmp_path):
        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)
        yield database
        database.close()

    def test_updates_matching_run(self, db):
        """operator_version is set on the matching row."""
        runs = [
            JobRun(
                job_name='job-a', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version=None,
            ),
        ]
        db.insert_job_runs(runs)

        updated = db.update_operator_version('job-a', '1', '10.0.0-abc123')
        assert updated == 1

        rows = db.execute_query(
            "SELECT operator_version FROM job_runs WHERE job_name = ? AND build_id = ?",
            ('job-a', '1'),
        )
        assert rows[0]['operator_version'] == '10.0.0-abc123'

    def test_returns_zero_for_nonexistent_run(self, db):
        """Returns 0 when no matching run exists."""
        updated = db.update_operator_version('no-job', '999', '1.0.0-aaa')
        assert updated == 0

    def test_does_not_affect_other_runs(self, db):
        """Only the targeted run is updated."""
        runs = [
            JobRun(
                job_name='job-a', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version=None,
            ),
            JobRun(
                job_name='job-b', build_id='2',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='gcp',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version=None,
            ),
        ]
        db.insert_job_runs(runs)

        db.update_operator_version('job-a', '1', '10.0.0-abc123')

        rows = db.execute_query(
            "SELECT operator_version FROM job_runs WHERE job_name = ? AND build_id = ?",
            ('job-b', '2'),
        )
        assert rows[0]['operator_version'] is None


# ---------------------------------------------------------------------------
# Backfill background function tests
# ---------------------------------------------------------------------------

class TestRunBackfillBackground:
    """Tests for run_backfill_background."""

    def test_backfill_updates_versions(self, tmp_path):
        """Backfill fetches logs and updates operator_version."""
        from src.web.server import run_backfill_background

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        runs = [
            JobRun(
                job_name='job-a', build_id='100',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version=None,
            ),
            JobRun(
                job_name='job-b', build_id='200',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='gcp',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version=None,
            ),
        ]
        database.insert_job_runs(runs)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write(
                'collector:\n'
                '  type: gcsweb\n'
                '  gcsweb:\n'
                '    url: https://example.com\n'
                '    bucket: test-bucket\n'
                'tracking:\n'
                '  versions: ["4.22"]\n'
                '  platforms: ["aws","gcp"]\n'
                '  blocklist: []\n'
            )

        def mock_fetch_build_log_text(run_path):
            if 'job-a' in run_path:
                return '"version": "10.0.0-aaa111"\nsome log output'
            if 'job-b' in run_path:
                return 'no version info here'
            return None

        with patch(
            'collectors.gcsweb.GCSWebCollector._fetch_build_log_text',
            side_effect=mock_fetch_build_log_text,
        ), patch('src.web.server.time.sleep'):
            run_backfill_background(db_path, config_file=config_path)

        # job-a should have version extracted
        rows = database.execute_query(
            "SELECT operator_version FROM job_runs WHERE job_name = ?",
            ('job-a',),
        )
        assert rows[0]['operator_version'] == '10.0.0-aaa111'

        # job-b had no version in log, should remain NULL
        rows = database.execute_query(
            "SELECT operator_version FROM job_runs WHERE job_name = ?",
            ('job-b',),
        )
        assert rows[0]['operator_version'] is None

        database.close()

    def test_backfill_no_runs_to_process(self, tmp_path):
        """Backfill completes immediately when no NULL runs exist."""
        from src.web.server import run_backfill_background

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        # Insert a run that already has operator_version
        runs = [
            JobRun(
                job_name='job-a', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-abc123',
            ),
        ]
        database.insert_job_runs(runs)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write(
                'collector:\n'
                '  type: gcsweb\n'
                '  gcsweb:\n'
                '    url: https://example.com\n'
                '    bucket: test-bucket\n'
                'tracking:\n'
                '  versions: ["4.22"]\n'
                '  platforms: ["aws"]\n'
                '  blocklist: []\n'
            )

        with patch('src.web.server.time.sleep'):
            run_backfill_background(db_path, config_file=config_path)

        assert backfill_status['progress'] == 'No runs to backfill'
        database.close()

    def test_backfill_skips_null_log(self, tmp_path):
        """Runs where build log fetch returns None are skipped."""
        from src.web.server import run_backfill_background

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        runs = [
            JobRun(
                job_name='job-missing', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version=None,
            ),
        ]
        database.insert_job_runs(runs)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write(
                'collector:\n'
                '  type: gcsweb\n'
                '  gcsweb:\n'
                '    url: https://example.com\n'
                '    bucket: test-bucket\n'
                'tracking:\n'
                '  versions: ["4.22"]\n'
                '  platforms: ["aws"]\n'
                '  blocklist: []\n'
            )

        with patch(
            'collectors.gcsweb.GCSWebCollector._fetch_build_log_text',
            return_value=None,
        ), patch('src.web.server.time.sleep'):
            run_backfill_background(db_path, config_file=config_path)

        rows = database.execute_query(
            "SELECT operator_version FROM job_runs WHERE job_name = ?",
            ('job-missing',),
        )
        assert rows[0]['operator_version'] is None
        database.close()


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestBackfillAPI:
    """Tests for /api/backfill-versions endpoint."""

    @pytest.fixture(autouse=True)
    def reset_backfill_status(self):
        """Reset global backfill_status before each test."""
        backfill_status['running'] = False
        backfill_status['progress'] = ''
        backfill_status['error'] = None
        backfill_status['completed_at'] = None
        backfill_status['total'] = 0
        backfill_status['processed'] = 0
        backfill_status['updated'] = 0
        yield

    @pytest.fixture
    def client(self, tmp_path):
        db_path = str(tmp_path / 'test.db')
        DashboardDatabase(db_path)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write(
                'collector:\n'
                '  type: gcsweb\n'
                '  gcsweb:\n'
                '    url: https://example.com\n'
                '    bucket: test-bucket\n'
                'tracking:\n'
                '  versions: ["4.22"]\n'
                '  platforms: ["aws"]\n'
                '  blocklist: []\n'
            )

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client

    def test_post_starts_backfill(self, client):
        """POST /api/backfill-versions starts the backfill."""
        with patch('src.web.server.run_backfill_background'):
            resp = client.post('/api/backfill-versions')
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['status'] == 'started'

    def test_post_returns_409_when_running(self, client):
        """POST returns 409 if backfill is already running."""
        backfill_status['running'] = True
        resp = client.post('/api/backfill-versions')
        assert resp.status_code == 409
        data = resp.get_json()
        assert 'already running' in data['error']

    def test_get_returns_status(self, client):
        """GET /api/backfill-versions returns current status."""
        backfill_status['running'] = False
        backfill_status['total'] = 100
        backfill_status['processed'] = 50
        backfill_status['updated'] = 30
        backfill_status['progress'] = 'Processed 50/100 runs, 30 updated'

        resp = client.get('/api/backfill-versions')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['running'] is False
        assert data['total'] == 100
        assert data['processed'] == 50
        assert data['updated'] == 30
        assert 'Processed' in data['progress']

    def test_get_returns_initial_status(self, client):
        """GET returns empty status before any backfill has run."""
        resp = client.get('/api/backfill-versions')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['running'] is False
        assert data['total'] == 0
        assert data['processed'] == 0
        assert data['updated'] == 0
