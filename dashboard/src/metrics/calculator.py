"""
Metrics calculator for test pass rates and trends
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from collections import defaultdict

from storage.database import DashboardDatabase


class MetricsCalculator:
    """Calculate and aggregate test metrics"""

    def __init__(self, database: DashboardDatabase, blocklist: Optional[List[str]] = None):
        """
        Initialize calculator with database

        Args:
            database: DashboardDatabase instance
            blocklist: Optional list of test names to exclude from metrics
        """
        self.db = database
        self.blocklist = blocklist or []

    def get_overall_trend(
        self,
        days: int = 30,
        version: Optional[str] = None,
        platform: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get overall pass rate trend over time

        Args:
            days: Number of days to look back
            version: Optional version filter
            platform: Optional platform filter

        Returns:
            Dictionary with trend data:
            {
                'dates': ['2026-03-01', '2026-03-02', ...],
                'pass_rates': [85.5, 87.2, ...],
                'total_runs': [45, 48, ...]
            }
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        daily_data = self.db.get_daily_pass_rates(
            start_date, end_date, version, platform
        )

        # Group by date
        trend_data = defaultdict(lambda: {'total_runs': 0, 'pass_rates': []})

        for row in daily_data:
            date = row['date']
            trend_data[date]['total_runs'] += row['total_runs']
            trend_data[date]['pass_rates'].append(row['avg_pass_rate'])

        # Calculate averages
        dates = sorted(trend_data.keys())
        pass_rates = []
        total_runs = []

        for date in dates:
            data = trend_data[date]
            # Average the pass rates across platforms/versions for this date
            avg_rate = sum(data['pass_rates']) / len(data['pass_rates']) if data['pass_rates'] else 0
            pass_rates.append(round(avg_rate, 2))
            total_runs.append(data['total_runs'])

        return {
            'dates': dates,
            'pass_rates': pass_rates,
            'total_runs': total_runs
        }

    def get_test_rankings(
        self,
        days: int = 30,
        version: Optional[str] = None,
        platform: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get tests ranked by pass rate (lowest first - most problematic)

        Args:
            days: Number of days to look back
            version: Optional version filter
            platform: Optional platform filter
            limit: Maximum number of tests to return

        Returns:
            List of test dictionaries with pass rate statistics
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        test_data = self.db.get_test_pass_rates(
            start_date, end_date, version=version, platform=platform, blocklist=self.blocklist
        )

        # Filter tests with meaningful sample size
        # Use lower threshold for platform filtering or short time periods (7 days)
        min_runs = 1 if (platform or days <= 7) else 2
        meaningful_tests = [
            test for test in test_data
            if test['total_runs'] >= min_runs
        ]

        # Sort by pass rate (ascending - worst first)
        ranked = sorted(meaningful_tests, key=lambda x: x['pass_rate'])

        return ranked[:limit]

    def get_version_comparison(
        self,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Compare pass rates across versions

        Args:
            days: Number of days to look back

        Returns:
            Dictionary with version comparison data:
            {
                'versions': ['4.21', '4.22'],
                'pass_rates': [85.5, 87.2],
                'total_runs': [120, 95]
            }
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        version_data = self.db.get_version_comparison(start_date, end_date)

        versions = []
        pass_rates = []
        total_runs = []

        for row in version_data:
            if row['version'] != 'unknown':  # Skip unknown versions
                versions.append(row['version'])
                pass_rates.append(round(row['avg_pass_rate'], 2))
                total_runs.append(row['total_runs'])

        return {
            'versions': versions,
            'pass_rates': pass_rates,
            'total_runs': total_runs
        }

    def get_platform_comparison(
        self,
        days: int = 30,
        version: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Compare pass rates across platforms

        Args:
            days: Number of days to look back
            version: Optional version filter

        Returns:
            Dictionary with platform comparison data
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        daily_data = self.db.get_daily_pass_rates(
            start_date, end_date, version=version
        )

        # Group by platform
        platform_data = defaultdict(lambda: {'total_runs': 0, 'pass_rates': []})

        for row in daily_data:
            platform = row['platform']
            if platform and platform != 'unknown':
                platform_data[platform]['total_runs'] += row['total_runs']
                platform_data[platform]['pass_rates'].append(row['avg_pass_rate'])

        platforms = []
        pass_rates = []
        total_runs = []

        for platform, data in sorted(platform_data.items()):
            platforms.append(platform)
            avg_rate = sum(data['pass_rates']) / len(data['pass_rates']) if data['pass_rates'] else 0
            pass_rates.append(round(avg_rate, 2))
            total_runs.append(data['total_runs'])

        return {
            'platforms': platforms,
            'pass_rates': pass_rates,
            'total_runs': total_runs
        }

    def get_summary_stats(
        self,
        days: int = 7,
        version: Optional[str] = None,
        platform: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get summary statistics for the dashboard

        Args:
            days: Number of days to look back
            version: Optional version filter
            platform: Optional platform filter

        Returns:
            Dictionary with summary statistics including:
            - total_tests: Total number of unique tests
            - passed_tests: Total number of test executions that passed
            - failed_tests: Total number of test executions that failed
            - avg_pass_rate: Percentage of test executions that passed
            - trend: 'improving', 'declining', or 'stable'
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        # Get test-level pass rates
        test_data = self.db.get_test_pass_rates(start_date, end_date, version=version, platform=platform, blocklist=self.blocklist)

        if not test_data:
            return {
                'total_tests': 0,
                'passed_tests': 0,
                'failed_tests': 0,
                'avg_pass_rate': 0,
                'trend': 'stable'
            }

        # Count test executions (not just unique tests)
        total_tests = len(test_data)
        total_executions = sum(test['total_runs'] for test in test_data)
        passed_executions = sum(test['passed_runs'] for test in test_data)
        failed_executions = total_executions - passed_executions

        # Calculate overall pass rate based on executions
        avg_pass_rate = (passed_executions / total_executions * 100) if total_executions > 0 else 0

        # Calculate trend based on daily pass rates
        # Guard: if all test executions passed, trend cannot be declining.
        # Compare source integers rather than the derived float to avoid
        # rounding gaps (e.g. 99.995 rounds to 100.0 for display).
        if passed_executions == total_executions and total_executions > 0:
            trend = 'stable'
        else:
            daily_data = self.db.get_daily_pass_rates(start_date, end_date, version=version, platform=platform)
            if daily_data and len(daily_data) > 1:
                # Aggregate by date before splitting — each date may have
                # multiple rows (one per platform) and splitting the flat
                # row list skews the temporal comparison.
                date_rates = defaultdict(list)
                for row in daily_data:
                    date_rates[row['date']].append(row['avg_pass_rate'])
                sorted_dates = sorted(date_rates.keys())
                daily_avgs = [
                    sum(date_rates[d]) / len(date_rates[d])
                    for d in sorted_dates
                ]

                midpoint = len(daily_avgs) // 2
                if midpoint > 0:
                    first_half = sum(daily_avgs[:midpoint]) / midpoint
                    second_half = sum(daily_avgs[midpoint:]) / len(daily_avgs[midpoint:])
                    diff = second_half - first_half

                    if diff > 2:
                        trend = 'improving'
                    elif diff < -2:
                        trend = 'declining'
                    else:
                        trend = 'stable'
                else:
                    trend = 'stable'
            else:
                trend = 'stable'

        return {
            'total_tests': total_tests,
            'passed_tests': passed_executions,
            'failed_tests': failed_executions,
            'avg_pass_rate': round(avg_pass_rate, 2),
            'trend': trend,
            'date_range': f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
        }
