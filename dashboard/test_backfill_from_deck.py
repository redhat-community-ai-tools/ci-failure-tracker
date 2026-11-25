"""Tests for the deck backfill script.

Covers the two properties that matter before writing rows:

1. Duplicate OCP IDs collapse with failure precedence, so a later passed
   variant cannot replace a failed result through INSERT OR REPLACE.
2. The script never disables TLS verification while sending the bearer
   token.
"""

import importlib.util
import json
import os
from datetime import datetime, timezone
from unittest import mock

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


class TestParseRunUrl:
    """Explicit run URLs are the recovery path for aged-out ProwJobs."""

    URL = ('https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com'
           '/view/gs/qe-private-deck/pr-logs/pull/openshift_release/89342/'
           'rehearse-89342-periodic-ci-openshift-openshift-tests-private-'
           'release-4.22-amd64-nightly-azure-ipi-ovn-winc-zstream-f7/'
           '2125203789886525440')

    def test_splits_base_src_job_and_build(self):
        script = _load_script()
        base, src, job, build = script.parse_run_url(self.URL)

        assert base == 'https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com'
        assert src.startswith('gs/qe-private-deck/pr-logs/pull/')
        assert job.endswith('-azure-ipi-ovn-winc-zstream-f7')
        assert build == '2125203789886525440'

    def test_negative_non_deck_url(self):
        script = _load_script()
        assert script.parse_run_url('https://example.com/job/1') is None


class TestFetchRunMetadata:
    """Start time and result come from the rendered metadata lens."""

    PAGE = ('<div>Spyglass Lens: Metadata Test started 2026-11-24 12:24:11 '
            '&#43;0000 UTC passed after 3h2m21s.</div>')

    def test_reads_started_state_and_duration(self):
        script = _load_script()
        lens = {'0': ['artifacts/x/finished.json']}

        with mock.patch.object(script, 'http_get',
                               return_value=self.PAGE.encode()):
            started, state, duration = script.fetch_run_metadata(
                'https://deck', 'token', 'gs/bucket/path', lens)

        assert started.isoformat() == '2026-11-24T12:24:11+00:00'
        assert state == 'success'
        assert duration == 3 * 3600 + 2 * 60 + 21

    def test_failed_run(self):
        script = _load_script()
        lens = {'0': ['artifacts/x/finished.json']}
        page = ('Test started 2026-11-24 12:24:11 +0000 UTC '
                'failed after 12m0s.')

        with mock.patch.object(script, 'http_get',
                               return_value=page.encode()):
            _, state, duration = script.fetch_run_metadata(
                'https://deck', 'token', 'gs/bucket/path', lens)

        assert state == 'failure'
        assert duration == 12 * 60


class TestRunRecordDeck:
    """Each run record keeps the deck that served it.

    An explicit run URL can point at a different deck than the configured
    one. Lens requests must follow that deck, otherwise the bucket is not
    reachable and the import silently yields nothing.
    """

    def test_prowjob_runs_use_configured_deck(self):
        script = _load_script()
        payload = json.dumps({'items': [{
            'spec': {'job': 'periodic-ci-x-winc-f7'},
            'status': {
                'build_id': '1', 'state': 'success',
                'startTime': '2026-11-25T00:00:00Z',
                'completionTime': '2026-11-25T01:00:00Z',
                'url': 'https://configured.example/view/gs/b/p',
            },
        }]}).encode()

        with mock.patch.object(script, 'http_get', return_value=payload):
            runs = script.runs_from_prowjobs(
                'https://configured.example', 'token',
                datetime(2026, 11, 24, tzinfo=timezone.utc),
                datetime(2026, 11, 26, tzinfo=timezone.utc),
                '*winc*')

        assert runs[0]['deck'] == 'https://configured.example'

    def test_explicit_url_keeps_its_own_deck(self):
        script = _load_script()
        url = ('https://other-deck.example/view/gs/bucket/pr-logs/pull/o_r/1/'
               'rehearse-1-periodic-ci-x-winc-f7/123')

        with mock.patch.object(script, 'fetch_run',
                               return_value=('gs/bucket/path', {})), \
             mock.patch.object(script, 'fetch_run_metadata',
                               return_value=(datetime(2026, 11, 24, tzinfo=timezone.utc),
                                             'success', 60.0)):
            runs = script.runs_from_urls('https://configured.example',
                                         'token', [url])

        assert runs[0]['deck'] == 'https://other-deck.example'
