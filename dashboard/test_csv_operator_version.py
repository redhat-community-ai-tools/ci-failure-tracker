"""Tests for CSV operator version extraction and HTML detection in gcsweb.

Covers:
- HTML detection in _fetch_file (Content-Type and content sniffing)
- CSV operator version extraction from clusterserviceversions.json
- _extract_operator_version with plain semver (no hash suffix)
- _parse_operator_version_key with plain semver
- Backfill using CSV method
- _process_run_single_pass CSV-first fallback
"""

import json
from datetime import datetime
from unittest.mock import patch, MagicMock

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
# HTML detection in _fetch_file
# ---------------------------------------------------------------------------

class TestFetchFileHTMLDetection:
    """Tests for _fetch_file HTML response detection."""

    def test_returns_none_for_html_content_type(self, collector):
        """_fetch_file returns None when Content-Type is text/html."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'Content-Type': 'text/html; charset=utf-8'}
        mock_response.content = b'<html><body>File listing</body></html>'
        mock_response.raise_for_status = MagicMock()

        with patch.object(collector.session, 'get', return_value=mock_response):
            result = collector._fetch_file('/gcs/bucket/logs/job/1/build-log.txt')
            assert result is None

    def test_returns_none_for_doctype_prefix(self, collector):
        """_fetch_file returns None for content starting with <!doctype."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'Content-Type': 'application/octet-stream'}
        mock_response.content = b'<!DOCTYPE html><html><body>viewer</body></html>'
        mock_response.raise_for_status = MagicMock()

        with patch.object(collector.session, 'get', return_value=mock_response):
            result = collector._fetch_file('/gcs/bucket/path/file.txt')
            assert result is None

    def test_returns_none_for_html_tag_prefix(self, collector):
        """_fetch_file returns None for content starting with <html."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'Content-Type': 'application/octet-stream'}
        mock_response.content = b'<html><head><title>gcsweb</title></head></html>'
        mock_response.raise_for_status = MagicMock()

        with patch.object(collector.session, 'get', return_value=mock_response):
            result = collector._fetch_file('/gcs/bucket/path/file.txt')
            assert result is None

    def test_returns_content_for_valid_file(self, collector):
        """_fetch_file returns content for legitimate non-HTML files."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'Content-Type': 'text/plain'}
        mock_response.content = b'Starting operator version 10.0.0-abc123\nReady.'
        mock_response.raise_for_status = MagicMock()

        with patch.object(collector.session, 'get', return_value=mock_response):
            result = collector._fetch_file('/gcs/bucket/logs/job/1/build-log.txt')
            assert result == b'Starting operator version 10.0.0-abc123\nReady.'

    def test_returns_content_for_json_file(self, collector):
        """_fetch_file returns content for JSON files."""
        json_bytes = b'{"items": [{"metadata": {"name": "test"}}]}'
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'Content-Type': 'application/json'}
        mock_response.content = json_bytes
        mock_response.raise_for_status = MagicMock()

        with patch.object(collector.session, 'get', return_value=mock_response):
            result = collector._fetch_file('/gcs/bucket/path/data.json')
            assert result == json_bytes

    def test_returns_none_on_http_error(self, collector):
        """_fetch_file returns None on HTTP errors."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("404 Not Found")

        with patch.object(collector.session, 'get', return_value=mock_response):
            result = collector._fetch_file('/gcs/bucket/missing.txt')
            assert result is None

    def test_handles_missing_content_type(self, collector):
        """_fetch_file works when Content-Type header is missing."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.content = b'plain text log content'
        mock_response.raise_for_status = MagicMock()

        with patch.object(collector.session, 'get', return_value=mock_response):
            result = collector._fetch_file('/gcs/bucket/logs/file.txt')
            assert result == b'plain text log content'


# ---------------------------------------------------------------------------
# CSV operator version extraction
# ---------------------------------------------------------------------------

class TestFetchCsvOperatorVersion:
    """Tests for _fetch_csv_operator_version."""

    def _make_csv_json(self, version='10.22.1', name='windows-machine-config-operator.v10.22.1'):
        """Build a minimal clusterserviceversions.json payload."""
        return json.dumps({
            'items': [
                {
                    'metadata': {'name': 'other-operator.v1.0.0'},
                    'spec': {'version': '1.0.0'},
                },
                {
                    'metadata': {'name': name},
                    'spec': {'version': version},
                    'status': {'phase': 'Succeeded'},
                },
            ]
        }).encode()

    def test_extracts_version_from_csv(self, collector):
        """Version is extracted from clusterserviceversions.json."""
        csv_content = self._make_csv_json('10.22.1')

        def mock_list_directory(path):
            if path.endswith('/artifacts/'):
                return [
                    ('/gcs/test/logs/job/1/artifacts/chain-name/', 'chain-name/'),
                ]
            return []

        def mock_fetch_file(path):
            if 'clusterserviceversions.json' in path:
                return csv_content
            return None

        with patch.object(collector, '_list_directory', side_effect=mock_list_directory), \
             patch.object(collector, '_fetch_file', side_effect=mock_fetch_file):
            result = collector._fetch_csv_operator_version('/gcs/test/logs/job/1')
            assert result == '10.22.1'

    def test_skips_build_resources_dir(self, collector):
        """build-resources/ directory is skipped."""
        csv_content = self._make_csv_json('10.22.1')

        def mock_list_directory(path):
            if path.endswith('/artifacts/'):
                return [
                    ('/gcs/test/logs/job/1/artifacts/build-resources/', 'build-resources/'),
                    ('/gcs/test/logs/job/1/artifacts/chain-name/', 'chain-name/'),
                ]
            return []

        calls = []

        def mock_fetch_file(path):
            calls.append(path)
            if 'chain-name' in path and 'clusterserviceversions.json' in path:
                return csv_content
            return None

        with patch.object(collector, '_list_directory', side_effect=mock_list_directory), \
             patch.object(collector, '_fetch_file', side_effect=mock_fetch_file):
            result = collector._fetch_csv_operator_version('/gcs/test/logs/job/1')
            assert result == '10.22.1'
            # Verify build-resources was not fetched
            assert not any('build-resources' in c for c in calls)

    def test_skips_release_dir(self, collector):
        """release/ directory is skipped."""
        csv_content = self._make_csv_json('10.20.2')

        def mock_list_directory(path):
            if path.endswith('/artifacts/'):
                return [
                    ('/gcs/test/logs/job/1/artifacts/release/', 'release/'),
                    ('/gcs/test/logs/job/1/artifacts/step-dir/', 'step-dir/'),
                ]
            return []

        calls = []

        def mock_fetch_file(path):
            calls.append(path)
            if 'step-dir' in path and 'clusterserviceversions.json' in path:
                return csv_content
            return None

        with patch.object(collector, '_list_directory', side_effect=mock_list_directory), \
             patch.object(collector, '_fetch_file', side_effect=mock_fetch_file):
            result = collector._fetch_csv_operator_version('/gcs/test/logs/job/1')
            assert result == '10.20.2'
            assert not any('release' in c for c in calls)

    def test_returns_none_when_csv_missing(self, collector):
        """Returns None when clusterserviceversions.json is not found."""
        def mock_list_directory(path):
            if path.endswith('/artifacts/'):
                return [
                    ('/gcs/test/logs/job/1/artifacts/chain-name/', 'chain-name/'),
                ]
            return []

        with patch.object(collector, '_list_directory', side_effect=mock_list_directory), \
             patch.object(collector, '_fetch_file', return_value=None):
            result = collector._fetch_csv_operator_version('/gcs/test/logs/job/1')
            assert result is None

    def test_returns_none_when_no_wmco_csv(self, collector):
        """Returns None when CSV exists but has no WMCO entry."""
        csv_content = json.dumps({
            'items': [
                {
                    'metadata': {'name': 'some-other-operator.v1.0.0'},
                    'spec': {'version': '1.0.0'},
                },
            ]
        }).encode()

        def mock_list_directory(path):
            if path.endswith('/artifacts/'):
                return [
                    ('/gcs/test/logs/job/1/artifacts/chain/', 'chain/'),
                ]
            return []

        def mock_fetch_file(path):
            if 'clusterserviceversions.json' in path:
                return csv_content
            return None

        with patch.object(collector, '_list_directory', side_effect=mock_list_directory), \
             patch.object(collector, '_fetch_file', side_effect=mock_fetch_file):
            result = collector._fetch_csv_operator_version('/gcs/test/logs/job/1')
            assert result is None

    def test_returns_none_on_invalid_json(self, collector):
        """Returns None when CSV file contains invalid JSON."""
        def mock_list_directory(path):
            if path.endswith('/artifacts/'):
                return [
                    ('/gcs/test/logs/job/1/artifacts/chain/', 'chain/'),
                ]
            return []

        def mock_fetch_file(path):
            if 'clusterserviceversions.json' in path:
                return b'not valid json{{'
            return None

        with patch.object(collector, '_list_directory', side_effect=mock_list_directory), \
             patch.object(collector, '_fetch_file', side_effect=mock_fetch_file):
            result = collector._fetch_csv_operator_version('/gcs/test/logs/job/1')
            assert result is None

    def test_returns_none_when_no_artifacts(self, collector):
        """Returns None when artifacts directory is empty."""
        with patch.object(collector, '_list_directory', return_value=[]):
            result = collector._fetch_csv_operator_version('/gcs/test/logs/job/1')
            assert result is None

    def test_handles_single_csv_object(self, collector):
        """Handles CSV file containing a single object (not a list)."""
        csv_content = json.dumps({
            'metadata': {'name': 'windows-machine-config-operator.v10.19.3'},
            'spec': {'version': '10.19.3'},
        }).encode()

        def mock_list_directory(path):
            if path.endswith('/artifacts/'):
                return [
                    ('/gcs/test/logs/job/1/artifacts/chain/', 'chain/'),
                ]
            return []

        def mock_fetch_file(path):
            if 'clusterserviceversions.json' in path:
                return csv_content
            return None

        with patch.object(collector, '_list_directory', side_effect=mock_list_directory), \
             patch.object(collector, '_fetch_file', side_effect=mock_fetch_file):
            result = collector._fetch_csv_operator_version('/gcs/test/logs/job/1')
            assert result == '10.19.3'


# ---------------------------------------------------------------------------
# _extract_operator_version with plain semver
# ---------------------------------------------------------------------------

class TestExtractOperatorVersionPlainSemver:
    """Tests for _extract_operator_version handling X.Y.Z without hash."""

    def test_json_style_plain_semver(self, collector):
        """Matches '"version": "10.22.1"' without hash suffix."""
        text = '{"spec": {"version": "10.22.1"}}'
        assert collector._extract_operator_version(text) == '10.22.1'

    def test_prose_style_plain_semver(self, collector):
        """Matches 'operator version 10.22.1' without hash suffix."""
        text = 'Starting operator version 10.22.1\nReady.'
        assert collector._extract_operator_version(text) == '10.22.1'

    def test_hash_version_preferred_over_plain(self, collector):
        """Hash-suffixed version is matched first (higher priority)."""
        text = '"version": "10.0.0-6dfe513"\n"version": "10.22.1"'
        assert collector._extract_operator_version(text) == '10.0.0-6dfe513'

    def test_ocp_version_still_not_matched(self, collector):
        """OCP version like '4.22' is still NOT matched (two-part)."""
        text = 'OCP version 4.22 is running'
        assert collector._extract_operator_version(text) is None


# ---------------------------------------------------------------------------
# _parse_operator_version_key with plain semver
# ---------------------------------------------------------------------------

class TestParseOperatorVersionKey:
    """Tests for _parse_operator_version_key in server.py."""

    def _get_parse_fn(self, tmp_path):
        """Get the _parse_operator_version_key function from a Flask app."""
        db_path = str(tmp_path / 'test.db')
        DashboardDatabase(db_path)
        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write('tracking:\n  versions: []\n  platforms: []\n  blocklist: []\n')

        # Import and access the nested function through the module
        from src.web import server
        # The function is defined inside create_app, so we need to
        # test it indirectly via the build-health API.  Instead, test
        # the sorting behavior through the endpoint.
        return None  # We test via API below

    def test_plain_semver_sorts_correctly(self, tmp_path):
        """Versions like '10.22.1' sort above '9.0.1-ccc'."""
        db_path = str(tmp_path / 'test.db')
        database = DashboardDatabase(db_path)

        runs = [
            JobRun(
                job_name='job-old', build_id='1',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.21', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='9.0.1-ccc333',
            ),
            JobRun(
                job_name='job-new', build_id='2',
                status=TestStatus.PASSED, timestamp=datetime.now(),
                duration_seconds=100, version='4.22', platform='aws',
                total_tests=10, passed_tests=10, failed_tests=0,
                skipped_tests=0, operator_version='10.22.1',
            ),
        ]
        database.insert_job_runs(runs)

        config_path = str(tmp_path / 'config.yaml')
        with open(config_path, 'w') as f:
            f.write('tracking:\n  versions: ["4.21","4.22"]\n  platforms: ["aws"]\n  blocklist: []\n')

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as client:
            resp = client.get('/api/build-health?days=7')
            data = resp.get_json()
            assert data['latest_version'] == '10.22.1'
            versions = [v['operator_version'] for v in data['operator_versions']]
            assert versions == ['10.22.1', '9.0.1-ccc333']

        database.close()

    def test_plain_semver_source_url_uses_tag(self, tmp_path):
        """Plain semver versions get a releases/tag URL instead of commit."""
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
            f.write(
                'tracking:\n'
                '  versions: ["4.22"]\n'
                '  platforms: ["aws"]\n'
                '  blocklist: []\n'
                '  source_repo_url: "https://github.com/openshift/wmco"\n'
            )

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as client:
            resp = client.get('/api/build-health?version=4.22&days=7')
            data = resp.get_json()
            entry = data['operator_versions'][0]
            assert entry['source_url'] == 'https://github.com/openshift/wmco/releases/tag/v10.22.1'

        database.close()

    def test_hash_version_source_url_uses_commit(self, tmp_path):
        """Hash-suffixed versions still get a commit URL."""
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
                '  source_repo_url: "https://github.com/openshift/wmco"\n'
            )

        app = create_app(db_path, config_file=config_path)
        app.config['TESTING'] = True
        with app.test_client() as client:
            resp = client.get('/api/build-health?version=4.22&days=7')
            data = resp.get_json()
            entry = data['operator_versions'][0]
            assert entry['source_url'] == 'https://github.com/openshift/wmco/commit/abc1234'

        database.close()


# ---------------------------------------------------------------------------
# Backfill with CSV method
# ---------------------------------------------------------------------------

class TestBackfillWithCsv:
    """Tests for run_backfill_background using CSV extraction."""

    def test_backfill_uses_csv_first(self, tmp_path):
        """Backfill tries CSV extraction before build log."""
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

        build_log_called = []

        def mock_csv(run_path):
            return '10.22.1'

        def mock_build_log(run_path):
            build_log_called.append(run_path)
            return None

        with patch(
            'collectors.gcsweb.GCSWebCollector._fetch_csv_operator_version',
            side_effect=mock_csv,
        ), patch(
            'collectors.gcsweb.GCSWebCollector._fetch_build_log_text',
            side_effect=mock_build_log,
        ), patch('src.web.server.time.sleep'):
            run_backfill_background(db_path, config_file=config_path)

        # CSV method returned a version, so build log should NOT be called
        assert build_log_called == []

        rows = database.execute_query(
            "SELECT operator_version FROM job_runs WHERE job_name = ?",
            ('job-a',),
        )
        assert rows[0]['operator_version'] == '10.22.1'
        database.close()

    def test_backfill_falls_back_to_build_log(self, tmp_path):
        """Backfill falls back to build log when CSV returns None."""
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
            'collectors.gcsweb.GCSWebCollector._fetch_csv_operator_version',
            return_value=None,
        ), patch(
            'collectors.gcsweb.GCSWebCollector._fetch_build_log_text',
            return_value='"version": "10.0.0-aaa111"\nlog output',
        ), patch('src.web.server.time.sleep'):
            run_backfill_background(db_path, config_file=config_path)

        rows = database.execute_query(
            "SELECT operator_version FROM job_runs WHERE job_name = ?",
            ('job-a',),
        )
        assert rows[0]['operator_version'] == '10.0.0-aaa111'
        database.close()


# ---------------------------------------------------------------------------
# _process_run_single_pass CSV-first fallback
# ---------------------------------------------------------------------------

class TestProcessRunCsvFallback:
    """Tests for _process_run_single_pass CSV-first extraction."""

    def test_csv_version_used_when_available(self, collector):
        """CSV version is used and build log is not fetched."""
        run = {
            'job_name': 'periodic-ci-test-release-4.22-aws-winc-f7',
            'build_id': '123',
            'path': '/gcs/test/logs/job/123',
            'timestamp': None,
        }

        finished = {'timestamp': 1720000000, 'result': 'SUCCESS', 'duration': 3600}
        build_log_calls = []

        def mock_build_log(path):
            build_log_calls.append(path)
            return None

        with patch.object(collector, '_fetch_finished_json', return_value=finished), \
             patch.object(collector, '_fetch_junit_xml_files', return_value=[]), \
             patch.object(collector, '_fetch_csv_operator_version', return_value='10.22.1'), \
             patch.object(collector, '_fetch_build_log_text', side_effect=mock_build_log):
            result = collector._process_run_single_pass(run)
            assert result is not None
            job_run, _test_results = result
            assert job_run.operator_version == '10.22.1'
            assert build_log_calls == []

    def test_falls_back_to_build_log_when_no_csv(self, collector):
        """Falls back to build log extraction when CSV returns None."""
        run = {
            'job_name': 'periodic-ci-test-release-4.22-aws-winc-f7',
            'build_id': '123',
            'path': '/gcs/test/logs/job/123',
            'timestamp': None,
        }

        finished = {'timestamp': 1720000000, 'result': 'SUCCESS', 'duration': 3600}

        with patch.object(collector, '_fetch_finished_json', return_value=finished), \
             patch.object(collector, '_fetch_junit_xml_files', return_value=[]), \
             patch.object(collector, '_fetch_csv_operator_version', return_value=None), \
             patch.object(collector, '_fetch_build_log_text', return_value='"version": "10.0.0-abc"'):
            result = collector._process_run_single_pass(run)
            assert result is not None
            job_run, _test_results = result
            assert job_run.operator_version == '10.0.0-abc'

    def test_no_version_when_both_fail(self, collector):
        """operator_version is None when both CSV and build log fail."""
        run = {
            'job_name': 'periodic-ci-test-release-4.22-aws-winc-f7',
            'build_id': '123',
            'path': '/gcs/test/logs/job/123',
            'timestamp': None,
        }

        finished = {'timestamp': 1720000000, 'result': 'FAILURE', 'duration': 3600}

        with patch.object(collector, '_fetch_finished_json', return_value=finished), \
             patch.object(collector, '_fetch_junit_xml_files', return_value=[]), \
             patch.object(collector, '_fetch_csv_operator_version', return_value=None), \
             patch.object(collector, '_fetch_build_log_text', return_value=None):
            result = collector._process_run_single_pass(run)
            assert result is not None
            job_run, _test_results = result
            assert job_run.operator_version is None
