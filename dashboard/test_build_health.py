"""Tests for Build Health feature: WMCO operator version extraction and API.

Covers:
- Operator version extraction from build log text (positive and negative cases)
- Database storage and retrieval of operator_version
- /api/build-health endpoint response shape and logic
"""

import json
import os
import sqlite3
import tempfile

import pytest

from src.collectors.gcsweb import GCSWebCollector
from src.collectors.base import JobRun, TestStatus
from src.storage.database import DashboardDatabase
from src.web.server import create_app


@pytest.fixture
def collector():
    """Create a GCSWebCollector with minimal config."""
    return GCSWebCollector({'url': 'https://example.com', 'bucket': 'test'})


# ---------------------------------------------------------------------------
# Operator version extraction tests
# ---------------------------------------------------------------------------

class TestExtractOperatorVersion:
    """Tests for _extract_operator_version."""

    def test_json_style_version(self, collector):
        """Log containing '"version": "10.0.0-6dfe513"' is extracted."""
        text = 'some preamble\n"version": "10.0.0-6dfe513"\nmore text'
        assert collector._extract_operator_version(text) == '10.0.0-6dfe513'

    def test_prose_style_version(self, collector):
        """Log containing 'operator version 9.0.0-abc1234' is extracted."""
        text = 'Starting up...\noperator version 9.0.0-abc1234\nReady.'
        assert collector._extract_operator_version(text) == '9.0.0-abc1234'

    def test_prose_case_insensitive(self, collector):
        """Prose pattern works case-insensitively."""
        text = 'Operator Version 8.1.0-deadbeef'
        assert collector._extract_operator_version(text) == '8.1.0-deadbeef'

    def test_no_version_returns_none(self, collector):
        """Log with no WMCO version string returns None."""
        text = 'Just some random log output\nno version info here\n'
        assert collector._extract_operator_version(text) is None

    def test_ocp_version_not_matched(self, collector):
        """OCP version like '4.22' is NOT matched (negative case)."""
        text = 'OCP version 4.22 is running\ncluster ready'
        assert collector._extract_operator_version(text) is None

    def test_semver_without_hash_not_matched(self, collector):
        """Semver without commit hash like '10.0.0' is NOT matched."""
        text = '"version": "10.0.0"'
        assert collector._extract_operator_version(text) is None

    def test_non_hex_hash_not_matched(self, collector):
        """Version with non-hex hash like '10.0.0-xyz!!!' is NOT matched."""
        text = '"version": "10.0.0-xyz!!!"'
        assert collector._extract_operator_version(text) is None

    def test_json_style_with_spaces(self, collector):
        """JSON pattern with extra spaces around colon."""
        text = '"version" :  "7.5.2-aabbcc"'
        assert collector._extract_operator_version(text) == '7.5.2-aabbcc'

    def test_first_match_wins(self, collector):
        """When multiple versions appear, the first JSON match wins."""
        text = '"version": "10.0.0-aaa111"\noperator version 9.0.0-bbb222'
        assert collector._extract_operator_version(text) == '10.0.0-aaa111'

    def test_empty_string(self, collector):
        """Empty string returns None."""
        assert collector._extract_operator_version('') is None


# ---------------------------------------------------------------------------
# Database tests
# ---------------------------------------------------------------------------

class TestDatabaseOperatorVersion:
    """Tests for operator_version column in job_runs."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create a temporary database."""
        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)
        yield database
        database.close()

    def test_insert_and_query_operator_version(self, db):
        """operator_version is stored and retrievable."""
        from datetime import datetime

        run = JobRun(
            job_name='test-job',
            build_id='12345',
            status=TestStatus.PASSED,
            timestamp=datetime.now(),
            duration_seconds=100.0,
            version='4.22',
            platform='aws',
            total_tests=10,
            passed_tests=10,
            failed_tests=0,
            skipped_tests=0,
            job_url='https://example.com',
            job_type='periodic',
            operator_version='10.0.0-abc1234',
        )
        db.insert_job_runs([run])

        rows = db.execute_query(
            "SELECT operator_version FROM job_runs WHERE job_name = ?",
            ('test-job',)
        )
        assert len(rows) == 1
        assert rows[0]['operator_version'] == '10.0.0-abc1234'

    def test_insert_null_operator_version(self, db):
        """operator_version can be None (not all runs have it)."""
        from datetime import datetime

        run = JobRun(
            job_name='test-job-none',
            build_id='99999',
            status=TestStatus.FAILED,
            timestamp=datetime.now(),
            duration_seconds=50.0,
            version='4.21',
            platform='gcp',
            total_tests=5,
            passed_tests=3,
            failed_tests=2,
            skipped_tests=0,
        )
        db.insert_job_runs([run])

        rows = db.execute_query(
            "SELECT operator_version FROM job_runs WHERE job_name = ?",
            ('test-job-none',)
        )
        assert len(rows) == 1
        assert rows[0]['operator_version'] is None


# ---------------------------------------------------------------------------
# get_build_health tests
# ---------------------------------------------------------------------------

class TestGetBuildHealth:
    """Tests for DashboardDatabase.get_build_health."""

    @pytest.fixture
    def db_with_data(self, tmp_path):
        """Create a database pre-populated with job runs."""
        from datetime import datetime

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        runs = [
            JobRun(
                job_name='job-aws', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-aaa111',
            ),
            JobRun(
                job_name='job-gcp', build_id='2',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='gcp',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-aaa111',
            ),
            JobRun(
                job_name='job-azure', build_id='3',
                status=TestStatus.FAILED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='azure',
                total_tests=10, passed_tests=7, failed_tests=3,
                skipped_tests=0, operator_version='10.0.0-aaa111',
            ),
            # A run without operator_version -- should be excluded
            JobRun(
                job_name='job-no-ver', build_id='4',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0,
            ),
        ]
        database.insert_job_runs(runs)
        yield database
        database.close()

    def test_returns_grouped_by_platform(self, db_with_data):
        """Build health returns one row per platform."""
        rows = db_with_data.get_build_health(version='4.22', days=7)
        assert len(rows) == 3
        platforms = {r['platform'] for r in rows}
        assert platforms == {'aws', 'gcp', 'azure'}

    def test_excludes_null_operator_version(self, db_with_data):
        """Runs without operator_version are excluded."""
        rows = db_with_data.get_build_health(version='4.22', days=7)
        # job-no-ver (build_id 4) should not appear; aws should have 1 run
        aws_rows = [r for r in rows if r['platform'] == 'aws']
        assert len(aws_rows) == 1
        assert aws_rows[0]['total_runs'] == 1

    def test_passed_and_failed_counts(self, db_with_data):
        """Pass/fail counts are accurate per platform."""
        rows = db_with_data.get_build_health(version='4.22', days=7)
        azure = [r for r in rows if r['platform'] == 'azure'][0]
        assert azure['passed_runs'] == 0  # status was FAILED
        assert azure['failed_runs'] == 1


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestBuildHealthAPI:
    """Tests for /api/build-health endpoint."""

    @pytest.fixture
    def client(self, tmp_path):
        """Create a Flask test client with pre-populated data."""
        from datetime import datetime

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        runs = [
            JobRun(
                job_name='job-aws', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-abc1234',
            ),
            JobRun(
                job_name='job-gcp', build_id='2',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='gcp',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-abc1234',
            ),
        ]
        database.insert_job_runs(runs)

        # Write a minimal config.yaml for create_app
        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write('tracking:\n  versions: ["4.22"]\n  platforms: ["aws","gcp"]\n  blocklist: []\n')

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client

        database.close()

    def test_build_health_returns_latest_version(self, client):
        """Endpoint returns the latest operator version."""
        resp = client.get('/api/build-health?version=4.22&days=7')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['latest_version'] == '10.0.0-abc1234'

    def test_build_health_platforms_present(self, client):
        """Endpoint returns per-platform breakdown."""
        resp = client.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        assert 'aws' in data['platforms']
        assert 'gcp' in data['platforms']

    def test_build_health_releasable_when_all_pass(self, client):
        """releasable is True when all platforms pass."""
        resp = client.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        assert data['releasable'] is True

    def test_build_health_source_url(self, client):
        """source_url links to the WMCO commit on GitHub."""
        resp = client.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        assert 'source_url' in data
        assert 'abc1234' in data['source_url']

    def test_build_health_empty_database(self, tmp_path):
        """Returns empty result when no operator versions exist."""
        db_path = str(tmp_path / 'empty.db')
        database = DashboardDatabase(db_path)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write('tracking:\n  versions: []\n  platforms: []\n  blocklist: []\n')

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True

        with app.test_client() as client:
            resp = client.get('/api/build-health?days=7')
            data = resp.get_json()
            assert data['latest_version'] is None
            assert data['platforms'] == {}

        database.close()


class TestBuildHealthReleasability:
    """Tests for releasable flag logic."""

    @pytest.fixture
    def client_with_failure(self, tmp_path):
        """Create client with one failing platform."""
        from datetime import datetime

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        runs = [
            JobRun(
                job_name='job-aws', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-abc1234',
            ),
            JobRun(
                job_name='job-gcp', build_id='2',
                status=TestStatus.FAILED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='gcp',
                total_tests=10, passed_tests=7, failed_tests=3,
                skipped_tests=0, operator_version='10.0.0-abc1234',
            ),
        ]
        database.insert_job_runs(runs)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write('tracking:\n  versions: ["4.22"]\n  platforms: ["aws","gcp"]\n  blocklist: []\n')

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client

        database.close()

    def test_not_releasable_when_platform_fails(self, client_with_failure):
        """releasable is False when any platform has failures."""
        resp = client_with_failure.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        assert data['releasable'] is False

    def test_pass_rate_reflects_failures(self, client_with_failure):
        """Pass rate is less than 100 when failures exist."""
        resp = client_with_failure.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        assert data['pass_rate'] == 50.0  # 1 passed out of 2 runs
