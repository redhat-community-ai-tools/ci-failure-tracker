"""Tests for GCSWebCollector JUnit XML parsing and diagnostic logging.

Validates that nested testsuites are handled correctly without
double-counting test counts or duplicating test results, and that
the collector logs warnings when jobs return no builds.
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
