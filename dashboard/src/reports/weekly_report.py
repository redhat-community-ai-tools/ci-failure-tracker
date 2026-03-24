"""
Weekly report generator for platform breakdown
"""

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from collections import defaultdict

from storage.database import DashboardDatabase
from metrics.calculator import MetricsCalculator


class WeeklyReportGenerator:
    """Generate weekly platform breakdown reports"""

    def __init__(self, database: DashboardDatabase, blocklist: Optional[List[str]] = None):
        """
        Initialize report generator

        Args:
            database: DashboardDatabase instance
            blocklist: Optional list of test names to exclude
        """
        self.db = database
        self.calculator = MetricsCalculator(database, blocklist=blocklist)

    def get_platform_week_over_week(
        self,
        current_week_days: int = 7,
        previous_week_days: int = 7,
        version: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get week-over-week platform comparison

        Args:
            current_week_days: Days in current week (default: 7)
            previous_week_days: Days in previous week (default: 7)
            version: Optional version filter

        Returns:
            Dictionary with platform comparison data
        """
        # Current week
        current_end = datetime.now()
        current_start = current_end - timedelta(days=current_week_days)

        # Previous week
        previous_end = current_start
        previous_start = previous_end - timedelta(days=previous_week_days)

        # Get current week data
        current_daily = self.db.get_daily_pass_rates(current_start, current_end, version=version)

        # Get previous week data
        previous_daily = self.db.get_daily_pass_rates(previous_start, previous_end, version=version)

        # Group by platform
        current_platforms = defaultdict(lambda: {'total_runs': 0, 'pass_rates': []})
        previous_platforms = defaultdict(lambda: {'total_runs': 0, 'pass_rates': []})

        for row in current_daily:
            platform = row['platform']
            if platform and platform != 'unknown':
                current_platforms[platform]['total_runs'] += row['total_runs']
                current_platforms[platform]['pass_rates'].append(row['avg_pass_rate'])

        for row in previous_daily:
            platform = row['platform']
            if platform and platform != 'unknown':
                previous_platforms[platform]['total_runs'] += row['total_runs']
                previous_platforms[platform]['pass_rates'].append(row['avg_pass_rate'])

        # Calculate averages and deltas
        all_platforms = set(current_platforms.keys()) | set(previous_platforms.keys())
        comparisons = {}

        for platform in sorted(all_platforms):
            current_rates = current_platforms[platform]['pass_rates']
            previous_rates = previous_platforms[platform]['pass_rates']

            current_avg = sum(current_rates) / len(current_rates) if current_rates else 0
            previous_avg = sum(previous_rates) / len(previous_rates) if previous_rates else 0

            delta = current_avg - previous_avg

            comparisons[platform] = {
                'current_pass_rate': round(current_avg, 1),
                'previous_pass_rate': round(previous_avg, 1),
                'delta': round(delta, 1),
                'current_runs': current_platforms[platform]['total_runs'],
                'previous_runs': previous_platforms[platform]['total_runs']
            }

        return {
            'current_period': f"{current_start.strftime('%b %d')}–{current_end.strftime('%d')}",
            'previous_period': f"{previous_start.strftime('%b %d')}–{previous_end.strftime('%d')}",
            'platforms': comparisons
        }

    def generate_slack_report(
        self,
        current_week_days: int = 7,
        previous_week_days: int = 7,
        top_failures: int = 5
    ) -> str:
        """
        Generate Slack-formatted weekly report

        Args:
            current_week_days: Days in current week
            previous_week_days: Days in previous week
            top_failures: Number of top failing tests to include

        Returns:
            Slack-formatted report string
        """
        # Get platform comparison
        comparison = self.get_platform_week_over_week(current_week_days, previous_week_days)

        # Get top failing tests
        top_tests = self.calculator.get_test_rankings(days=current_week_days, limit=top_failures)

        # Build report
        lines = []
        lines.append(f"WINC PROW CI Health — Week {comparison['current_period']}")
        lines.append("")
        lines.append("Pass Rate:")

        # Platform breakdown
        for platform, data in sorted(comparison['platforms'].items()):
            prev = data['previous_pass_rate']
            curr = data['current_pass_rate']
            delta = data['delta']

            # Status indicator
            if delta >= 5:
                status = "[OK]"
            elif delta >= 0:
                status = "[OK]"
            elif delta >= -5:
                status = "[WARN]"
            else:
                status = "[FAIL]"

            # Delta formatting
            if delta > 0:
                delta_str = f"↑+{delta}%"
            elif delta < 0:
                delta_str = f"↓{delta}%"
            else:
                delta_str = "→0%"

            lines.append(f"{status} {platform.capitalize():10s} {prev:.0f}% → {curr:.0f}%   {delta_str}")

        lines.append("")

        # Top failing tests
        if top_tests:
            lines.append(f"Top {len(top_tests)} Failing Tests:")
            for i, test in enumerate(top_tests, 1):
                lines.append(
                    f"{i}. {test['test_name']} ({test['pass_rate']:.0f}% pass rate, "
                    f"{test['total_runs']} runs)"
                )
        else:
            lines.append("Top Failing Tests: None")

        lines.append("")

        # Summary stats
        current_end = datetime.now()
        current_start = current_end - timedelta(days=current_week_days)
        summary = self.calculator.get_summary_stats(days=current_week_days)

        lines.append(f"Overall: {summary['avg_pass_rate']}% pass rate, {summary['total_runs']} total runs")
        lines.append(f"Period: {current_start.strftime('%Y-%m-%d')} to {current_end.strftime('%Y-%m-%d')}")

        return "\n".join(lines)

    def generate_console_report(
        self,
        current_week_days: int = 7,
        previous_week_days: int = 7,
        top_failures: int = 10
    ) -> str:
        """
        Generate console-formatted weekly report

        Args:
            current_week_days: Days in current week
            previous_week_days: Days in previous week
            top_failures: Number of top failing tests to include

        Returns:
            Console-formatted report string
        """
        # Get platform comparison
        comparison = self.get_platform_week_over_week(current_week_days, previous_week_days)

        # Get top failing tests
        top_tests = self.calculator.get_test_rankings(days=current_week_days, limit=top_failures)

        # Build report
        lines = []
        lines.append("=" * 70)
        lines.append(f"WINC PROW CI Health Report — Week {comparison['current_period']}")
        lines.append("=" * 70)
        lines.append("")
        lines.append("PLATFORM PASS RATES (Week-over-Week)")
        lines.append("-" * 70)
        lines.append(f"{'Platform':<12} {'Previous':>8} {'Current':>8} {'Change':>10} {'Trend':>10}")
        lines.append("-" * 70)

        # Platform breakdown
        for platform, data in sorted(comparison['platforms'].items()):
            prev = data['previous_pass_rate']
            curr = data['current_pass_rate']
            delta = data['delta']

            # Delta formatting
            if delta > 0:
                delta_str = f"+{delta}%"
                trend = "UP"
            elif delta < 0:
                delta_str = f"{delta}%"
                trend = "DOWN"
            else:
                delta_str = "0%"
                trend = "STABLE"

            lines.append(
                f"{platform.capitalize():<12} {prev:>7.1f}% {curr:>7.1f}% {delta_str:>10} {trend:>10}"
            )

        lines.append("")
        lines.append(f"TOP {len(top_tests)} FAILING TESTS")
        lines.append("-" * 70)
        lines.append(f"{'Rank':<6} {'Test Name':<20} {'Version':<8} {'Pass Rate':>10} {'Runs':>6}")
        lines.append("-" * 70)

        # Top failing tests
        if top_tests:
            for i, test in enumerate(top_tests, 1):
                lines.append(
                    f"{i:<6} {test['test_name']:<20} {test['version']:<8} "
                    f"{test['pass_rate']:>9.1f}% {test['total_runs']:>6}"
                )
        else:
            lines.append("No failing tests found")

        lines.append("")
        lines.append("SUMMARY")
        lines.append("-" * 70)

        current_end = datetime.now()
        current_start = current_end - timedelta(days=current_week_days)
        summary = self.calculator.get_summary_stats(days=current_week_days)

        lines.append(f"Overall Pass Rate: {summary['avg_pass_rate']}%")
        lines.append(f"Total Test Runs:   {summary['total_runs']}")
        lines.append(f"Trend:             {summary['trend'].upper()}")
        lines.append(f"Period:            {current_start.strftime('%Y-%m-%d')} to {current_end.strftime('%Y-%m-%d')}")
        lines.append("=" * 70)

        return "\n".join(lines)
