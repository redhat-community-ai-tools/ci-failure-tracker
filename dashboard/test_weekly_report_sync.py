"""Tests for weekly report time range sync when switching tabs.

Validates that the showTab function syncs the overview timeRange
dropdown value to the weekly report reportCurrentDays and
reportPreviousDays dropdowns before calling refreshWeeklyReport()
(issue #66).
"""

import os
import re
import sys
import tempfile

import pytest

# Add src to path so imports work like the main app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from web.server import create_app


@pytest.fixture
def app():
    """Create a test Flask app with a temporary database."""
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    application = create_app(db_path=db_path, config_file=config_path)
    application.config['TESTING'] = True
    yield application
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def dashboard_html(client):
    """Return the rendered dashboard HTML as a string."""
    response = client.get('/')
    return response.data.decode('utf-8')


class TestWeeklyReportTimeRangeSync:
    """Verify showTab syncs overview time range to weekly report filters."""

    def _extract_show_tab_body(self, html):
        """Extract the showTab function body from the rendered HTML."""
        match = re.search(
            r'function\s+showTab\s*\(tabName\)\s*\{', html
        )
        assert match, "showTab function not found in dashboard HTML"
        start = match.start()
        # Find matching closing brace by counting braces
        depth = 0
        pos = html.index('{', start)
        for i in range(pos, len(html)):
            if html[i] == '{':
                depth += 1
            elif html[i] == '}':
                depth -= 1
                if depth == 0:
                    return html[start:i + 1]
        raise AssertionError("Could not find end of showTab function")

    def test_show_tab_reads_time_range(self, dashboard_html):
        """showTab must read the overview timeRange value."""
        body = self._extract_show_tab_body(dashboard_html)
        assert 'timeRange' in body, (
            "showTab does not reference the overview timeRange dropdown"
        )

    def test_show_tab_sets_report_current_days(self, dashboard_html):
        """showTab must set reportCurrentDays from overview timeRange."""
        body = self._extract_show_tab_body(dashboard_html)
        assert 'reportCurrentDays' in body, (
            "showTab does not reference reportCurrentDays dropdown"
        )

    def test_show_tab_sets_report_previous_days(self, dashboard_html):
        """showTab must set reportPreviousDays from overview timeRange."""
        body = self._extract_show_tab_body(dashboard_html)
        assert 'reportPreviousDays' in body, (
            "showTab does not reference reportPreviousDays dropdown"
        )

    def test_sync_precedes_refresh(self, dashboard_html):
        """timeRange sync must happen before refreshWeeklyReport() call."""
        body = self._extract_show_tab_body(dashboard_html)
        time_range_pos = body.index('timeRange')
        refresh_pos = body.index('refreshWeeklyReport()')
        assert time_range_pos < refresh_pos, (
            "timeRange sync must appear before refreshWeeklyReport() call"
        )

    def test_report_current_days_has_overview_options(self, dashboard_html):
        """reportCurrentDays dropdown must include common timeRange values.

        The overview timeRange includes 3, 7, 14, 30. The weekly report
        reportCurrentDays must include at least those values so the sync
        can succeed for all common selections.
        """
        required_values = ['3', '7', '14', '30']
        # Find the reportCurrentDays select element
        match = re.search(
            r'<select\s+id="reportCurrentDays"[^>]*>(.*?)</select>',
            dashboard_html,
            re.DOTALL,
        )
        assert match, "reportCurrentDays select element not found"
        select_html = match.group(1)
        option_values = re.findall(r'value="(\d+)"', select_html)
        for val in required_values:
            assert val in option_values, (
                f"reportCurrentDays is missing option value={val}"
            )
