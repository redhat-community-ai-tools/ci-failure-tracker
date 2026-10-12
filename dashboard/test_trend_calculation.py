"""Tests for trend calculation in MetricsCalculator.get_summary_stats.

Covers:
- Trend is 'stable' when all test executions pass (100% pass rate)
- Trend aggregates by date before splitting into halves, so uneven
  platform distribution across dates does not skew the result
- Trend correctly reports 'improving' and 'declining' when pass rates
  actually change over time
"""

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.collectors.base import JobRun, TestResult, TestStatus
from src.storage.database import DashboardDatabase
from src.metrics.calculator import MetricsCalculator


@pytest.fixture
def db(tmp_path):
    """Create a temporary database."""
    db_path = str(tmp_path / 'test.db')
    database = DashboardDatabase(db_path)
    yield database
    database.close()


def _make_job_run(job_name, build_id, timestamp, version, platform,
                  total_tests, passed_tests):
    """Helper to create a JobRun with sensible defaults."""
    failed = total_tests - passed_tests
    status = TestStatus.PASSED if failed == 0 else TestStatus.FAILED
    return JobRun(
        job_name=job_name,
        build_id=build_id,
        status=status,
        timestamp=timestamp,
        duration_seconds=100.0,
        version=version,
        platform=platform,
        total_tests=total_tests,
        passed_tests=passed_tests,
        failed_tests=failed,
        skipped_tests=0,
        job_url='https://example.com',
    )


def _make_test_result(test_name, build_id, timestamp, version, platform,
                      status, job_name='job'):
    """Helper to create a TestResult."""
    return TestResult(
        test_name=test_name,
        test_description='Test description',
        status=status,
        timestamp=timestamp,
        duration_seconds=10.0,
        error_message=None,
        job_name=job_name,
        build_id=build_id,
        version=version,
        platform=platform,
        job_url='https://example.com',
        log_url=None,
    )


class TestTrendStableWhenAllPass:
    """Trend must be 'stable' when pass rate is 100%."""

    def test_trend_stable_when_all_pass(self, db):
        """100% pass rate across multiple dates and platforms -> stable."""
        now = datetime.now()
        runs = []
        test_results = []

        # 3 dates, 3 platforms each, all passing — 9 job runs
        for day_offset in range(3):
            ts = now - timedelta(days=day_offset)
            for i, platform in enumerate(['aws', 'gcp', 'azure']):
                bid = f'{day_offset}-{i}'
                jname = f'job-{platform}'
                runs.append(_make_job_run(
                    jname, bid, ts, '5.0', platform,
                    total_tests=18, passed_tests=18,
                ))
                # Add OCP-prefixed test results (required by get_test_pass_rates)
                for t in range(6):
                    test_results.append(_make_test_result(
                        f'OCP-{1000 + t}', bid, ts, '5.0', platform,
                        TestStatus.PASSED, job_name=jname,
                    ))

        db.insert_job_runs(runs)
        db.insert_test_results(test_results)

        calc = MetricsCalculator(db)
        stats = calc.get_summary_stats(days=7, version='5.0')

        assert stats['avg_pass_rate'] == 100.0
        assert stats['trend'] == 'stable'

    def test_trend_stable_single_platform_all_pass(self, db):
        """100% pass rate on one platform -> stable."""
        now = datetime.now()
        runs = []
        test_results = []

        for day_offset in range(4):
            ts = now - timedelta(days=day_offset)
            bid = str(day_offset)
            runs.append(_make_job_run(
                'job-aws', bid, ts, '5.0', 'aws',
                total_tests=18, passed_tests=18,
            ))
            for t in range(6):
                test_results.append(_make_test_result(
                    f'OCP-{2000 + t}', bid, ts, '5.0', 'aws',
                    TestStatus.PASSED, job_name='job-aws',
                ))

        db.insert_job_runs(runs)
        db.insert_test_results(test_results)

        calc = MetricsCalculator(db)
        stats = calc.get_summary_stats(days=7, version='5.0')

        assert stats['avg_pass_rate'] == 100.0
        assert stats['trend'] == 'stable'


class TestTrendNotSkewedByPlatformDistribution:
    """Trend should aggregate by date before comparing halves."""

    def test_uneven_platforms_stable(self, db):
        """Day 1: 3 platforms at 90%, Day 2: 1 platform at 90%.

        Row-based midpoint split would put Day1-platformA and
        Day1-platformB in first half, Day1-platformC and Day2-platformA
        in second half — same rates but arbitrary split.  Date-based
        aggregation gives the same average for both days -> stable.
        """
        now = datetime.now()
        day1 = now - timedelta(days=2)
        day2 = now - timedelta(days=1)
        runs = []
        test_results = []

        # Day 1: 3 platforms, all at 90% (18 total, 16 passed)
        for i, platform in enumerate(['aws', 'gcp', 'azure']):
            bid = f'd1-{i}'
            jname = f'job-{platform}'
            runs.append(_make_job_run(
                jname, bid, day1, '5.0', platform,
                total_tests=18, passed_tests=16,
            ))
            for t in range(9):
                status = TestStatus.PASSED if t < 8 else TestStatus.FAILED
                test_results.append(_make_test_result(
                    f'OCP-{3000 + t}', bid, day1, '5.0', platform,
                    status, job_name=jname,
                ))

        # Day 2: 1 platform, also at 90% (18 total, 16 passed)
        bid = 'd2-0'
        runs.append(_make_job_run(
            'job-aws', bid, day2, '5.0', 'aws',
            total_tests=18, passed_tests=16,
        ))
        for t in range(9):
            status = TestStatus.PASSED if t < 8 else TestStatus.FAILED
            test_results.append(_make_test_result(
                f'OCP-{3000 + t}', bid, day2, '5.0', 'aws',
                status, job_name='job-aws',
            ))

        db.insert_job_runs(runs)
        db.insert_test_results(test_results)

        calc = MetricsCalculator(db)
        stats = calc.get_summary_stats(days=7, version='5.0')

        assert stats['trend'] == 'stable'


class TestTrendDirectionAccuracy:
    """Trend correctly reports improving and declining."""

    def test_trend_improving(self, db):
        """Pass rate increases from first half to second half."""
        now = datetime.now()
        runs = []
        test_results = []

        # Day 1 (older): 70% pass rate
        day1 = now - timedelta(days=4)
        bid = 'old'
        runs.append(_make_job_run(
            'job-aws', bid, day1, '5.0', 'aws',
            total_tests=20, passed_tests=14,
        ))
        for t in range(10):
            status = TestStatus.PASSED if t < 7 else TestStatus.FAILED
            test_results.append(_make_test_result(
                f'OCP-{4000 + t}', bid, day1, '5.0', 'aws',
                status, job_name='job-aws',
            ))

        # Day 2 (newer): 95% pass rate
        day2 = now - timedelta(days=1)
        bid = 'new'
        runs.append(_make_job_run(
            'job-aws', bid, day2, '5.0', 'aws',
            total_tests=20, passed_tests=19,
        ))
        for t in range(10):
            status = TestStatus.PASSED if t < 9 else TestStatus.FAILED
            test_results.append(_make_test_result(
                f'OCP-{4000 + t}', bid, day2, '5.0', 'aws',
                status, job_name='job-aws',
            ))

        db.insert_job_runs(runs)
        db.insert_test_results(test_results)

        calc = MetricsCalculator(db)
        stats = calc.get_summary_stats(days=7, version='5.0')

        assert stats['trend'] == 'improving'

    def test_trend_declining(self, db):
        """Pass rate decreases from first half to second half."""
        now = datetime.now()
        runs = []
        test_results = []

        # Day 1 (older): 95% pass rate
        day1 = now - timedelta(days=4)
        bid = 'old'
        runs.append(_make_job_run(
            'job-aws', bid, day1, '5.0', 'aws',
            total_tests=20, passed_tests=19,
        ))
        for t in range(10):
            status = TestStatus.PASSED if t < 9 else TestStatus.FAILED
            test_results.append(_make_test_result(
                f'OCP-{5000 + t}', bid, day1, '5.0', 'aws',
                status, job_name='job-aws',
            ))

        # Day 2 (newer): 70% pass rate
        day2 = now - timedelta(days=1)
        bid = 'new'
        runs.append(_make_job_run(
            'job-aws', bid, day2, '5.0', 'aws',
            total_tests=20, passed_tests=14,
        ))
        for t in range(10):
            status = TestStatus.PASSED if t < 7 else TestStatus.FAILED
            test_results.append(_make_test_result(
                f'OCP-{5000 + t}', bid, day2, '5.0', 'aws',
                status, job_name='job-aws',
            ))

        db.insert_job_runs(runs)
        db.insert_test_results(test_results)

        calc = MetricsCalculator(db)
        stats = calc.get_summary_stats(days=7, version='5.0')

        assert stats['trend'] == 'declining'
