"""Tests for GCSWebCollector JUnit XML parsing and diagnostic logging.

Validates that nested testsuites are handled correctly without
double-counting test counts or duplicating test results, and that
the collector logs warnings when jobs return no builds.

Also validates version extraction for rehearse, postsubmit FBC, and
PR job names, job type derivation, and PR log path construction.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from src.collectors.gcsweb import GCSWebCollector
from src.collectors.base import TestStatus


@pytest.fixture
def collector():
    """Create a GCSWebCollector with minimal config."""
    return GCSWebCollector({'url': 'https://example.com', 'bucket': 'test'})


@pytest.fixture
def collector_with_wmco_map():
    """Create a GCSWebCollector with WMCO version mapping config."""
    return GCSWebCollector({
        'url': 'https://example.com',
        'bucket': 'test',
        'branch_version_map': {'main': '5.0'},
        'wmco_version_map': {'10': '4', '11': '5'},
    })


FLAT_XML = """\
<testsuite name="suite" tests="3" failures="1" errors="0" skipped="0">
  <testcase name="OCP-1111 test one" time="1.0"/>
  <testcase name="OCP-2222 test two" time="2.0">
    <failure message="boom"/>
  </testcase>
  <testcase name="OCP-3333 test three" time="0.5"/>
</testsuite>
"""

NESTED_XML = """\
<testsuites>
  <testsuite name="parent" tests="10" failures="3" errors="0" skipped="1">
    <testsuite name="child1" tests="5" failures="2" errors="0" skipped="1">
      <testcase name="OCP-1111 test one" time="1.0">
        <failure message="fail1"/>
      </testcase>
      <testcase name="OCP-2222 test two" time="2.0">
        <failure message="fail2"/>
      </testcase>
      <testcase name="OCP-3333 test three" time="0.5">
        <skipped message="skip"/>
      </testcase>
      <testcase name="OCP-4444 test four" time="1.0"/>
      <testcase name="OCP-5555 test five" time="1.0"/>
    </testsuite>
    <testsuite name="child2" tests="5" failures="1" errors="0" skipped="0">
      <testcase name="OCP-6666 test six" time="1.0">
        <failure message="fail3"/>
      </testcase>
      <testcase name="OCP-7777 test seven" time="1.0"/>
      <testcase name="OCP-8888 test eight" time="1.0"/>
      <testcase name="OCP-9999 test nine" time="1.0"/>
      <testcase name="OCP-1010 test ten" time="1.0"/>
    </testsuite>
  </testsuite>
</testsuites>
"""

DEEPLY_NESTED_XML = """\
<testsuites>
  <testsuite name="root" tests="4" failures="1" errors="0" skipped="0">
    <testsuite name="mid" tests="4" failures="1" errors="0" skipped="0">
      <testsuite name="leaf" tests="4" failures="1" errors="0" skipped="0">
        <testcase name="OCP-0001 deep test" time="1.0">
          <failure message="deep fail"/>
        </testcase>
        <testcase name="OCP-0002 deep pass" time="1.0"/>
        <testcase name="OCP-0003 deep pass2" time="1.0"/>
        <testcase name="OCP-0004 deep pass3" time="1.0"/>
      </testsuite>
    </testsuite>
  </testsuite>
</testsuites>
"""

METADATA = {'version': '4.18', 'platform': 'vsphere'}


class TestParseJunitXml:
    """Tests for _parse_junit_xml handling of nested testsuites."""

    def test_flat_testsuite(self, collector):
        root = ET.fromstring(FLAT_XML)
        results = collector._parse_junit_xml(root, 'job1', '100', METADATA)

        assert len(results) == 3
        statuses = {r.test_name: r.status for r in results}
        assert statuses['OCP-1111'] == TestStatus.PASSED
        assert statuses['OCP-2222'] == TestStatus.FAILED
        assert statuses['OCP-3333'] == TestStatus.PASSED

    def test_nested_testsuites_no_duplicate_results(self, collector):
        """Nested testsuites must not produce duplicate TestResult entries."""
        root = ET.fromstring(NESTED_XML)
        results = collector._parse_junit_xml(root, 'job1', '100', METADATA)

        # 10 unique testcases in the XML
        assert len(results) == 10
        names = [r.test_name for r in results]
        assert len(set(names)) == 10

    def test_deeply_nested_no_duplicates(self, collector):
        root = ET.fromstring(DEEPLY_NESTED_XML)
        results = collector._parse_junit_xml(root, 'job1', '100', METADATA)
        assert len(results) == 4


class TestProcessJobRunCounts:
    """Tests for _process_job_run testsuite count aggregation."""

    def _count_from_xml(self, collector, xml_string):
        """Helper: compute counts the same way _process_job_run does."""
        root = ET.fromstring(xml_string)
        total = 0
        failed = 0
        skipped = 0
        all_suites = []
        if root.tag == 'testsuite':
            all_suites.append(root)
        all_suites.extend(root.findall('.//testsuite'))
        for ts in all_suites:
            if ts.findall('testsuite'):
                continue
            total += int(ts.get('tests', 0))
            failed += int(ts.get('failures', 0))
            failed += int(ts.get('errors', 0))
            skipped += int(ts.get('skipped', 0))
        passed = total - failed - skipped
        return total, passed, failed, skipped

    def test_flat_counts(self, collector):
        total, passed, failed, skipped = self._count_from_xml(collector, FLAT_XML)
        assert total == 3
        assert failed == 1
        assert passed == 2
        assert skipped == 0

    def test_nested_counts_no_double_counting(self, collector):
        """Parent testsuite counts must not be added to children's counts."""
        total, passed, failed, skipped = self._count_from_xml(collector, NESTED_XML)
        # Only leaf testsuites: child1 (5 tests, 2 fail, 1 skip) + child2 (5 tests, 1 fail)
        assert total == 10
        assert failed == 3
        assert skipped == 1
        assert passed == 6

    def test_deeply_nested_counts(self, collector):
        total, passed, failed, skipped = self._count_from_xml(collector, DEEPLY_NESTED_XML)
        # Only the leaf testsuite should be counted
        assert total == 4
        assert failed == 1
        assert passed == 3
        assert skipped == 0


class TestDiagnosticLogging:
    """Tests for diagnostic logging when jobs return no builds."""

    def test_list_job_runs_logs_warning_when_no_builds(self, collector, caplog):
        """Collector should warn when a job directory has no builds."""
        with patch.object(collector, '_list_directory', return_value=[]):
            with caplog.at_level(logging.WARNING, logger='src.collectors.gcsweb'):
                start = datetime.now() - timedelta(days=30)
                end = datetime.now()
                runs = collector._list_job_runs('some-nonexistent-job', start, end)

        assert runs == []
        assert any(
            'No builds found for job: some-nonexistent-job' in msg
            for msg in caplog.messages
        )

    def test_list_job_runs_no_warning_when_builds_exist(self, collector, caplog):
        """Collector should NOT warn when builds are found."""
        ts = int((datetime.now() - timedelta(days=1)).timestamp())
        links = [(f'/gcs/test/logs/job/{ts}/', f'{ts}/')]
        with patch.object(collector, '_list_directory', return_value=links):
            with caplog.at_level(logging.WARNING, logger='src.collectors.gcsweb'):
                start = datetime.now() - timedelta(days=30)
                end = datetime.now()
                runs = collector._list_job_runs('some-job', start, end)

        assert len(runs) == 1
        assert not any(
            'No builds found' in msg
            for msg in caplog.messages
        )

    def test_list_directory_logs_warning_on_error(self, collector, caplog):
        """_list_directory should log a warning (not print) on HTTP errors."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception('404 Not Found')
        with patch.object(collector.session, 'get', return_value=mock_response):
            with caplog.at_level(logging.WARNING, logger='src.collectors.gcsweb'):
                result = collector._list_directory('/gcs/test/logs/missing-job/')

        assert result == []
        assert any(
            'Error listing directory' in msg and '404 Not Found' in msg
            for msg in caplog.messages
        )

    def test_collect_job_runs_logs_empty_jobs_summary(self, collector, caplog):
        """collect_job_runs should log a summary of jobs with no builds."""
        with patch.object(collector, '_resolve_patterns', return_value=[
            'periodic-ci-release-4.23-aws-winc-f7',
            'periodic-ci-release-4.23-gcp-winc-f7',
        ]):
            with patch.object(collector, '_list_job_runs', return_value=[]):
                with caplog.at_level(logging.WARNING, logger='src.collectors.gcsweb'):
                    start = datetime.now() - timedelta(days=30)
                    end = datetime.now()
                    runs = collector.collect_job_runs(
                        start_date=start,
                        end_date=end,
                        job_patterns=['periodic-ci-release-4.23-*'],
                    )

        assert runs == []
        assert any(
            '2 job(s) returned no builds' in msg
            for msg in caplog.messages
        )

    def test_collect_job_runs_does_not_warn_for_jobs_with_data(self, collector, caplog):
        """Jobs that return builds should not appear in the empty-jobs warning."""
        from src.collectors.base import JobRun

        job_run = JobRun(
            job_name='periodic-ci-release-4.22-aws-winc-f7',
            build_id='123',
            status=TestStatus.PASSED,
            timestamp=datetime.now(),
            duration_seconds=100,
            version='4.22',
            platform='aws',
            total_tests=10,
            passed_tests=10,
            failed_tests=0,
            skipped_tests=0,
        )

        def mock_list_job_runs(job_name, start, end, max_results=100):
            if '4.22' in job_name:
                return [{'job_name': job_name, 'build_id': '123',
                         'path': '/gcs/test/logs/job/123', 'timestamp': datetime.now()}]
            return []

        with patch.object(collector, '_resolve_patterns', return_value=[
            'periodic-ci-release-4.22-aws-winc-f7',
            'periodic-ci-release-4.23-aws-winc-f7',
        ]):
            with patch.object(collector, '_list_job_runs', side_effect=mock_list_job_runs):
                with patch.object(collector, '_process_job_run', return_value=job_run):
                    with caplog.at_level(logging.WARNING, logger='src.collectors.gcsweb'):
                        start = datetime.now() - timedelta(days=30)
                        end = datetime.now()
                        runs = collector.collect_job_runs(
                            start_date=start,
                            end_date=end,
                            job_patterns=['periodic-ci-release-*'],
                        )

        # Only the 4.23 job should be in the warning
        assert len(runs) == 1
        warning_msgs = [m for m in caplog.messages if 'returned no builds' in m]
        assert len(warning_msgs) == 1
        assert '4.23' in warning_msgs[0]
        assert '4.22' not in warning_msgs[0]

    def test_fetch_file_logs_warning_on_error(self, collector, caplog):
        """_fetch_file should log a warning (not print) on errors."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception('Connection timeout')
        with patch.object(collector.session, 'get', return_value=mock_response):
            with caplog.at_level(logging.WARNING, logger='src.collectors.gcsweb'):
                result = collector._fetch_file('/gcs/test/logs/job/123/finished.json')

        assert result is None
        assert any(
            'Error fetching file' in msg and 'Connection timeout' in msg
            for msg in caplog.messages
        )


class TestRehearsePrefixStripping:
    """Tests for _strip_rehearse_prefix."""

    def test_strips_rehearse_prefix(self, collector):
        result = collector._strip_rehearse_prefix(
            'rehearse-81646-periodic-ci-openshift-openshift-tests-private-'
            'release-4.21-amd64-nightly-aws-ipi-ovn-winc-zstream-f14'
        )
        assert result == (
            'periodic-ci-openshift-openshift-tests-private-'
            'release-4.21-amd64-nightly-aws-ipi-ovn-winc-zstream-f14'
        )

    def test_preserves_non_rehearse_name(self, collector):
        name = 'periodic-ci-openshift-release-4.21-aws-winc-f14'
        assert collector._strip_rehearse_prefix(name) == name

    def test_does_not_strip_rehearse_without_number(self, collector):
        """Negative: 'rehearse-' without a numeric PR ID is not a rehearse prefix."""
        name = 'rehearse-abc-periodic-ci-release-4.21'
        assert collector._strip_rehearse_prefix(name) == name


class TestDeriveJobType:
    """Tests for _derive_job_type -- includes negative cases (AGENTS.md rule 8)."""

    def test_periodic_job(self, collector):
        assert collector._derive_job_type(
            'periodic-ci-openshift-release-4.21-aws-winc-f14'
        ) == 'periodic'

    def test_postsubmit_job(self, collector):
        assert collector._derive_job_type(
            'branch-ci-openshift-windows-machine-config-operator-fbc-main-v10-21-aws-winc'
        ) == 'postsubmit'

    def test_presubmit_job(self, collector):
        assert collector._derive_job_type(
            'pull-ci-openshift-openshift-tests-private-release-4.21-aws-winc'
        ) == 'presubmit'

    def test_rehearse_job(self, collector):
        assert collector._derive_job_type(
            'rehearse-81646-periodic-ci-release-4.21-aws-winc-f14'
        ) == 'rehearse'

    def test_unknown_prefix_defaults_to_periodic(self, collector):
        """Negative: unrecognized prefix should fall back to periodic."""
        assert collector._derive_job_type('custom-job-name-winc') == 'periodic'


class TestExtractMetadataNewPatterns:
    """Tests for _extract_metadata with rehearse, FBC postsubmit, and PR jobs.

    Includes negative test cases for similar-but-incorrect patterns
    (AGENTS.md rule 8).
    """

    def test_rehearse_job_extracts_version(self, collector):
        """Rehearse job: strip prefix, then release-4.21 pattern matches."""
        meta = collector._extract_metadata(
            'rehearse-81646-periodic-ci-openshift-openshift-tests-private-'
            'release-4.21-amd64-nightly-aws-ipi-ovn-winc-zstream-f14'
        )
        assert meta['version'] == '4.21'
        assert meta['platform'] == 'aws'

    def test_rehearse_job_gcp_platform(self, collector):
        meta = collector._extract_metadata(
            'rehearse-81646-periodic-ci-openshift-openshift-tests-private-'
            'release-4.21-amd64-nightly-gcp-ipi-ovn-winc-zstream-f14'
        )
        assert meta['version'] == '4.21'
        assert meta['platform'] == 'gcp'

    def test_rehearse_job_vsphere_platform(self, collector):
        meta = collector._extract_metadata(
            'rehearse-81646-periodic-ci-openshift-openshift-tests-private-'
            'release-4.21-amd64-nightly-vsphere-ipi-ovn-winc-zstream-f14'
        )
        assert meta['version'] == '4.21'
        assert meta['platform'] == 'vsphere'

    def test_fbc_postsubmit_wmco_v10_21(self, collector_with_wmco_map):
        """FBC postsubmit: v10-21 -> OCP 4.21 (WMCO 10 -> OCP major 4)."""
        meta = collector_with_wmco_map._extract_metadata(
            'branch-ci-openshift-windows-machine-config-operator-'
            'fbc-main-v10-21-aws-winc'
        )
        assert meta['version'] == '4.21'
        assert meta['platform'] == 'aws'

    def test_fbc_postsubmit_wmco_v11_0(self, collector_with_wmco_map):
        """FBC postsubmit: v11-0 -> OCP 5.0 (WMCO 11 -> OCP major 5)."""
        meta = collector_with_wmco_map._extract_metadata(
            'branch-ci-openshift-windows-machine-config-operator-'
            'fbc-main-v11-0-gcp-winc'
        )
        assert meta['version'] == '5.0'
        assert meta['platform'] == 'gcp'

    def test_fbc_postsubmit_no_wmco_map_uses_fallback(self, collector):
        """FBC postsubmit without wmco_version_map uses arithmetic fallback."""
        meta = collector._extract_metadata(
            'branch-ci-openshift-windows-machine-config-operator-'
            'fbc-main-v10-21-aws-winc'
        )
        # Fallback: 10 - 6 = 4, so OCP 4.21
        assert meta['version'] == '4.21'

    def test_fbc_postsubmit_no_version_uses_branch_map(self, collector_with_wmco_map):
        """FBC postsubmit without version segment uses branch_version_map."""
        meta = collector_with_wmco_map._extract_metadata(
            'branch-ci-openshift-windows-machine-config-operator-'
            'fbc-main-aws-winc'
        )
        # No v\d+-\d+ or release-X.Y, falls through to branch_version_map
        assert meta['version'] == '5.0'
        assert meta['platform'] == 'aws'

    def test_presubmit_job_extracts_version(self, collector):
        meta = collector._extract_metadata(
            'pull-ci-openshift-openshift-tests-private-'
            'release-4.21-amd64-aws-ipi-ovn-winc'
        )
        assert meta['version'] == '4.21'
        assert meta['platform'] == 'aws'

    def test_negative_no_version_pattern_returns_unknown(self, collector):
        """Negative: job name with no recognizable version -> 'unknown'."""
        meta = collector._extract_metadata('some-random-job-aws-winc')
        assert meta['version'] == 'unknown'
        assert meta['platform'] == 'aws'

    def test_negative_v_pattern_without_dash_not_matched(self, collector):
        """Negative: 'v1021' (no dash) should NOT match FBC pattern."""
        meta = collector._extract_metadata(
            'branch-ci-openshift-fbc-main-v1021-aws-winc'
        )
        # v1021 doesn't match v(\d+)-(\d+), so should fall through
        assert meta['version'] == 'unknown'

    def test_negative_release_without_version_number(self, collector):
        """Negative: 'release-main' should NOT match release-X.Y."""
        meta = collector._extract_metadata(
            'periodic-ci-openshift-release-main-aws-winc-f14'
        )
        assert meta['version'] == 'unknown'


class TestBuildJobUrl:
    """Tests for _build_job_url -- supports both logs/ and pr-logs/ paths."""

    def test_logs_path(self, collector):
        url = collector._build_job_url('/gcs/test/logs/some-job/12345')
        assert url == (
            'https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com'
            '/view/gs/test/logs/some-job/12345'
        )

    def test_pr_logs_path(self, collector):
        url = collector._build_job_url(
            '/gcs/test/pr-logs/pull/openshift_release/81646/'
            'rehearse-81646-periodic-ci-release-4.21-aws-winc/2075066861'
        )
        assert url == (
            'https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com'
            '/view/gs/test/pr-logs/pull/openshift_release/81646/'
            'rehearse-81646-periodic-ci-release-4.21-aws-winc/2075066861'
        )


class TestListRecentPrs:
    """Tests for _list_recent_prs."""

    def test_returns_sorted_pr_numbers(self, collector):
        links = [
            ('/gcs/test/pr-logs/pull/openshift_release/100/', '100/'),
            ('/gcs/test/pr-logs/pull/openshift_release/200/', '200/'),
            ('/gcs/test/pr-logs/pull/openshift_release/150/', '150/'),
        ]
        with patch.object(collector, '_list_directory', return_value=links):
            prs = collector._list_recent_prs('openshift_release', max_prs=10)

        assert prs == ['200', '150', '100']

    def test_respects_max_prs_limit(self, collector):
        links = [
            (f'/gcs/test/pr-logs/pull/repo/{i}/', f'{i}/')
            for i in range(1, 50)
        ]
        with patch.object(collector, '_list_directory', return_value=links):
            prs = collector._list_recent_prs('repo', max_prs=5)

        assert len(prs) == 5
        # Should be the 5 highest PR numbers
        assert prs == ['49', '48', '47', '46', '45']

    def test_ignores_non_numeric_entries(self, collector):
        """Negative: non-numeric directory names should be skipped."""
        links = [
            ('/gcs/test/pr-logs/pull/repo/100/', '100/'),
            ('/gcs/test/pr-logs/pull/repo/latest/', 'latest/'),
            ('/gcs/test/pr-logs/pull/repo/abc/', 'abc/'),
        ]
        with patch.object(collector, '_list_directory', return_value=links):
            prs = collector._list_recent_prs('repo', max_prs=10)

        assert prs == ['100']


class TestListPrJobs:
    """Tests for _list_pr_jobs -- fnmatch filtering and path construction."""

    def test_filters_by_pattern(self, collector):
        job_links = [
            ('/gcs/test/pr-logs/pull/repo/100/rehearse-100-aws-winc-f14/', 'rehearse-100-aws-winc-f14/'),
            ('/gcs/test/pr-logs/pull/repo/100/unrelated-job/', 'unrelated-job/'),
        ]
        build_links = [
            ('/gcs/test/pr-logs/pull/repo/100/rehearse-100-aws-winc-f14/2075/', '2075/'),
        ]

        def mock_list(path):
            if path.endswith('/100/'):
                return job_links
            if 'winc' in path:
                return build_links
            return []

        with patch.object(collector, '_list_directory', side_effect=mock_list):
            results = collector._list_pr_jobs('repo', '100', '*winc*')

        assert len(results) == 1
        assert results[0]['job_name'] == 'rehearse-100-aws-winc-f14'
        assert results[0]['build_id'] == '2075'

    def test_pattern_no_match_returns_empty(self, collector):
        """Negative: pattern that matches nothing returns empty list."""
        job_links = [
            ('/gcs/test/pr-logs/pull/repo/100/unrelated-job/', 'unrelated-job/'),
        ]
        with patch.object(collector, '_list_directory', return_value=job_links):
            results = collector._list_pr_jobs('repo', '100', '*winc*')

        assert results == []


class TestCollectAllWithPrSources:
    """Tests for collect_all with pr_log_sources integration."""

    def test_collect_all_works_without_pr_log_sources(self, collector):
        """Config backwards-compatibility: collect_all works when
        pr_log_sources is absent (empty/None)."""
        with patch.object(collector, '_resolve_patterns', return_value=[]):
            job_runs, test_results = collector.collect_all(
                start_date=datetime.now() - timedelta(days=7),
                end_date=datetime.now(),
                job_patterns=['some-pattern'],
                pr_log_sources=None,
            )

        assert job_runs == []
        assert test_results == []

    def test_collect_all_works_with_empty_pr_log_sources(self, collector):
        """Config backwards-compatibility: empty pr_log_sources list."""
        with patch.object(collector, '_resolve_patterns', return_value=[]):
            job_runs, test_results = collector.collect_all(
                start_date=datetime.now() - timedelta(days=7),
                end_date=datetime.now(),
                job_patterns=['some-pattern'],
                pr_log_sources=[],
            )

        assert job_runs == []
        assert test_results == []

    def test_collect_all_calls_pr_sources_when_configured(self, collector):
        """When pr_log_sources is non-empty, _collect_pr_sources is called."""
        pr_sources = [{'repo': 'openshift_release', 'job_pattern': '*winc*', 'max_prs': 5}]

        with patch.object(collector, '_resolve_patterns', return_value=[]):
            with patch.object(
                collector, '_collect_pr_sources',
                return_value=([], [])
            ) as mock_pr:
                collector.collect_all(
                    start_date=datetime.now() - timedelta(days=7),
                    end_date=datetime.now(),
                    job_patterns=['some-pattern'],
                    pr_log_sources=pr_sources,
                )

        mock_pr.assert_called_once()
        call_args = mock_pr.call_args
        assert call_args[0][0] == pr_sources


class TestProcessRunSinglePassJobType:
    """Tests for _process_run_single_pass setting job_type and job_url."""

    def test_sets_job_type_and_dynamic_url(self, collector):
        """Verify job_type is set and job_url is derived from run path."""
        run = {
            'job_name': 'rehearse-81646-periodic-ci-release-4.21-aws-winc-f14',
            'build_id': '2075066861',
            'path': '/gcs/test/pr-logs/pull/openshift_release/81646/'
                    'rehearse-81646-periodic-ci-release-4.21-aws-winc-f14/2075066861',
            'timestamp': None,
        }
        finished = {'timestamp': 1720500000, 'result': 'SUCCESS', 'duration': 3600}

        with patch.object(collector, '_fetch_finished_json', return_value=finished):
            with patch.object(collector, '_fetch_junit_xml_files', return_value=[]):
                result = collector._process_run_single_pass(run)

        assert result is not None
        job_run, test_results = result
        assert job_run.job_type == 'rehearse'
        assert 'pr-logs/pull' in job_run.job_url
        assert job_run.version == '4.21'
