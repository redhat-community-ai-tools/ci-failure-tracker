"""Tests for client-side fetch cache in the dashboard template.

Validates structural correctness of the response cache so that
switching tabs or re-selecting the same period serves cached results
instead of making redundant API calls (issue #22).

These tests parse the rendered JavaScript and verify ordering of
operations, conditional logic, and structural relationships rather
than checking for string presence alone (AGENTS.md rule 13).
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
    """Extract the main inline <script> block from the rendered HTML.

    Returns the content of the <script> block that contains the
    fetchData function (the application logic block, not CDN imports).
    """
    matches = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
    for block in reversed(matches):
        if 'fetchData' in block:
            return block
    raise AssertionError("No <script> block containing fetchData found")


def _extract_function_body(script, func_name):
    """Extract the body of a named JS function from the script source.

    Handles both sync and async function declarations.  Returns the
    full brace-delimited body (including the outer braces).
    """
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


class TestFetchCacheStructure:
    """Verify structural correctness of the client-side cache logic.

    Each test renders the dashboard, extracts the relevant JavaScript
    function body, and asserts on ordering or structural relationships
    between statements -- not merely on whether a variable name appears
    somewhere in the HTML.
    """

    def test_cache_check_precedes_fetch_call(self, client):
        """fetchData must consult the cache *before* making a network
        request.  If the cache read were after or absent, every call
        would hit the network, defeating the purpose of the cache."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = _extract_function_body(script, 'fetchData')

        cache_read_pos = body.find('_fetchCache[cacheKey]')
        fetch_call_pos = body.find('await fetch(')

        assert cache_read_pos != -1, (
            "fetchData must read from _fetchCache"
        )
        assert fetch_call_pos != -1, (
            "fetchData must call fetch()"
        )
        assert cache_read_pos < fetch_call_pos, (
            "Cache lookup must precede the network fetch() call so "
            "cached responses are served without a round-trip"
        )

    def test_ttl_check_guards_cache_return(self, client):
        """The cache freshness check must compare elapsed time against
        FETCH_CACHE_TTL_MS using a less-than comparison.  A negated or
        missing check would serve arbitrarily stale data or never use
        the cache at all."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = _extract_function_body(script, 'fetchData')

        ttl_pattern = re.search(
            r'Date\.now\(\)\s*-\s*cached\.time\s*\)\s*<\s*FETCH_CACHE_TTL_MS',
            body,
        )
        assert ttl_pattern, (
            "fetchData must check (Date.now() - cached.time) < "
            "FETCH_CACHE_TTL_MS to guard cached returns"
        )

    def test_error_response_bypasses_cache_write(self, client):
        """When the server returns an error (response.ok is false),
        fetchData must return the data *without* writing it to the
        cache.  The !response.ok early-return must appear before the
        cache-write statement so errors are never persisted."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = _extract_function_body(script, 'fetchData')

        ok_guard = re.search(
            r'if\s*\(\s*!response\.ok\s*\)\s*return\b', body
        )
        assert ok_guard, (
            "fetchData must have an early return when !response.ok"
        )

        cache_write_pos = body.find('_fetchCache[cacheKey] =')
        assert cache_write_pos != -1, (
            "fetchData must write to _fetchCache on success"
        )

        assert ok_guard.start() < cache_write_pos, (
            "The !response.ok guard must appear before the cache write "
            "so error responses are never stored"
        )

    def test_invalidate_clears_all_cache_entries(self, client):
        """invalidateFetchCache must enumerate and delete every key in
        _fetchCache.  A function that only resets a flag or clears a
        single key would leave stale entries behind."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = _extract_function_body(script, 'invalidateFetchCache')

        assert 'Object.keys(_fetchCache)' in body, (
            "invalidateFetchCache must enumerate all cache keys"
        )
        assert 'delete _fetchCache[' in body, (
            "invalidateFetchCache must delete entries from _fetchCache"
        )

    def test_cache_invalidated_before_dashboard_refresh(self, client):
        """After data collection completes, the cache must be cleared
        before refreshDashboard() is called.  If the order were
        reversed, the refresh would serve stale cached data from before
        the collection ran."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = _extract_function_body(script, 'checkCollectionStatus')

        invalidate_pos = body.find('invalidateFetchCache()')
        refresh_pos = body.find('refreshDashboard()')

        assert invalidate_pos != -1, (
            "checkCollectionStatus must call invalidateFetchCache()"
        )
        assert refresh_pos != -1, (
            "checkCollectionStatus must call refreshDashboard()"
        )
        assert invalidate_pos < refresh_pos, (
            "invalidateFetchCache() must be called before "
            "refreshDashboard() so the refresh fetches fresh data"
        )

    def test_cache_write_stores_data_and_timestamp(self, client):
        """The cache entry must include both the response data and a
        timestamp so the TTL check can determine freshness.  Missing
        either field would break cache reads or freshness checks."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = _extract_function_body(script, 'fetchData')

        cache_write = re.search(
            r'_fetchCache\[cacheKey\]\s*=\s*\{[^}]*\bdata\b[^}]*'
            r'\btime\b[^}]*\}',
            body,
        )
        if not cache_write:
            cache_write = re.search(
                r'_fetchCache\[cacheKey\]\s*=\s*\{[^}]*\btime\b[^}]*'
                r'\bdata\b[^}]*\}',
                body,
            )
        assert cache_write, (
            "Cache write must store an object containing both 'data' "
            "and 'time' fields"
        )

    def test_cache_key_incorporates_endpoint_and_params(self, client):
        """The cache key must combine endpoint and query parameters so
        different API calls are cached independently.  A key built from
        only one would cause cross-endpoint collisions or make filtered
        queries share a single cache slot."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = _extract_function_body(script, 'fetchData')

        key_construction = re.search(
            r'cacheKey\s*=\s*`\$\{endpoint\}[^`]*\$\{queryString\}`',
            body,
        )
        assert key_construction, (
            "Cache key must incorporate both endpoint and query params "
            "via template literal interpolation"
        )


class TestSessionExpiredHandling:
    """Verify that fetchData detects OAuth proxy 403 responses and shows
    a session-expired message instead of a confusing content-type error.

    When the OpenShift OAuth proxy returns HTTP 403 with an HTML body
    (session/cookie expired), the user should see clear re-authentication
    guidance rather than 'Expected JSON but received text/html'.
    """

    def test_403_non_json_check_exists(self, client):
        """fetchData must check for response.status === 403 combined with
        a non-JSON content-type.  This detects the OAuth proxy's HTML 403
        page that is returned when the user's session expires."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = _extract_function_body(script, 'fetchData')

        check_403 = re.search(
            r'response\.status\s*===\s*403\s*&&\s*!contentType\.includes\('
            r"['\"]application/json['\"]\)",
            body,
        )
        assert check_403, (
            "fetchData must check for response.status === 403 with "
            "non-JSON content-type to detect expired OAuth sessions"
        )

    def test_403_check_precedes_generic_content_type_check(self, client):
        """The 403 session-expired check must appear before the generic
        content-type mismatch check.  If the order were reversed, a 403
        from the OAuth proxy would hit the generic error path and show
        a confusing 'Expected JSON but received text/html' message."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = _extract_function_body(script, 'fetchData')

        check_403_pos = re.search(
            r'response\.status\s*===\s*403', body
        )
        generic_check_pos = re.search(
            r"if\s*\(\s*!contentType\.includes\(\s*['\"]application/json"
            r"['\"]\s*\)\s*\)",
            body,
        )

        assert check_403_pos, (
            "fetchData must have a 403 status check"
        )
        assert generic_check_pos, (
            "fetchData must have a generic content-type check"
        )
        assert check_403_pos.start() < generic_check_pos.start(), (
            "The 403 session-expired check must appear before the "
            "generic content-type check so expired sessions get a "
            "helpful message instead of a confusing content-type error"
        )

    def test_403_error_contains_session_expired_message(self, client):
        """The error thrown for a 403 with non-JSON content must contain
        session-expired guidance so the user knows to refresh the page
        to re-authenticate."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = _extract_function_body(script, 'fetchData')

        # Find the block that handles 403 + non-JSON
        check_403 = re.search(
            r'response\.status\s*===\s*403\s*&&\s*!contentType\.includes\('
            r"['\"]application/json['\"]\)",
            body,
        )
        assert check_403, (
            "fetchData must have a 403 + non-JSON check"
        )

        # The throw statement following the 403 check must mention
        # session expiry and re-authentication
        block_after = body[check_403.start():check_403.start() + 300]
        assert 'Session expired' in block_after, (
            "The 403 error message must mention 'Session expired'"
        )
        assert 're-authenticate' in block_after, (
            "The 403 error message must mention 're-authenticate' "
            "so the user knows to refresh the page"
        )

    def test_403_json_response_bypasses_session_expired_check(self, client):
        """A 403 response with application/json content-type should NOT
        trigger the session-expired check.  This distinguishes OAuth
        proxy 403s (HTML body) from application-level 403s (JSON body)
        that the Flask backend might return in the future."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = _extract_function_body(script, 'fetchData')

        # The 403 check must include a content-type guard so JSON 403s
        # fall through to normal error handling
        check_403 = re.search(
            r'response\.status\s*===\s*403\s*&&\s*!contentType\.includes\('
            r"['\"]application/json['\"]\)",
            body,
        )
        assert check_403, (
            "The 403 check must require non-JSON content-type so that "
            "application-level JSON 403 responses are handled normally"
        )
