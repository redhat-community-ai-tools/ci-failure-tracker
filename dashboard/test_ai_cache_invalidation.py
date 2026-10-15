"""Tests for AI analysis cache invalidation when error messages change.

Validates that stale cached AI analyses are not returned when the
underlying failure error message has changed (issue #244).

Includes structural tests for the runAIAnalysis JS function changes
in the dashboard template (AGENTS.md rule 16).
"""

import hashlib
import os
import re
import sqlite3
import tempfile

import pytest

from conftest import extract_function_body
from src.storage.database import DashboardDatabase
from web.server import create_app


def _hash(message):
    """Compute SHA-256 hash of an error message."""
    return hashlib.sha256(message.encode('utf-8')).hexdigest()


@pytest.fixture
def db(tmp_path):
    """Create a temporary database for testing."""
    db_path = str(tmp_path / "test.db")
    database = DashboardDatabase(db_path)
    yield database
    database.close()


class TestAICacheInvalidation:
    """Tests for error_message_hash cache invalidation."""

    def test_cache_returned_when_error_unchanged(self, db):
        """Cached analysis is returned when error hash matches."""
        old_error = 'wrong kubelet version 1.28'
        error_hash = _hash(old_error)

        db.save_ai_analysis('OCP-33612', '4.21', analysis={
            'root_cause': 'Wrong kubelet version 1.28',
            'confidence': 85,
            'failure_type': 'product_bug',
        }, error_message_hash=error_hash)

        result = db.get_ai_analysis(
            'OCP-33612', '4.21',
            current_error_hash=error_hash,
        )
        assert result is not None
        assert result['root_cause'] == 'Wrong kubelet version 1.28'

    def test_cache_invalidated_on_error_change(self, db):
        """Cached analysis is NOT returned when error message changed."""
        old_error = 'wrong kubelet version 1.28'
        old_hash = _hash(old_error)

        db.save_ai_analysis('OCP-33612', '4.21', analysis={
            'root_cause': 'Wrong kubelet version 1.28',
            'confidence': 85,
            'failure_type': 'product_bug',
        }, error_message_hash=old_hash)

        new_error = 'wrong kubelet version 1.29'
        new_hash = _hash(new_error)

        result = db.get_ai_analysis(
            'OCP-33612', '4.21',
            current_error_hash=new_hash,
        )
        assert result is None

    def test_cache_returned_without_hash_check(self, db):
        """Cached analysis is returned when no current_error_hash given."""
        db.save_ai_analysis('OCP-33612', '4.21', analysis={
            'root_cause': 'Some root cause',
            'confidence': 90,
            'failure_type': 'transient',
        }, error_message_hash=_hash('some error'))

        result = db.get_ai_analysis('OCP-33612', '4.21')
        assert result is not None
        assert result['root_cause'] == 'Some root cause'

    def test_legacy_analysis_without_hash_returned(self, db):
        """Old analyses without stored hash are returned (backward compat)."""
        db.save_ai_analysis('OCP-33612', '4.21', analysis={
            'root_cause': 'Legacy analysis',
            'confidence': 80,
            'failure_type': 'product_bug',
        })
        # No error_message_hash was stored (None)

        result = db.get_ai_analysis(
            'OCP-33612', '4.21',
            current_error_hash=_hash('any error'),
        )
        # Stored hash is NULL, so cache is not invalidated
        assert result is not None
        assert result['root_cause'] == 'Legacy analysis'

    def test_new_analysis_overwrites_stale(self, db):
        """A new save with updated hash replaces the old analysis."""
        old_hash = _hash('old error')
        db.save_ai_analysis('OCP-33612', '4.21', analysis={
            'root_cause': 'Old root cause',
            'confidence': 85,
            'failure_type': 'product_bug',
        }, error_message_hash=old_hash)

        new_hash = _hash('new error')
        db.save_ai_analysis('OCP-33612', '4.21', analysis={
            'root_cause': 'New root cause',
            'confidence': 90,
            'failure_type': 'automation_bug',
        }, error_message_hash=new_hash)

        result = db.get_ai_analysis(
            'OCP-33612', '4.21',
            current_error_hash=new_hash,
        )
        assert result is not None
        assert result['root_cause'] == 'New root cause'
        assert result['error_message_hash'] == new_hash

    def test_hash_stored_in_database(self, db):
        """Verify the error_message_hash column is populated."""
        error_hash = _hash('test error message')
        db.save_ai_analysis('OCP-12345', '4.22', analysis={
            'root_cause': 'Test cause',
            'confidence': 75,
            'failure_type': 'transient',
        }, error_message_hash=error_hash)

        result = db.get_ai_analysis('OCP-12345', '4.22')
        assert result['error_message_hash'] == error_hash

    def test_different_test_same_hash_independent(self, db):
        """Different tests with same error hash are independent entries."""
        error_hash = _hash('shared error')
        db.save_ai_analysis('OCP-11111', '4.21', analysis={
            'root_cause': 'Cause for 11111',
            'confidence': 80,
            'failure_type': 'transient',
        }, error_message_hash=error_hash)

        db.save_ai_analysis('OCP-22222', '4.21', analysis={
            'root_cause': 'Cause for 22222',
            'confidence': 85,
            'failure_type': 'product_bug',
        }, error_message_hash=error_hash)

        r1 = db.get_ai_analysis(
            'OCP-11111', '4.21', current_error_hash=error_hash,
        )
        r2 = db.get_ai_analysis(
            'OCP-22222', '4.21', current_error_hash=error_hash,
        )
        assert r1 is not None
        assert r2 is not None
        assert r1['root_cause'] == 'Cause for 11111'
        assert r2['root_cause'] == 'Cause for 22222'


class TestMigrationPreservesErrorMessageHash:
    """Test that the schema migration includes the error_message_hash column."""

    def test_migration_from_old_schema_includes_hash_column(self, tmp_path):
        """Create a DB with the old schema (platform TEXT NOT NULL), then
        open it with DashboardDatabase to trigger migration.  Verify
        save_ai_analysis succeeds with error_message_hash."""
        db_path = str(tmp_path / "migrate.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE ai_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_name TEXT NOT NULL,
                version TEXT NOT NULL,
                platform TEXT NOT NULL,
                analysis_date DATETIME NOT NULL,
                root_cause TEXT,
                component TEXT,
                confidence INTEGER,
                failure_type TEXT,
                platform_specific INTEGER,
                affected_platforms TEXT,
                evidence TEXT,
                suggested_action TEXT,
                issue_title TEXT,
                issue_description TEXT,
                analysis_mode TEXT,
                cost REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(test_name, version, analysis_date)
            )
        """)
        conn.commit()
        conn.close()

        # Opening with DashboardDatabase triggers _create_tables migration
        database = DashboardDatabase(db_path)
        try:
            error_hash = _hash('test error after migration')
            database.save_ai_analysis(
                'OCP-99999', '4.21',
                analysis={
                    'root_cause': 'Post-migration analysis',
                    'confidence': 90,
                    'failure_type': 'product_bug',
                },
                error_message_hash=error_hash,
            )

            result = database.get_ai_analysis(
                'OCP-99999', '4.21',
                current_error_hash=error_hash,
            )
            assert result is not None
            assert result['root_cause'] == 'Post-migration analysis'
            assert result['error_message_hash'] == error_hash
        finally:
            database.close()


# -- Structural tests for runAIAnalysis JS changes (AGENTS.md rule 16) --

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


class TestRunAIAnalysisStructure:
    """Structural tests for the runAIAnalysis JS function.

    Modelled on TestFetchCacheStructure in test_fetch_cache.py.
    Renders the dashboard, extracts the function body, and asserts on
    ordering and structural relationships.
    """

    def test_uses_cached_parameter_controls_fetch_payload(self, client):
        """runAIAnalysis must pass the useCached parameter into the
        fetch body as use_cached so the server can decide whether to
        serve a cached result."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'runAIAnalysis')

        assert 'use_cached: useCached' in body or 'use_cached:useCached' in body, (
            "runAIAnalysis must forward useCached into the fetch payload "
            "as use_cached"
        )

    def test_default_uses_cached_is_true(self, client):
        """When useCached is undefined, it must default to true so the
        initial click uses the cache."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'runAIAnalysis')

        # The function should set useCached = true when undefined
        assert 'useCached = true' in body, (
            "runAIAnalysis must default useCached to true"
        )

    def test_reanalyze_button_replaces_success_button(self, client):
        """On success, the button must be changed to offer re-analysis
        with useCached=false rather than showing a static 'Analyzed'."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'runAIAnalysis')

        # Must contain Re-analyze text
        assert 'Re-analyze' in body, (
            "Success path must set button text to Re-analyze"
        )
        # Must re-bind onclick to call with false
        assert 'runAIAnalysis(false)' in body, (
            "Re-analyze button must call runAIAnalysis(false) to bypass cache"
        )

    def test_reanalyze_binding_after_success_content(self, client):
        """The Re-analyze button binding must appear after the success
        content is rendered, not before."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'runAIAnalysis')

        content_pos = body.find('content.innerHTML')
        reanalyze_pos = body.find('Re-analyze')

        assert content_pos != -1, "Success path must set content.innerHTML"
        assert reanalyze_pos != -1, "Success path must reference Re-analyze"
        assert content_pos < reanalyze_pos, (
            "Content rendering must precede Re-analyze button setup"
        )

    def test_error_path_restores_default_onclick(self, client):
        """When a non-OK response is received, the error path must
        restore btn.onclick to the default cache-enabled behavior so
        that a retry after a failed re-analysis uses the cache."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'runAIAnalysis')

        # Find the else branch (error path) after the success if-block
        # It should contain onclick restoration
        else_pos = body.find("'Failed to analyze'")
        assert else_pos != -1, "Error else-branch must exist"

        # After the error content, the onclick should be restored
        error_section = body[else_pos:]
        assert 'btn.onclick' in error_section, (
            "Error path must restore btn.onclick to default behavior"
        )

    def test_catch_path_restores_default_onclick(self, client):
        """When a network error occurs, the catch path must restore
        btn.onclick to the default cache-enabled behavior."""
        response = client.get('/')
        html = response.data.decode('utf-8')
        script = _extract_script_body(html)
        body = extract_function_body(script, 'runAIAnalysis')

        # Find the catch block
        catch_pos = body.find('} catch (error)')
        assert catch_pos != -1, "Catch block must exist"

        catch_section = body[catch_pos:]
        assert 'btn.onclick' in catch_section, (
            "Catch path must restore btn.onclick to default behavior"
        )
