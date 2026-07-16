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

    def test_fetchdata_handles_non_json_responses(self, client):
        """fetchData must handle non-JSON server responses without
        throwing an uncaught SyntaxError.  Either response.ok must be
        checked before response.json() is called, or response.json()
        must be wrapped in try/catch so that HTML or other non-JSON
        responses produce a descriptive error instead of crashing."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = _extract_function_body(script, 'fetchData')

        # Approach A: response.ok is checked before response.json()
        # Find the first occurrence of each after the fetch() call
        fetch_pos = body.find('await fetch(')
        assert fetch_pos != -1, "fetchData must call fetch()"
        after_fetch = body[fetch_pos:]

        ok_check_pos = after_fetch.find('!response.ok')
        json_call_pos = after_fetch.find('response.json()')
        ok_before_json = (
            ok_check_pos != -1
            and json_call_pos != -1
            and ok_check_pos < json_call_pos
        )

        # Approach B: response.json() is wrapped in try/catch
        try_catch_json = re.search(
            r'try\s*\{[^}]*response\.json\(\)', body, re.DOTALL
        )
        has_try_catch = try_catch_json is not None

        assert ok_before_json or has_try_catch, (
            "fetchData must protect against non-JSON responses: either "
            "check response.ok before calling response.json(), or wrap "
            "response.json() in try/catch to handle SyntaxError from "
            "HTML error pages"
        )
