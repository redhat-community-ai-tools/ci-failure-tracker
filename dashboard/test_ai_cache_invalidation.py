"""Tests for AI analysis cache invalidation when error messages change.

Validates that stale cached AI analyses are not returned when the
underlying failure error message has changed (issue #244).
"""

import hashlib
import pytest

from src.storage.database import DashboardDatabase


@pytest.fixture
def db(tmp_path):
    """Create a temporary database for testing."""
    db_path = str(tmp_path / "test.db")
    return DashboardDatabase(db_path)


class TestAICacheInvalidation:
    """Tests for error_message_hash cache invalidation."""

    def _hash(self, message):
        """Compute SHA-256 hash of an error message."""
        return hashlib.sha256(message.encode('utf-8')).hexdigest()

    def test_cache_returned_when_error_unchanged(self, db):
        """Cached analysis is returned when error hash matches."""
        old_error = 'wrong kubelet version 1.28'
        error_hash = self._hash(old_error)

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
        old_hash = self._hash(old_error)

        db.save_ai_analysis('OCP-33612', '4.21', analysis={
            'root_cause': 'Wrong kubelet version 1.28',
            'confidence': 85,
            'failure_type': 'product_bug',
        }, error_message_hash=old_hash)

        new_error = 'wrong kubelet version 1.29'
        new_hash = self._hash(new_error)

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
        }, error_message_hash=self._hash('some error'))

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
            current_error_hash=self._hash('any error'),
        )
        # Stored hash is NULL, so cache is not invalidated
        assert result is not None
        assert result['root_cause'] == 'Legacy analysis'

    def test_new_analysis_overwrites_stale(self, db):
        """A new save with updated hash replaces the old analysis."""
        old_hash = self._hash('old error')
        db.save_ai_analysis('OCP-33612', '4.21', analysis={
            'root_cause': 'Old root cause',
            'confidence': 85,
            'failure_type': 'product_bug',
        }, error_message_hash=old_hash)

        new_hash = self._hash('new error')
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
        error_hash = self._hash('test error message')
        db.save_ai_analysis('OCP-12345', '4.22', analysis={
            'root_cause': 'Test cause',
            'confidence': 75,
            'failure_type': 'transient',
        }, error_message_hash=error_hash)

        result = db.get_ai_analysis('OCP-12345', '4.22')
        assert result['error_message_hash'] == error_hash

    def test_different_test_same_hash_independent(self, db):
        """Different tests with same error hash are independent entries."""
        error_hash = self._hash('shared error')
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
