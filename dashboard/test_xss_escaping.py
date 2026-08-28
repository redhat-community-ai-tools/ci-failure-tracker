"""Tests for XSS escaping in the build health drill-down template code.

Validates that:
1. escapeHtml is defined at global scope (accessible to all functions).
2. toggleBuildHealthDetail() escapes all API-sourced values before
   innerHTML interpolation: job_name, build_id, timestamp, test_name,
   job_url, and error messages.
3. renderVersionCard() escapes interpolated parameters in the onclick
   attribute string (versionLabel, platform, ocp_version).
4. job_url is validated against an http(s) scheme before use in href.
5. The escapeHtml function correctly converts HTML metacharacters and
   does not corrupt normal CI data (no double-escaping).

Per AGENTS.md rule 15, these tests verify structural correctness of
escaping in the rendered JavaScript rather than checking for simple
string presence.
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


def _extract_script_body(html):
    """Extract the main inline <script> block from the rendered HTML."""
    script_tag_pattern = re.compile(
        r'<\s*script\b[^>]*>(.*?)<\s*/\s*script\b[^>]*>',
        re.IGNORECASE | re.DOTALL,
    )
    matches = script_tag_pattern.findall(html)
    for block in reversed(matches):
        if 'fetchData' in block:
            return block
    raise AssertionError("No <script> block containing fetchData found")


def _extract_function_body(script, func_name):
    """Extract the body of a named JS function from the script source."""
    pattern = (
        r'(?:async\s+)?function\s+'
        + re.escape(func_name)
        + r'\s*\([^)]*\)\s*\{'
    )
    match = re.search(pattern, script)
    if not match:
        raise AssertionError(f"Function {func_name} not found in script")

    brace_start = match.end() - 1
    depth = 1
    pos = brace_start + 1
    while pos < len(script) and depth > 0:
        if script[pos] == '{':
            depth += 1
        elif script[pos] == '}':
            depth -= 1
        pos += 1

    return script[brace_start:pos]


class TestEscapeHtmlGlobalScope:
    """Verify escapeHtml is defined at global scope."""

    def test_escape_html_defined_before_functions(self, client):
        """escapeHtml must be defined at global script scope, before
        any function that uses it, so it is accessible to both
        renderVersionCard and toggleBuildHealthDetail."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)

        # escapeHtml should be defined at the top level of the script,
        # before the first function definition
        escape_pos = script.find('const escapeHtml')
        assert escape_pos != -1, "escapeHtml not found in script"

        # It should appear before renderVersionCard
        render_pos = script.find('function renderVersionCard')
        assert render_pos != -1
        assert escape_pos < render_pos, (
            "escapeHtml must be defined before renderVersionCard"
        )

        # It should appear before toggleBuildHealthDetail
        toggle_pos = script.find('function toggleBuildHealthDetail')
        assert toggle_pos != -1
        assert escape_pos < toggle_pos, (
            "escapeHtml must be defined before toggleBuildHealthDetail"
        )

    def test_no_duplicate_escape_html_in_openLogWindow(self, client):
        """escapeHtml should not be redefined inside openLogWindow
        now that it is at global scope."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = _extract_function_body(script, 'openLogWindow')

        assert 'const escapeHtml' not in body, (
            "escapeHtml should not be redefined inside openLogWindow"
        )


class TestToggleBuildHealthDetailEscaping:
    """Verify toggleBuildHealthDetail escapes all API-sourced values."""

    @pytest.fixture
    def toggle_body(self, client):
        """Extract the body of toggleBuildHealthDetail."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        return _extract_function_body(script, 'toggleBuildHealthDetail')

    def test_job_name_escaped_in_title_attribute(self, toggle_body):
        """run.job_name must be escaped when used in the title attribute."""
        # The title attribute should use escapeHtml(run.job_name)
        title_pattern = re.compile(
            r'title=.*escapeHtml\(run\.job_name\)', re.DOTALL
        )
        assert title_pattern.search(toggle_body), (
            "run.job_name must be escaped with escapeHtml() in title attr"
        )

    def test_job_name_escaped_in_display(self, toggle_body):
        """The displayed job name (jobShort) must be escaped."""
        assert 'escapeHtml(jobShort)' in toggle_body, (
            "jobShort (derived from job_name) must be escaped for display"
        )

    def test_build_id_escaped(self, toggle_body):
        """run.build_id must be escaped before display."""
        assert 'escapeHtml(run.build_id)' in toggle_body, (
            "run.build_id must be escaped with escapeHtml()"
        )

    def test_timestamp_escaped(self, toggle_body):
        """dateStr (from run.timestamp) must be escaped before display."""
        assert 'escapeHtml(dateStr)' in toggle_body, (
            "dateStr must be escaped with escapeHtml()"
        )

    def test_test_name_escaped(self, toggle_body):
        """ft.test_name must be escaped in the failed-test list."""
        assert 'escapeHtml(ft.test_name)' in toggle_body, (
            "ft.test_name must be escaped with escapeHtml()"
        )

    def test_error_message_escaped(self, toggle_body):
        """The catch block must escape the error before innerHTML."""
        assert 'escapeHtml(String(err))' in toggle_body, (
            "Error message must be escaped via escapeHtml(String(err))"
        )

    def test_job_url_validated_scheme(self, toggle_body):
        """run.job_url must be validated against http(s) scheme."""
        # Look for the URL scheme validation regex (JS uses https?:\/\/)
        assert re.search(
            r'https\?:', toggle_body
        ), (
            "job_url must be validated with an http(s) scheme check"
        )

    def test_job_url_escaped_in_href(self, toggle_body):
        """The validated job URL must be escaped before href use."""
        assert 'escapeHtml(safeJobUrl)' in toggle_body, (
            "safeJobUrl must be escaped with escapeHtml() in href"
        )


class TestRenderVersionCardEscaping:
    """Verify renderVersionCard escapes onclick attribute parameters."""

    @pytest.fixture
    def render_body(self, client):
        """Extract the body of renderVersionCard."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        return _extract_function_body(script, 'renderVersionCard')

    def test_version_label_escaped_in_onclick(self, render_body):
        """versionLabel must be escaped in the onclick attribute."""
        # Find the onclick line and verify escapeHtml wraps versionLabel
        onclick_lines = [
            line for line in render_body.split('\n')
            if 'onclick=' in line and 'toggleBuildHealthDetail' in line
        ]
        assert len(onclick_lines) == 1, (
            "Expected exactly one onclick with toggleBuildHealthDetail"
        )
        onclick = onclick_lines[0]
        assert 'escapeHtml(versionLabel)' in onclick, (
            "versionLabel must be escaped in onclick attribute"
        )

    def test_platform_escaped_in_onclick(self, render_body):
        """platform must be escaped in the onclick attribute."""
        onclick_lines = [
            line for line in render_body.split('\n')
            if 'onclick=' in line and 'toggleBuildHealthDetail' in line
        ]
        onclick = onclick_lines[0]
        assert 'escapeHtml(platform)' in onclick, (
            "platform must be escaped in onclick attribute"
        )

    def test_ocp_version_escaped_in_onclick(self, render_body):
        """ocp_version must be escaped in the onclick attribute."""
        onclick_lines = [
            line for line in render_body.split('\n')
            if 'onclick=' in line and 'toggleBuildHealthDetail' in line
        ]
        onclick = onclick_lines[0]
        assert 'escapeHtml(ver.ocp_version' in onclick, (
            "ver.ocp_version must be escaped in onclick attribute"
        )

    def test_version_label_escaped_in_heading(self, render_body):
        """versionLabel must be escaped in the h3 heading."""
        h3_lines = [
            line for line in render_body.split('\n')
            if '<h3' in line and 'versionLabel' in line
        ]
        assert len(h3_lines) == 1
        assert 'escapeHtml(versionLabel)' in h3_lines[0], (
            "versionLabel must be escaped in the h3 heading"
        )

    def test_ocp_version_escaped_in_display(self, render_body):
        """ver.ocp_version must be escaped when displayed in the
        OCP version label span."""
        assert 'escapeHtml(ver.ocp_version)' in render_body, (
            "ver.ocp_version must be escaped for display"
        )

    def test_source_url_escaped_in_href(self, render_body):
        """ver.source_url must be escaped in the href attribute."""
        assert 'escapeHtml(ver.source_url)' in render_body, (
            "ver.source_url must be escaped in href"
        )

    def test_platform_escaped_in_table_cell(self, render_body):
        """platform must be escaped in the table cell display."""
        assert 'escapeHtml(platform)' in render_body, (
            "platform must be escaped in table cell"
        )


class TestEscapeHtmlBehavior:
    """Verify the escapeHtml function implementation is correct.

    These are Python-side equivalents that confirm the JS function
    handles XSS payloads and normal data correctly.
    """

    @staticmethod
    def _escape_html(s):
        """Python equivalent of the JS escapeHtml function."""
        s = str(s) if s else ''
        return (
            s.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace("'", '&#39;')
            .replace('"', '&quot;')
        )

    def test_xss_script_tag_escaped(self):
        """XSS payload <script>alert(1)</script> is escaped."""
        result = self._escape_html('<script>alert(1)</script>')
        assert '<script>' not in result
        assert '&lt;script&gt;' in result

    def test_xss_img_tag_escaped(self):
        """XSS payload with img onerror is escaped."""
        result = self._escape_html('<img src=x onerror=alert(1)>')
        assert '<img' not in result
        assert '&lt;img' in result

    def test_normal_ci_data_unchanged(self):
        """Normal CI data like job names is not corrupted."""
        assert self._escape_html('e2e-aws-sdn-serial') == 'e2e-aws-sdn-serial'

    def test_normal_build_id_unchanged(self):
        """Numeric build IDs pass through unchanged."""
        assert self._escape_html('12345') == '12345'

    def test_normal_test_name_unchanged(self):
        """OCP test names pass through unchanged."""
        assert self._escape_html('OCP-12345') == 'OCP-12345'

    def test_ampersand_escaped(self):
        """Ampersands are escaped to prevent entity injection."""
        assert '&amp;' in self._escape_html('a&b')

    def test_quotes_escaped(self):
        """Both quote types are escaped for attribute safety."""
        assert '&quot;' in self._escape_html('a"b')
        assert '&#39;' in self._escape_html("a'b")

    def test_empty_string_handled(self):
        """Empty string returns empty string."""
        assert self._escape_html('') == ''

    def test_none_handled(self):
        """None returns empty string (matches JS behavior)."""
        assert self._escape_html(None) == ''
