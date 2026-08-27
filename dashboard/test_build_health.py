"""Tests for Build Health feature: WMCO operator version extraction and API.

Covers:
- Operator version extraction from build log text (positive and negative cases)
- Configurable extraction patterns via config
- Database storage and retrieval of operator_version
- /api/build-health endpoint response shape and logic
- Semantic version sorting
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


class TestConfigurablePatterns:
    """Tests for operator_version.patterns config."""

    def test_custom_pattern_used(self):
        """Custom pattern from config is used instead of defaults."""
        config = {
            'url': 'https://example.com',
            'bucket': 'test',
            'operator_version': {
                'patterns': [r'build:\s*v(\d+\.\d+\.\d+-[0-9a-f]+)'],
            },
        }
        c = GCSWebCollector(config)
        text = 'build: v5.0.0-cafe123'
        assert c._extract_operator_version(text) == '5.0.0-cafe123'

    def test_custom_pattern_no_false_positive(self):
        """Custom pattern does not match text suited to default patterns."""
        config = {
            'url': 'https://example.com',
            'bucket': 'test',
            'operator_version': {
                'patterns': [r'build:\s*v(\d+\.\d+\.\d+-[0-9a-f]+)'],
            },
        }
        c = GCSWebCollector(config)
        # Default JSON-style pattern should NOT match when config overrides
        text = '"version": "10.0.0-6dfe513"'
        assert c._extract_operator_version(text) is None

    def test_default_patterns_when_no_config(self):
        """Defaults are used when operator_version config is absent."""
        c = GCSWebCollector({'url': 'https://example.com', 'bucket': 'test'})
        text = '"version": "10.0.0-6dfe513"'
        assert c._extract_operator_version(text) == '10.0.0-6dfe513'


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

    def test_includes_ocp_version(self, db_with_data):
        """Each row includes the OCP version field."""
        rows = db_with_data.get_build_health(version='4.22', days=7)
        for r in rows:
            assert r['version'] == '4.22'

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

    def test_returns_first_seen_and_last_seen(self, db_with_data):
        """Each row includes first_seen and last_seen timestamps."""
        rows = db_with_data.get_build_health(version='4.22', days=7)
        for r in rows:
            assert 'first_seen' in r
            assert 'last_seen' in r
            assert r['first_seen'] is not None
            assert r['last_seen'] is not None

    def test_first_seen_last_seen_span(self, tmp_path):
        """first_seen/last_seen reflect MIN/MAX timestamps."""
        from datetime import datetime, timedelta

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        early = datetime.now() - timedelta(days=3)
        late = datetime.now()
        runs = [
            JobRun(
                job_name='job-aws-1', build_id='a1',
                status=TestStatus.PASSED, timestamp=early,
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-aaa111',
            ),
            JobRun(
                job_name='job-aws-2', build_id='a2',
                status=TestStatus.PASSED, timestamp=late,
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-aaa111',
            ),
        ]
        database.insert_job_runs(runs)
        rows = database.get_build_health(version='4.22', days=7)
        assert len(rows) == 1
        assert rows[0]['first_seen'] == early.isoformat()
        assert rows[0]['last_seen'] == late.isoformat()
        database.close()


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

    def test_build_health_returns_latest_per_ocp(self, client):
        """Endpoint returns one entry per OCP version with ocp_version field."""
        resp = client.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        assert isinstance(data['operator_versions'], list)
        assert len(data['operator_versions']) == 1
        entry = data['operator_versions'][0]
        assert entry['operator_version'] == '10.0.0-abc1234'
        assert entry['ocp_version'] == '4.22'

    def test_build_health_platforms_present(self, client):
        """Endpoint returns per-platform breakdown inside each version."""
        resp = client.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        version_data = data['operator_versions'][0]
        assert 'aws' in version_data['platforms']
        assert 'gcp' in version_data['platforms']

    def test_build_health_releasable_when_all_pass(self, client):
        """releasable is True when all platforms pass."""
        resp = client.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        assert data['operator_versions'][0]['releasable'] is True

    def test_build_health_source_url(self, client):
        """source_url links to the WMCO commit on GitHub."""
        resp = client.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        version_data = data['operator_versions'][0]
        assert 'source_url' in version_data
        assert 'abc1234' in version_data['source_url']

    def test_build_health_first_seen_last_seen(self, client):
        """API response includes first_seen and last_seen fields."""
        resp = client.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        version_data = data['operator_versions'][0]
        assert 'first_seen' in version_data
        assert 'last_seen' in version_data
        assert version_data['first_seen'] is not None
        assert version_data['last_seen'] is not None

    def test_build_health_source_url_from_config(self, tmp_path):
        """source_url uses the source_repo_url value from config."""
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
        ]
        database.insert_job_runs(runs)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write(
                'tracking:\n'
                '  versions: ["4.22"]\n'
                '  platforms: ["aws"]\n'
                '  blocklist: []\n'
                '  source_repo_url: "https://github.com/my-org/my-repo"\n'
            )

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as test_client:
            resp = test_client.get('/api/build-health?version=4.22&days=7')
            data = resp.get_json()
            version_data = data['operator_versions'][0]
            assert version_data['source_url'] == (
                'https://github.com/my-org/my-repo/commit/abc1234'
            )

        database.close()

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
            assert data['operator_versions'] == []

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
        assert data['operator_versions'][0]['releasable'] is False

    def test_pass_rate_reflects_failures(self, client_with_failure):
        """Pass rate is less than 100 when failures exist."""
        resp = client_with_failure.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        assert data['operator_versions'][0]['pass_rate'] == 50.0


class TestBuildHealthSourceUrlPlainSemver:
    """Tests for source_url with plain semver (no commit hash)."""

    @pytest.fixture
    def client_plain_semver(self, tmp_path):
        """Create client with plain semver operator version (no hash)."""
        from datetime import datetime

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        runs = [
            JobRun(
                job_name='job-aws', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.22.1',
            ),
        ]
        database.insert_job_runs(runs)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write('tracking:\n  versions: ["4.22"]\n  platforms: ["aws"]\n  blocklist: []\n')

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client

        database.close()

    def test_plain_semver_gets_no_source_url(self, client_plain_semver):
        """Plain semver version has no source_url key in the response."""
        resp = client_plain_semver.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        version_data = data['operator_versions'][0]
        assert 'source_url' not in version_data

    def test_plain_semver_source_url_absent(self, client_plain_semver):
        """Plain semver response omits source_url entirely."""
        resp = client_plain_semver.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        version_data = data['operator_versions'][0]
        assert 'source_url' not in version_data


class TestBuildHealthDateMerging:
    """Tests for first_seen/last_seen merging across platforms."""

    @pytest.fixture
    def client_multi_platform_dates(self, tmp_path):
        """Create client with runs on different platforms at different times."""
        from datetime import datetime, timedelta

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        early = datetime.now() - timedelta(days=5)
        mid = datetime.now() - timedelta(days=2)
        late = datetime.now()

        runs = [
            JobRun(
                job_name='job-aws', build_id='1',
                status=TestStatus.PASSED, timestamp=early,
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-abc1234',
            ),
            JobRun(
                job_name='job-gcp', build_id='2',
                status=TestStatus.PASSED, timestamp=mid,
                duration_seconds=100, version='4.22', platform='gcp',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-abc1234',
            ),
            JobRun(
                job_name='job-azure', build_id='3',
                status=TestStatus.PASSED, timestamp=late,
                duration_seconds=100, version='4.22', platform='azure',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-abc1234',
            ),
        ]
        database.insert_job_runs(runs)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write('tracking:\n  versions: ["4.22"]\n  platforms: ["aws","gcp","azure"]\n  blocklist: []\n')

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client, early, late

        database.close()

    def test_first_seen_is_earliest_across_platforms(self, client_multi_platform_dates):
        """first_seen is the minimum timestamp across all platforms."""
        client, early, late = client_multi_platform_dates
        resp = client.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        version_data = data['operator_versions'][0]
        assert version_data['first_seen'] == early.isoformat()

    def test_last_seen_is_latest_across_platforms(self, client_multi_platform_dates):
        """last_seen is the maximum timestamp across all platforms."""
        client, early, late = client_multi_platform_dates
        resp = client.get('/api/build-health?version=4.22&days=7')
        data = resp.get_json()
        version_data = data['operator_versions'][0]
        assert version_data['last_seen'] == late.isoformat()


class TestSemanticVersionSorting:
    """Tests for semantic version sorting in /api/build-health."""

    @pytest.fixture
    def client_multi_ocp(self, tmp_path):
        """Create client with multiple OCP versions each having different
        operator versions, including 9.x and 10.x."""
        from datetime import datetime

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        runs = [
            # OCP 4.21 has two operator versions; only 9.0.1 (latest) should be returned
            JobRun(
                job_name='job-aws-421-old', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='9.0.0-bbb222',
            ),
            JobRun(
                job_name='job-aws-421-new', build_id='2',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='9.0.1-ccc333',
            ),
            # OCP 4.22 has one operator version
            JobRun(
                job_name='job-aws-422', build_id='3',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.0.0-aaa111',
            ),
        ]
        database.insert_job_runs(runs)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write('tracking:\n  versions: ["4.21","4.22"]\n  platforms: ["aws"]\n  blocklist: []\n')

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client

        database.close()

    def test_10_sorts_above_9(self, client_multi_ocp):
        """10.0.0 is correctly sorted above 9.0.1 (not lexicographic)."""
        resp = client_multi_ocp.get('/api/build-health?days=7')
        data = resp.get_json()
        assert data['latest_version'] == '10.0.0-aaa111'
        versions = [v['operator_version'] for v in data['operator_versions']]
        assert versions == ['10.0.0-aaa111', '9.0.1-ccc333']

    def test_only_latest_per_ocp_returned(self, client_multi_ocp):
        """Only the latest operator version per OCP version is returned."""
        resp = client_multi_ocp.get('/api/build-health?days=7')
        data = resp.get_json()
        # Two OCP versions (4.21 and 4.22), one entry each
        assert len(data['operator_versions']) == 2
        ocp_versions = {v['ocp_version'] for v in data['operator_versions']}
        assert ocp_versions == {'4.21', '4.22'}
        # 4.21 should have 9.0.1 (not 9.0.0)
        v421 = [v for v in data['operator_versions'] if v['ocp_version'] == '4.21'][0]
        assert v421['operator_version'] == '9.0.1-ccc333'

    def test_ocp_version_field_present(self, client_multi_ocp):
        """Each entry has the ocp_version field."""
        resp = client_multi_ocp.get('/api/build-health?days=7')
        data = resp.get_json()
        for entry in data['operator_versions']:
            assert 'ocp_version' in entry


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------

class TestBuildHealthExcludedJobKeywords:
    """Tests for excluding disconnected/proxy jobs from build health."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create a temporary database."""
        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)
        yield database
        database.close()

    def test_excludes_disconnected_and_proxy_jobs(self, db):
        """Disconnected and proxy jobs are excluded from build health."""
        from datetime import datetime

        runs = [
            JobRun(
                job_name='periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-nightly-vsphere-ipi-ovn-zstream-f14-winc',
                build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='vsphere',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
            JobRun(
                job_name='periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-nightly-vsphere-ipi-disconnected-winc',
                build_id='2',
                status=TestStatus.FAILED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='vsphere',
                total_tests=10, passed_tests=5, failed_tests=5,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
            JobRun(
                job_name='periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-nightly-vsphere-ipi-proxy-winc',
                build_id='3',
                status=TestStatus.FAILED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='vsphere',
                total_tests=10, passed_tests=3, failed_tests=7,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
        ]
        db.insert_job_runs(runs)

        results = db.get_build_health(
            version='4.21', days=7,
            excluded_job_keywords=['disconnected', 'proxy'],
        )
        vsphere = [r for r in results if r['platform'] == 'vsphere']
        assert len(vsphere) == 1
        assert vsphere[0]['total_runs'] == 1
        assert vsphere[0]['passed_runs'] == 1
        assert vsphere[0]['failed_runs'] == 0

    def test_no_exclusion_when_keywords_empty(self, db):
        """All jobs are included when excluded_job_keywords is empty."""
        from datetime import datetime

        runs = [
            JobRun(
                job_name='periodic-ci-vsphere-ipi-disconnected-winc',
                build_id='10',
                status=TestStatus.FAILED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='vsphere',
                total_tests=10, passed_tests=5, failed_tests=5,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
        ]
        db.insert_job_runs(runs)

        results = db.get_build_health(
            version='4.21', days=7, excluded_job_keywords=[],
        )
        assert len(results) == 1
        assert results[0]['total_runs'] == 1

    def test_no_exclusion_when_keywords_none(self, db):
        """All jobs are included when excluded_job_keywords is None."""
        from datetime import datetime

        runs = [
            JobRun(
                job_name='periodic-ci-vsphere-ipi-proxy-winc',
                build_id='11',
                status=TestStatus.FAILED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='vsphere',
                total_tests=10, passed_tests=5, failed_tests=5,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
        ]
        db.insert_job_runs(runs)

        results = db.get_build_health(version='4.21', days=7)
        assert len(results) == 1
        assert results[0]['total_runs'] == 1

    def test_exclusion_is_case_insensitive(self, db):
        """Keywords match case-insensitively in job names."""
        from datetime import datetime

        runs = [
            JobRun(
                job_name='periodic-ci-VSPHERE-IPI-DISCONNECTED-winc',
                build_id='20',
                status=TestStatus.FAILED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='vsphere',
                total_tests=10, passed_tests=5, failed_tests=5,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
        ]
        db.insert_job_runs(runs)

        results = db.get_build_health(
            version='4.21', days=7,
            excluded_job_keywords=['disconnected'],
        )
        assert len(results) == 0

    def test_normal_jobs_not_excluded(self, db):
        """Normal jobs that don't match any keyword are kept."""
        from datetime import datetime

        runs = [
            JobRun(
                job_name='periodic-ci-vsphere-ipi-ovn-winc-f14',
                build_id='30',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='vsphere',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
            JobRun(
                job_name='periodic-ci-aws-ipi-ovn-winc-f7',
                build_id='31',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
        ]
        db.insert_job_runs(runs)

        results = db.get_build_health(
            version='4.21', days=7,
            excluded_job_keywords=['disconnected', 'proxy'],
        )
        assert len(results) == 2
        platforms = {r['platform'] for r in results}
        assert platforms == {'vsphere', 'aws'}


class TestBuildHealthExcludedJobKeywordsAPI:
    """Tests for excluded_job_keywords via the /api/build-health endpoint."""

    @pytest.fixture
    def client_with_excluded_jobs(self, tmp_path):
        """Create client with normal + disconnected + proxy job runs."""
        from datetime import datetime

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        runs = [
            JobRun(
                job_name='periodic-ci-vsphere-ipi-ovn-winc-f14',
                build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='vsphere',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
            JobRun(
                job_name='periodic-ci-vsphere-ipi-disconnected-winc',
                build_id='2',
                status=TestStatus.FAILED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='vsphere',
                total_tests=10, passed_tests=5, failed_tests=5,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
            JobRun(
                job_name='periodic-ci-vsphere-ipi-proxy-winc',
                build_id='3',
                status=TestStatus.FAILED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='vsphere',
                total_tests=10, passed_tests=3, failed_tests=7,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
        ]
        database.insert_job_runs(runs)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write(
                'tracking:\n'
                '  versions: ["4.21"]\n'
                '  platforms: ["vsphere"]\n'
                '  blocklist: []\n'
                'build_health:\n'
                '  excluded_job_keywords:\n'
                '    - "disconnected"\n'
                '    - "proxy"\n'
            )

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client

        database.close()

    def test_api_excludes_disconnected_proxy(self, client_with_excluded_jobs):
        """API response excludes disconnected/proxy jobs from counts."""
        resp = client_with_excluded_jobs.get(
            '/api/build-health?version=4.21&days=7'
        )
        assert resp.status_code == 200
        data = resp.get_json()
        version_data = data['operator_versions'][0]
        vsphere = version_data['platforms']['vsphere']
        assert vsphere['total_runs'] == 1
        assert vsphere['passed_runs'] == 1
        assert vsphere['failed_runs'] == 0

    def test_api_releasable_after_exclusion(self, client_with_excluded_jobs):
        """Build is releasable when only excluded jobs have failures."""
        resp = client_with_excluded_jobs.get(
            '/api/build-health?version=4.21&days=7'
        )
        data = resp.get_json()
        assert data['operator_versions'][0]['releasable'] is True


class TestExcludedKeywordsLoadedFromConfig:
    """Config-driven test: assert excluded_job_keywords from config.yaml."""

    def test_excluded_keywords_in_config(self):
        """Config file contains expected excluded_job_keywords."""
        import yaml
        import os

        config_path = os.path.join(
            os.path.dirname(__file__), 'config.yaml',
        )
        with open(config_path) as f:
            config = yaml.safe_load(f)

        keywords = config['build_health']['excluded_job_keywords']
        assert 'disconnected' in keywords
        assert 'proxy' in keywords


class TestKnownIssuesLoadedFromConfig:
    """Config-driven test: assert known_issues from config.yaml (rule 11)."""

    def test_known_issues_in_config(self):
        """Config file contains expected known_issues entries."""
        import yaml
        import os

        config_path = os.path.join(
            os.path.dirname(__file__), 'config.yaml',
        )
        with open(config_path) as f:
            config = yaml.safe_load(f)

        known_issues = config['build_health']['known_issues']
        assert isinstance(known_issues, list)
        assert len(known_issues) >= 2

        # Build lookup for assertion
        ki_map = {ki['test_id']: ki['bug'] for ki in known_issues}
        assert 'OCP-65980' in ki_map
        assert 'OCP-71173' in ki_map
        assert ki_map['OCP-65980'] == 'OCPBUGS-111093'
        assert ki_map['OCP-71173'] == 'OCPBUGS-111093'

    def test_known_issues_match_excluded_test_ids(self):
        """Every known_issues test_id is also in excluded_test_ids."""
        import yaml
        import os

        config_path = os.path.join(
            os.path.dirname(__file__), 'config.yaml',
        )
        with open(config_path) as f:
            config = yaml.safe_load(f)

        excluded = set(config['build_health']['excluded_test_ids'])
        known = {ki['test_id'] for ki in config['build_health']['known_issues']}
        assert known.issubset(excluded), (
            f"known_issues test_ids {known - excluded} not in excluded_test_ids"
        )


class TestGetBuildHealthKnownIssueTests:
    """Tests for known_issue_tests in get_build_health return value."""

    @pytest.fixture
    def db_with_known_issues(self, tmp_path):
        """Create a database with runs that fail due to excluded tests."""
        from datetime import datetime

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        runs = [
            # Passed run on AWS
            JobRun(
                job_name='job-aws', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
            # Failed run on GCP - fails due to excluded tests only
            JobRun(
                job_name='job-gcp', build_id='2',
                status=TestStatus.FAILED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='gcp',
                total_tests=10, passed_tests=8, failed_tests=2,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
        ]
        database.insert_job_runs(runs)

        # Insert test_results for the GCP failure (excluded tests)
        from collectors.base import TestResult
        test_results = [
            TestResult(
                test_name='OCP-65980:proxy-acceptance',
                test_description='Cluster-wide proxy acceptance',
                status=TestStatus.FAILED,
                timestamp=datetime.now(),
                duration_seconds=10.0,
                error_message='proxy error',
                job_name='job-gcp', build_id='2',
                version='4.21', platform='gcp',
            ),
            TestResult(
                test_name='OCP-71173:proxy-windows',
                test_description='Windows nodes behind proxy',
                status=TestStatus.FAILED,
                timestamp=datetime.now(),
                duration_seconds=10.0,
                error_message='proxy error',
                job_name='job-gcp', build_id='2',
                version='4.21', platform='gcp',
            ),
        ]
        database.insert_test_results(test_results)

        yield database
        database.close()

    def test_returns_known_issue_tests_when_excluded(self, db_with_known_issues):
        """get_build_health returns known_issue_tests for platforms
        with excluded test failures."""
        rows = db_with_known_issues.get_build_health(
            version='4.21', days=7,
            excluded_test_ids=['OCP-65980', 'OCP-71173'],
        )

        gcp = [r for r in rows if r['platform'] == 'gcp']
        assert len(gcp) == 1
        assert sorted(gcp[0]['known_issue_tests']) == ['OCP-65980', 'OCP-71173']
        # The run should be treated as passed
        assert gcp[0]['passed_runs'] == 1
        assert gcp[0]['failed_runs'] == 0

    def test_no_known_issue_tests_for_passing_platform(self, db_with_known_issues):
        """Platforms with only passed runs have empty known_issue_tests."""
        rows = db_with_known_issues.get_build_health(
            version='4.21', days=7,
            excluded_test_ids=['OCP-65980', 'OCP-71173'],
        )

        aws = [r for r in rows if r['platform'] == 'aws']
        assert len(aws) == 1
        assert aws[0]['known_issue_tests'] == []

    def test_no_known_issue_tests_without_exclusion(self, db_with_known_issues):
        """Without excluded_test_ids, known_issue_tests is empty."""
        rows = db_with_known_issues.get_build_health(
            version='4.21', days=7,
            excluded_test_ids=[],
        )

        for row in rows:
            assert row['known_issue_tests'] == []


class TestBuildHealthKnownIssuesAPI:
    """Tests for known_issues in /api/build-health response."""

    @pytest.fixture
    def client_with_known_issues(self, tmp_path):
        """Create a Flask test client with known-issue test failures."""
        from datetime import datetime
        from collectors.base import TestResult

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        runs = [
            JobRun(
                job_name='job-aws', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
            JobRun(
                job_name='job-gcp', build_id='2',
                status=TestStatus.FAILED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='gcp',
                total_tests=10, passed_tests=8, failed_tests=2,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
        ]
        database.insert_job_runs(runs)

        test_results = [
            TestResult(
                test_name='OCP-65980:proxy-acceptance',
                test_description='Cluster-wide proxy acceptance',
                status=TestStatus.FAILED,
                timestamp=datetime.now(),
                duration_seconds=10.0,
                error_message='proxy error',
                job_name='job-gcp', build_id='2',
                version='4.21', platform='gcp',
            ),
            TestResult(
                test_name='OCP-71173:proxy-windows',
                test_description='Windows nodes behind proxy',
                status=TestStatus.FAILED,
                timestamp=datetime.now(),
                duration_seconds=10.0,
                error_message='proxy error',
                job_name='job-gcp', build_id='2',
                version='4.21', platform='gcp',
            ),
        ]
        database.insert_test_results(test_results)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write(
                'tracking:\n'
                '  versions: ["4.21"]\n'
                '  platforms: ["aws","gcp"]\n'
                '  blocklist: []\n'
                'build_health:\n'
                '  excluded_test_ids:\n'
                '    - "OCP-65980"\n'
                '    - "OCP-71173"\n'
                '  known_issues:\n'
                '    - test_id: "OCP-65980"\n'
                '      bug: "OCPBUGS-111093"\n'
                '    - test_id: "OCP-71173"\n'
                '      bug: "OCPBUGS-111093"\n'
            )

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as client:
            yield client

        database.close()

    def test_api_returns_known_issues(self, client_with_known_issues):
        """API response includes known_issues with test_id and bug."""
        resp = client_with_known_issues.get(
            '/api/build-health?version=4.21&days=7'
        )
        assert resp.status_code == 200
        data = resp.get_json()
        version_data = data['operator_versions'][0]

        assert 'known_issues' in version_data
        ki = version_data['known_issues']
        assert len(ki) == 2

        ki_map = {entry['test_id']: entry['bug'] for entry in ki}
        assert ki_map['OCP-65980'] == 'OCPBUGS-111093'
        assert ki_map['OCP-71173'] == 'OCPBUGS-111093'

    def test_api_releasable_with_known_issues(self, client_with_known_issues):
        """Build is releasable when failures are all known issues."""
        resp = client_with_known_issues.get(
            '/api/build-health?version=4.21&days=7'
        )
        data = resp.get_json()
        assert data['operator_versions'][0]['releasable'] is True

    def test_api_platform_known_issues(self, client_with_known_issues):
        """Per-platform data includes known_issue_tests."""
        resp = client_with_known_issues.get(
            '/api/build-health?version=4.21&days=7'
        )
        data = resp.get_json()
        platforms = data['operator_versions'][0]['platforms']

        gcp = platforms['gcp']
        assert 'known_issue_tests' in gcp
        ki_ids = {ki['test_id'] for ki in gcp['known_issue_tests']}
        assert ki_ids == {'OCP-65980', 'OCP-71173'}

        # AWS had no failures, no known_issue_tests key
        aws = platforms['aws']
        assert 'known_issue_tests' not in aws

    def test_api_no_known_issues_when_config_empty(self, tmp_path):
        """No known_issues field when config has no known_issues."""
        from datetime import datetime

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)
        runs = [
            JobRun(
                job_name='job-aws', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
        ]
        database.insert_job_runs(runs)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write(
                'tracking:\n'
                '  versions: ["4.21"]\n'
                '  platforms: ["aws"]\n'
                '  blocklist: []\n'
            )

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as client:
            resp = client.get('/api/build-health?version=4.21&days=7')
            data = resp.get_json()
            version_data = data['operator_versions'][0]
            # No known issues should be present
            assert 'known_issues' not in version_data

        database.close()

    def test_api_mixed_known_and_unknown_failures(self, tmp_path):
        """Build is NOT releasable when both known and unknown failures exist."""
        from datetime import datetime
        from collectors.base import TestResult

        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        runs = [
            JobRun(
                job_name='job-gcp', build_id='1',
                status=TestStatus.FAILED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='gcp',
                total_tests=10, passed_tests=7, failed_tests=3,
                skipped_tests=0, operator_version='10.21.2-abc123',
            ),
        ]
        database.insert_job_runs(runs)

        test_results = [
            TestResult(
                test_name='OCP-65980:proxy-acceptance',
                test_description='Known issue test',
                status=TestStatus.FAILED,
                timestamp=datetime.now(),
                duration_seconds=10.0,
                error_message='proxy error',
                job_name='job-gcp', build_id='1',
                version='4.21', platform='gcp',
            ),
            TestResult(
                test_name='OCP-99999:unknown-failure',
                test_description='Unknown failure',
                status=TestStatus.FAILED,
                timestamp=datetime.now(),
                duration_seconds=10.0,
                error_message='real error',
                job_name='job-gcp', build_id='1',
                version='4.21', platform='gcp',
            ),
        ]
        database.insert_test_results(test_results)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write(
                'tracking:\n'
                '  versions: ["4.21"]\n'
                '  platforms: ["gcp"]\n'
                '  blocklist: []\n'
                'build_health:\n'
                '  excluded_test_ids:\n'
                '    - "OCP-65980"\n'
                '  known_issues:\n'
                '    - test_id: "OCP-65980"\n'
                '      bug: "OCPBUGS-111093"\n'
            )

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as client:
            resp = client.get('/api/build-health?version=4.21&days=7')
            data = resp.get_json()
            version_data = data['operator_versions'][0]
            # Not releasable because OCP-99999 is a real failure
            assert version_data['releasable'] is False
            # But known_issues still shows the known issue
            assert 'known_issues' in version_data
            ki_ids = {ki['test_id'] for ki in version_data['known_issues']}
            assert 'OCP-65980' in ki_ids

        database.close()


class TestBuildHealthErrorHandling:
    """Tests for /api/build-health error handling."""

    def test_build_health_returns_json_on_db_error(self, tmp_path, monkeypatch):
        """Verify /api/build-health returns JSON even when the DB query fails."""
        db_path = str(tmp_path / 'test.db')

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write('tracking:\n  versions: []\n  platforms: []\n  blocklist: []\n')

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True

        def broken_query(*args, **kwargs):
            raise Exception("simulated database error")

        monkeypatch.setattr(
            "storage.database.DashboardDatabase.get_build_health",
            broken_query,
        )

        with app.test_client() as client:
            resp = client.get('/api/build-health')
            assert resp.status_code == 500
            assert resp.content_type == 'application/json'
            data = resp.get_json()
            assert 'error' in data
            assert data['error'] == 'An internal error has occurred.'
