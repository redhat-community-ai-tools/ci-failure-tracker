"""Tests for empty previous period handling in weekly report.

When no CI runs exist in the previous comparison window the report
must return None (JSON null) for previous_pass_rate and delta instead
of a misleading 0%.  The Slack and console formatters must render
these None values as "N/A" rather than crashing or showing "0%".

Covers issue #70.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add src to path so imports work like the main app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from reports.weekly_report import WeeklyReportGenerator


def _make_generator(daily_current, daily_previous,
                    test_current=None, test_previous=None):
    """Build a WeeklyReportGenerator with mocked database responses."""
    db = MagicMock()

    # get_daily_pass_rates returns current then previous
    db.get_daily_pass_rates.side_effect = [daily_current, daily_previous]

    # get_test_pass_rates returns current then previous per platform
    if test_current is None:
        test_current = []
    if test_previous is None:
        test_previous = []
    db.get_test_pass_rates.side_effect = lambda *a, **kw: test_current

    gen = WeeklyReportGenerator(db)
    # Mock blocklist on the calculator
    gen.calculator.blocklist = []
    return gen


class TestEmptyPreviousPeriod:
    """previous_pass_rate and delta must be None when no previous data."""

    def test_previous_rate_is_none_when_no_previous_runs(self):
        """With current data but no previous data, previous_pass_rate
        must be None (not 0)."""
        current_daily = [
            {'platform': 'aws', 'total_runs': 10, 'avg_pass_rate': 85.0},
        ]
        previous_daily = []  # no runs in previous window

        gen = _make_generator(current_daily, previous_daily)
        result = gen.get_platform_week_over_week()

        aws = result['platforms']['aws']
        assert aws['previous_pass_rate'] is None
        assert aws['delta'] is None
        assert aws['current_pass_rate'] == 85.0

    def test_both_periods_have_data(self):
        """When both periods have data, values must be numeric."""
        current_daily = [
            {'platform': 'aws', 'total_runs': 10, 'avg_pass_rate': 90.0},
        ]
        previous_daily = [
            {'platform': 'aws', 'total_runs': 8, 'avg_pass_rate': 80.0},
        ]

        gen = _make_generator(current_daily, previous_daily)
        result = gen.get_platform_week_over_week()

        aws = result['platforms']['aws']
        assert aws['previous_pass_rate'] == 80.0
        assert aws['current_pass_rate'] == 90.0
        assert aws['delta'] == 10.0

    def test_current_rate_is_none_when_no_current_runs(self):
        """If only previous data exists, current_pass_rate must be None."""
        current_daily = []
        previous_daily = [
            {'platform': 'gcp', 'total_runs': 5, 'avg_pass_rate': 70.0},
        ]

        gen = _make_generator(current_daily, previous_daily)
        result = gen.get_platform_week_over_week()

        gcp = result['platforms']['gcp']
        assert gcp['current_pass_rate'] is None
        assert gcp['delta'] is None
        assert gcp['previous_pass_rate'] == 70.0


class TestSlackReportNoneHandling:
    """Slack report must show N/A for missing previous data."""

    def test_slack_report_shows_na_for_missing_previous(self):
        current_daily = [
            {'platform': 'aws', 'total_runs': 10, 'avg_pass_rate': 85.0},
        ]
        previous_daily = []

        db = MagicMock()
        db.get_daily_pass_rates.side_effect = [current_daily, previous_daily]
        db.get_test_pass_rates.return_value = []

        gen = WeeklyReportGenerator(db)
        gen.calculator.blocklist = []
        gen.calculator.get_test_rankings = MagicMock(return_value=[])
        gen.calculator.get_summary_stats = MagicMock(return_value={
            'avg_pass_rate': 85.0,
            'total_tests': 10,
            'passed_tests': 8,
            'failed_tests': 2,
            'trend': 'stable',
        })

        report = gen.generate_slack_report()

        # Must contain N/A, must not contain "0%" for previous
        assert 'N/A' in report
        # The line for aws should not show "0%" as previous
        for line in report.splitlines():
            if 'Aws' in line:
                assert '0%' not in line.split('→')[0], \
                    f"Previous rate should be N/A, not 0%: {line}"
                break

    def test_slack_report_normal_case(self):
        """When both periods have data, report uses numeric values."""
        current_daily = [
            {'platform': 'aws', 'total_runs': 10, 'avg_pass_rate': 90.0},
        ]
        previous_daily = [
            {'platform': 'aws', 'total_runs': 8, 'avg_pass_rate': 80.0},
        ]

        db = MagicMock()
        db.get_daily_pass_rates.side_effect = [current_daily, previous_daily]
        db.get_test_pass_rates.return_value = []

        gen = WeeklyReportGenerator(db)
        gen.calculator.blocklist = []
        gen.calculator.get_test_rankings = MagicMock(return_value=[])
        gen.calculator.get_summary_stats = MagicMock(return_value={
            'avg_pass_rate': 90.0,
            'total_tests': 10,
            'passed_tests': 9,
            'failed_tests': 1,
            'trend': 'up',
        })

        report = gen.generate_slack_report()

        for line in report.splitlines():
            if 'Aws' in line:
                assert '80%' in line.split('→')[0], \
                    f"Previous rate should be 80%: {line}"
                break


class TestConsoleReportNoneHandling:
    """Console report must show N/A for missing previous data."""

    def test_console_report_shows_na_for_missing_previous(self):
        current_daily = [
            {'platform': 'aws', 'total_runs': 10, 'avg_pass_rate': 85.0},
        ]
        previous_daily = []

        db = MagicMock()
        db.get_daily_pass_rates.side_effect = [current_daily, previous_daily]
        db.get_test_pass_rates.return_value = []

        gen = WeeklyReportGenerator(db)
        gen.calculator.blocklist = []
        gen.calculator.get_test_rankings = MagicMock(return_value=[])
        gen.calculator.get_summary_stats = MagicMock(return_value={
            'avg_pass_rate': 85.0,
            'total_tests': 10,
            'passed_tests': 8,
            'failed_tests': 2,
            'trend': 'stable',
        })

        report = gen.generate_console_report()

        assert 'N/A' in report
        for line in report.splitlines():
            if 'Aws' in line:
                assert '0.0%' not in line, \
                    f"Previous rate should be N/A, not 0.0%: {line}"
                break
