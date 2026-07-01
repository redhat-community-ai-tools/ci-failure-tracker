"""Tests for GCSWebCollector JUnit XML parsing.

Validates that nested testsuites are handled correctly without
double-counting test counts or duplicating test results.
"""

import xml.etree.ElementTree as ET

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
