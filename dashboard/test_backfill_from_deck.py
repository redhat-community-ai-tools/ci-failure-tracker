"""Tests for the deck backfill script.

Covers the two properties that matter before writing rows:

1. Duplicate OCP IDs collapse with failure precedence, so a later passed
   variant cannot replace a failed result through INSERT OR REPLACE.
2. The script never disables TLS verification while sending the bearer
   token.
"""

import importlib.util
import os

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), 'scripts',
                           'backfill_from_deck.py')


def _load_script():
    spec = importlib.util.spec_from_file_location('backfill_from_deck',
                                                  SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _test(name, status, duration=1.0, message=None):
    return {
        'name': name,
        'status': status,
        'duration_seconds': duration,
        'message': message,
    }


class TestAggregateByTestId:
    """Duplicate OCP IDs must not collapse to a passing result."""

    def test_failed_wins_over_passed(self):
        script = _load_script()
        tests = [
            _test('OCP-1234:user:Windows_Containers:[sig-windows] WINC check',
                  'failed', 30.0, 'boom'),
            _test('OCP-1234:user:Windows_Containers:[sig-windows] WINC check',
                  'passed', 1.0),
        ]

        kept = script.aggregate_by_test_id(tests)

        assert len(kept) == 1
        assert kept[0]['status'] == 'failed'
        assert kept[0]['message'] == 'boom'

    def test_failed_wins_regardless_of_order(self):
        script = _load_script()
        tests = [
            _test('OCP-77:a:Windows_Containers:[sig-windows] x', 'passed'),
            _test('OCP-77:a:Windows_Containers:[sig-windows] x', 'failed'),
        ]

        kept = script.aggregate_by_test_id(tests)

        assert [t['status'] for t in kept] == ['failed']

    def test_passed_wins_over_skipped(self):
        script = _load_script()
        tests = [
            _test('OCP-9:a:Windows_Containers:[sig-windows] y', 'skipped'),
            _test('OCP-9:a:Windows_Containers:[sig-windows] y', 'passed'),
        ]

        kept = script.aggregate_by_test_id(tests)

        assert [t['status'] for t in kept] == ['passed']

    def test_distinct_ids_are_preserved(self):
        script = _load_script()
        tests = [
            _test('OCP-1:a:Windows_Containers:[sig-windows] x', 'failed'),
            _test('OCP-2:a:Windows_Containers:[sig-windows] y', 'passed'),
            _test('OCP-3:a:Windows_Containers:[sig-windows] z', 'skipped'),
        ]

        kept = script.aggregate_by_test_id(tests)

        assert len(kept) == 3
        assert sorted(t['status'] for t in kept) == ['failed', 'passed', 'skipped']

    def test_negative_non_ocp_names_keep_distinct_identity(self):
        script = _load_script()
        tests = [
            _test(': verify node count should match or exceed machine count',
                  'passed'),
            _test(': destroy should succeed', 'passed'),
        ]

        kept = script.aggregate_by_test_id(tests)

        assert len(kept) == 2


class TestDescribeTest:
    """The stored identity follows the dashboard conventions."""

    def test_ocp_id_and_description(self):
        script = _load_script()
        test_id, description = script.describe_test(
            ': OCP-56354:jfrancoa:Windows_Containers:[sig-windows] '
            'Windows_Containers Stop dependent services [Disruptive] '
            '[Serial]')
        assert test_id == 'OCP-56354'
        assert description == 'Stop dependent services'

    def test_no_ocp_id_returns_raw_name(self):
        script = _load_script()
        test_id, description = script.describe_test(': destroy should succeed')
        assert test_id == 'destroy should succeed'
        assert description == 'destroy should succeed'


class TestTlsVerification:
    """The script must not trust an arbitrary peer while sending the token."""

    def test_no_verification_bypass_in_source(self):
        with open(SCRIPT_PATH) as f:
            source = f.read()
        assert 'CERT_NONE' not in source
        assert 'check_hostname=False' not in source
