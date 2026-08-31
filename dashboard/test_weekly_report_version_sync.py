"""Tests for weekly report version sync when switching tabs.

Validates that the showTab function syncs the overview versionFilter
dropdown value to the weekly report reportVersion dropdown before
calling refreshWeeklyReport() (issue #207).
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


def _extract_show_tab_body(html):
    """Extract the showTab function body from the rendered HTML."""
    match = re.search(
        r'function\s+showTab\s*\(tabName\)\s*\{', html
    )
    assert match, "showTab function not found in dashboard HTML"
    start = match.start()
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


def _extract_weekly_report_block(show_tab_body):
    """Extract the weekly-report branch from showTab."""
    match = re.search(
        r"if\s*\(\s*tabName\s*===\s*['\"]weekly-report['\"]\s*\)\s*\{",
        show_tab_body,
    )
    assert match, "weekly-report branch not found in showTab"
    start = match.start()
    brace_pos = show_tab_body.index('{', start)
    depth = 0
    for i in range(brace_pos, len(show_tab_body)):
        if show_tab_body[i] == '{':
            depth += 1
        elif show_tab_body[i] == '}':
            depth -= 1
            if depth == 0:
                return show_tab_body[brace_pos:i + 1]
    raise AssertionError("Could not find end of weekly-report block")


class TestWeeklyReportVersionSync:
    """Verify showTab syncs overview version to weekly report filter."""

    def test_show_tab_reads_version_filter(self, dashboard_html):
        """showTab must read the overview versionFilter value."""
        body = _extract_show_tab_body(dashboard_html)
        block = _extract_weekly_report_block(body)
        assert 'versionFilter' in block, (
            "showTab weekly-report block does not reference the "
            "overview versionFilter dropdown"
        )

    def test_show_tab_sets_report_version(self, dashboard_html):
        """showTab must set reportVersion from overview versionFilter."""
        body = _extract_show_tab_body(dashboard_html)
        block = _extract_weekly_report_block(body)
        assert 'reportVersion' in block, (
            "showTab weekly-report block does not reference "
            "reportVersion dropdown"
        )

    def test_version_sync_precedes_refresh(self, dashboard_html):
        """Version sync must happen before refreshWeeklyReport() call."""
        body = _extract_show_tab_body(dashboard_html)
        block = _extract_weekly_report_block(body)
        version_pos = block.index('versionFilter')
        refresh_pos = block.index('refreshWeeklyReport()')
        assert version_pos < refresh_pos, (
            "versionFilter sync must appear before "
            "refreshWeeklyReport() call"
        )

    def test_version_sync_iterates_options(self, dashboard_html):
        """Version sync must iterate reportVersion options to validate
        the value exists before setting it (matching the time range
        sync pattern)."""
        body = _extract_show_tab_body(dashboard_html)
        block = _extract_weekly_report_block(body)
        # Find the version sync section (after versionFilter read,
        # before refreshWeeklyReport)
        version_pos = block.index('versionFilter')
        refresh_pos = block.index('refreshWeeklyReport()')
        version_block = block[version_pos:refresh_pos]
        assert 'reportVersion' in version_block, (
            "reportVersion must be set between reading versionFilter "
            "and calling refreshWeeklyReport"
        )
        assert '.options' in version_block, (
            "Version sync must iterate reportVersion options to "
            "validate the value before setting it"
        )

    def test_no_version_sync_in_build_health(self, dashboard_html):
        """Build health tab should not sync version filters."""
        body = _extract_show_tab_body(dashboard_html)
        match = re.search(
            r"else\s+if\s*\(\s*tabName\s*===\s*['\"]build-health['\"]\s*\)",
            body,
        )
        assert match, "build-health branch not found in showTab"
        start = body.index('{', match.start())
        depth = 0
        for i in range(start, len(body)):
            if body[i] == '{':
                depth += 1
            elif body[i] == '}':
                depth -= 1
                if depth == 0:
                    build_block = body[start:i + 1]
                    break
        assert 'versionFilter' not in build_block, (
            "build-health block should not sync versionFilter"
        )
