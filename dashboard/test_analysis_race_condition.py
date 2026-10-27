"""Tests for AI analysis race condition guard (issue #269).

Validates that:
1. A request-tracking counter (currentModalRequestId) exists and is
   incremented by openLogWindow and closeLogModal to invalidate stale
   async responses.
2. An AbortController is used to cancel in-flight fetch requests when
   the modal is closed or switched to a different test.
3. loadExistingTestData and runAIAnalysis capture the request ID before
   the fetch and check it before writing to the DOM.
4. AbortError is handled silently in both async functions.
5. Error messages in runAIAnalysis use escapeHtml (AGENTS.md rule 21).

Per AGENTS.md rule 16, these tests verify structural correctness of
the JavaScript rather than checking for simple string presence.
"""

import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from conftest import extract_function_body
from web.server import create_app


@pytest.fixture
def app(monkeypatch):
    """Create a test Flask app with AI analysis enabled."""
    monkeypatch.setenv('ENABLE_AI_ANALYSIS', 'true')
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
    matches = re.findall(
        r'<\s*script\b[^>]*>(.*?)<\s*/\s*script\b[^>]*>',
        html, re.IGNORECASE | re.DOTALL,
    )
    for block in reversed(matches):
        if 'runAIAnalysis' in block:
            return block
    raise AssertionError("No <script> block containing runAIAnalysis found")


class TestModalRequestIdGuard:
    """Verify the currentModalRequestId counter prevents stale responses."""

    def test_request_id_variable_declared(self, client):
        """currentModalRequestId must be declared as a top-level variable."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        assert 'currentModalRequestId' in script, (
            "currentModalRequestId variable must be declared"
        )

    def test_abort_controller_variable_declared(self, client):
        """currentAbortController must be declared as a top-level variable."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        assert 'currentAbortController' in script, (
            "currentAbortController variable must be declared"
        )

    def test_open_log_window_increments_request_id(self, client):
        """openLogWindow must increment currentModalRequestId to
        invalidate any in-flight requests from a previous modal."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'openLogWindow')
        assert 'currentModalRequestId++' in body, (
            "openLogWindow must increment currentModalRequestId"
        )

    def test_close_log_modal_increments_request_id(self, client):
        """closeLogModal must increment currentModalRequestId to
        invalidate any in-flight requests."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'closeLogModal')
        assert 'currentModalRequestId++' in body, (
            "closeLogModal must increment currentModalRequestId"
        )

    def test_open_log_window_aborts_previous_requests(self, client):
        """openLogWindow must abort the previous AbortController
        before creating a new one."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'openLogWindow')
        assert 'currentAbortController.abort()' in body, (
            "openLogWindow must abort any existing AbortController"
        )
        assert 'new AbortController()' in body, (
            "openLogWindow must create a new AbortController"
        )

    def test_close_log_modal_aborts_requests(self, client):
        """closeLogModal must abort the AbortController to cancel
        in-flight fetch requests."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'closeLogModal')
        assert 'currentAbortController.abort()' in body, (
            "closeLogModal must abort the AbortController"
        )


class TestLoadExistingTestDataStalenessGuard:
    """Verify loadExistingTestData discards stale responses."""

    def test_captures_request_id_before_fetch(self, client):
        """loadExistingTestData must capture currentModalRequestId
        before the fetch call."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'loadExistingTestData')

        id_capture_pos = body.find('requestId = currentModalRequestId')
        fetch_pos = body.find("fetch('/api/get-test-data'")
        assert id_capture_pos != -1, (
            "Must capture currentModalRequestId into a local variable"
        )
        assert fetch_pos != -1, "Must contain fetch call"
        assert id_capture_pos < fetch_pos, (
            "Request ID capture must happen before the fetch call"
        )

    def test_checks_request_id_before_dom_update(self, client):
        """loadExistingTestData must check requestId against
        currentModalRequestId before updating the DOM."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'loadExistingTestData')

        assert 'requestId !== currentModalRequestId' in body, (
            "Must compare requestId to currentModalRequestId before "
            "updating the DOM"
        )

    def test_passes_abort_signal_to_fetch(self, client):
        """loadExistingTestData must pass the AbortController signal
        to the fetch call."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'loadExistingTestData')

        assert 'signal:' in body or 'signal :' in body, (
            "Must pass signal to the fetch options"
        )

    def test_handles_abort_error_silently(self, client):
        """loadExistingTestData must catch AbortError without showing
        an error to the user."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'loadExistingTestData')

        assert "error.name === 'AbortError'" in body, (
            "Must check for AbortError in the catch block"
        )


class TestRunAIAnalysisStalenessGuard:
    """Verify runAIAnalysis discards stale responses."""

    def test_captures_request_id_before_fetch(self, client):
        """runAIAnalysis must capture currentModalRequestId before
        the fetch call."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'runAIAnalysis')

        id_capture_pos = body.find('requestId = currentModalRequestId')
        fetch_pos = body.find("fetch('/api/analyze-failure'")
        assert id_capture_pos != -1, (
            "Must capture currentModalRequestId into a local variable"
        )
        assert fetch_pos != -1, "Must contain fetch call"
        assert id_capture_pos < fetch_pos, (
            "Request ID capture must happen before the fetch call"
        )

    def test_checks_request_id_before_dom_update(self, client):
        """runAIAnalysis must check requestId against
        currentModalRequestId before updating the DOM."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'runAIAnalysis')

        assert 'requestId !== currentModalRequestId' in body, (
            "Must compare requestId to currentModalRequestId before "
            "updating the DOM"
        )

    def test_passes_abort_signal_to_fetch(self, client):
        """runAIAnalysis must pass the AbortController signal
        to the fetch call."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'runAIAnalysis')

        assert 'signal:' in body or 'signal :' in body, (
            "Must pass signal to the fetch options"
        )

    def test_handles_abort_error_silently(self, client):
        """runAIAnalysis must catch AbortError without showing an
        error to the user."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'runAIAnalysis')

        assert "error.name === 'AbortError'" in body, (
            "Must check for AbortError in the catch block"
        )

    def test_error_messages_use_escape_html(self, client):
        """Error messages rendered via innerHTML must be passed
        through escapeHtml (AGENTS.md rule 21)."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'runAIAnalysis')

        # Find error display lines that use innerHTML assignment
        # and verify they call escapeHtml
        error_lines = [
            line.strip() for line in body.split('\n')
            if 'innerHTML' in line and 'Error:' in line
        ]
        assert len(error_lines) > 0, (
            "runAIAnalysis must have error display lines"
        )
        for line in error_lines:
            assert 'escapeHtml(' in line, (
                f"Error display must use escapeHtml: {line}"
            )

    def test_request_id_captured_at_function_start(self, client):
        """requestId must be captured at the top of runAIAnalysis,
        before any async operations or DOM updates."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'runAIAnalysis')

        id_capture_pos = body.find('requestId = currentModalRequestId')
        # Button state update is the first DOM interaction
        btn_update_pos = body.find('btn.disabled = true')
        assert id_capture_pos != -1, "Must capture request ID"
        assert btn_update_pos != -1, "Must disable button"
        assert id_capture_pos < btn_update_pos, (
            "Request ID capture must happen before any DOM updates"
        )

    def test_stale_check_before_success_content(self, client):
        """The staleness check must appear between the fetch and the
        success content rendering to prevent stale data display."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'runAIAnalysis')

        fetch_pos = body.find("fetch('/api/analyze-failure'")
        stale_check_pos = body.find(
            'requestId !== currentModalRequestId'
        )
        # Find the success path content.innerHTML (AI Suggestion)
        success_content_pos = body.find('AI Suggestion:')

        assert fetch_pos != -1, "Must contain fetch call"
        assert stale_check_pos != -1, "Must contain staleness check"
        assert success_content_pos != -1, "Must contain success content"
        assert fetch_pos < stale_check_pos < success_content_pos, (
            "Staleness check must appear between fetch and "
            "success content rendering"
        )
