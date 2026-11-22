"""Tests for the WMCO toolchain / readiness pre-classifiers.

OCP-37362 produces two distinct failures that used to share one label:

* ``Unmatching golang version`` - the operator was built with a different
  Go minor than the payload. That is a product/build defect and the whole
  point of the test, so labelling it ``automation_bug`` is a false
  negative.
* ``ContainerCreating`` on the WMCO pod - the test read the operator logs
  before the pod started. That is a test-side race.

These tests pin both directions, plus the negative cases that must still
reach the AI (AGENTS rule 8).
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from ai import analyzer as analyzer_module
from ai.analyzer import (
    HybridFailureAnalyzer,
    detect_wmco_golang_version_mismatch,
    detect_wmco_not_ready,
)

# Real failure text from build 2123845171324915712 (aws, 4.22), trimmed.
GOLANG_MISMATCH = '''fail [github.com/openshift/openshift-tests-private/test/extended/winc/winc.go:1053]: Unmatching golang version
I1120 20:58:00.468267 2355 winc.go:1047] Golang version is: "go1.26.10 (Red Hat 1.26.10-1.el9_8) X:strictfipsruntime"
I1120 20:58:00.468303 2355 winc.go:1048] Golang version truncated is: "go1.26
STEP: Compare fetched version with WMCO log version'''

# Real failure text from build 2123845171408801792 (vsphere, 4.22), trimmed.
WMCO_NOT_READY = '''fail [winc.go:1051]: Unexpected error:
Error running oc --kubeconfig=/tmp/kubeconfig-1 logs deployment.apps/windows-machine-config-operator -n openshift-windows-machine-config-operator:
Error from server (BadRequest): container "manager" in pod "windows-machine-config-operator-86df5ddf57-hxgbj" is waiting to start: ContainerCreating'''


class TestGolangMismatchDetected:
    """The product-side signature must be classified as a product bug."""

    def test_real_failure_is_product_bug(self):
        result = detect_wmco_golang_version_mismatch(GOLANG_MISMATCH)
        assert result is not None
        assert result['failure_type'] == 'product_bug'
        assert result['classification'] == 'product_bug'
        assert result['is_product_bug'] is True

    def test_flags_for_human_review(self):
        """The operator-side version is not in the message, so a human
        must confirm before a bug is filed."""
        result = detect_wmco_golang_version_mismatch(GOLANG_MISMATCH)
        assert result['needs_human_review'] is True
        assert 'operator' in result['review_reason'].lower()

    def test_does_not_blame_the_test(self):
        """Regression: this used to be reported as automation_bug."""
        result = detect_wmco_golang_version_mismatch(GOLANG_MISMATCH)
        assert 'test' not in result['issue_title'].lower() or \
            'not a defect in the test' in result['root_cause']

    def test_names_the_toolchain_fix(self):
        result = detect_wmco_golang_version_mismatch(GOLANG_MISMATCH)
        assert 'rhel_9_1.26' in result['suggested_action']

    def test_marker_only_variant(self):
        """The bare phrase is enough when the stack line is trimmed."""
        result = detect_wmco_golang_version_mismatch(
            'Unmatching golang version')
        assert result is not None
        assert result['failure_type'] == 'product_bug'

    def test_source_line_variant(self):
        """winc.go:1073 is the older line number for the same assertion."""
        result = detect_wmco_golang_version_mismatch(
            'fail [winc.go:1073]: Unmatching golang version')
        assert result is not None


class TestGolangMismatchNotDetected:
    """Negative cases that must still reach the AI."""

    def test_empty_message(self):
        assert detect_wmco_golang_version_mismatch('') is None

    def test_none_message(self):
        assert detect_wmco_golang_version_mismatch(None) is None

    def test_benign_version_log_line(self):
        """A passing run logs the version; that is not a mismatch."""
        benign = ('I1120 winc.go:1047] Golang version is: "go1.26.10 '
                  '(Red Hat 1.26.10-1.el9_8) X:strictfipsruntime"')
        assert detect_wmco_golang_version_mismatch(benign) is None

    def test_unrelated_version_comparison(self):
        assert detect_wmco_golang_version_mismatch(
            'expected version 4.22.1 but got 4.22.0') is None

    def test_readiness_race_is_not_a_mismatch(self):
        """The other OCP-37362 mode must not be labelled product_bug."""
        assert detect_wmco_golang_version_mismatch(WMCO_NOT_READY) is None


class TestWmcoNotReadyDetected:
    """An unavailable operator pod is an environment issue, not a bug.

    Verified on build 2123845171408801792: a master-0 drain evicted the
    pod at 19:39:10.932, the replacement pulled its image for 22s, and the
    test's `oc logs` ran at 19:39:26.668 - eight seconds before the
    container existed. The operator was healthy at 19:39:35.
    """

    def test_real_failure_is_infrastructure(self):
        result = detect_wmco_not_ready(WMCO_NOT_READY)
        assert result is not None
        assert result['failure_type'] == 'system_issue'
        assert result['is_product_bug'] is False

    def test_not_classified_as_a_defect(self):
        """Regression: it was automation_bug, which sent the failure to
        the wrong owner for a legitimately unavailable environment."""
        result = detect_wmco_not_ready(WMCO_NOT_READY)
        assert result['failure_type'] not in ('product_bug', 'automation_bug')

    def test_mentions_the_test_brittleness_as_secondary(self):
        result = detect_wmco_not_ready(WMCO_NOT_READY)
        action = result['suggested_action'].lower()
        assert 'no product bug to file' in action
        assert 'wait' in action
        assert 'ote twin' in action

    def test_drain_and_pull_signals_are_reported(self):
        text = WMCO_NOT_READY + '\nDraining node to update config.'
        result = detect_wmco_not_ready(text)
        assert 'eviction/drain observed' in result['evidence']

    def test_pod_after_state_in_text(self):
        """Order of the two markers in the message must not matter."""
        text = ('container "manager" is waiting to start: '
                'ContainerCreating for pod '
                '"windows-machine-config-operator-86df5ddf57-hxgbj"')
        assert detect_wmco_not_ready(text) is not None


class TestWmcoNotReadyNotDetected:
    """Negative cases: other pods, other errors."""

    def test_empty_message(self):
        assert detect_wmco_not_ready('') is None

    def test_container_creating_for_another_component(self):
        """A different pod stuck in ContainerCreating may be a real
        cluster problem and must not be pre-classified as a test race."""
        other = ('container "manager" in pod "prometheus-k8s-0" is '
                 'waiting to start: ContainerCreating')
        assert detect_wmco_not_ready(other) is None

    def test_other_pod_in_the_wmco_namespace(self):
        """Negative: the namespace name alone must not match. Every error
        from that namespace carries `-n openshift-windows-machine-config-
        operator`, so matching the namespace would label an unrelated
        pod's startup problem as a WMCO test race."""
        other = ('container "manager" in pod "diagnostic-helper" is waiting '
                 'to start: ContainerCreating\n'
                 'command: oc logs -n openshift-windows-machine-config-operator')
        assert detect_wmco_not_ready(other) is None

    def test_pod_name_needs_the_hash_suffix(self):
        """The deployment name alone is not a pod reference."""
        assert detect_wmco_not_ready(
            'ContainerCreating for deployment/windows-machine-config-operator'
        ) is None

    def test_golang_mismatch_is_not_a_readiness_race(self):
        assert detect_wmco_not_ready(GOLANG_MISMATCH) is None

    def test_pod_name_without_state(self):
        assert detect_wmco_not_ready(
            'windows-machine-config-operator deployment is available') is None


class TestPrecedence:
    """A message carrying both signals must resolve to the product bug."""

    def test_product_bug_wins(self, monkeypatch):
        monkeypatch.setenv('ENMAAS_KEY', 'test-key')
        combined = GOLANG_MISMATCH + '\n' + WMCO_NOT_READY
        analyzer = HybridFailureAnalyzer()
        result = analyzer.analyze_failure(
            'OCP-37362', combined, log_url='', platform='aws', version='4.22')
        assert result['failure_type'] == 'product_bug'
        assert result['pre_classifier'] == 'wmco_golang_mismatch_detector'


class TestPreClassifiersSkipTheAi:
    """Both signatures are answered offline, without a model call."""

    def test_golang_mismatch_skips_ai(self, monkeypatch):
        monkeypatch.setenv('ENMAAS_KEY', 'test-key')
        analyzer = HybridFailureAnalyzer()
        with patch.object(analyzer_module.requests, 'post') as post:
            result = analyzer.analyze_failure(
                'OCP-37362', GOLANG_MISMATCH, log_url='',
                platform='aws', version='4.22')
        post.assert_not_called()
        assert result['analysis_mode'] == 'pre-classifier'
        assert result['cost'] == 0.0

    def test_readiness_race_skips_ai(self, monkeypatch):
        monkeypatch.setenv('ENMAAS_KEY', 'test-key')
        analyzer = HybridFailureAnalyzer()
        with patch.object(analyzer_module.requests, 'post') as post:
            result = analyzer.analyze_failure(
                'OCP-37362', WMCO_NOT_READY, log_url='',
                platform='vsphere', version='4.22')
        post.assert_not_called()
        assert result['failure_type'] == 'system_issue'

    def test_force_ai_still_bypasses(self, monkeypatch):
        """Re-analyze must still be able to ask the model."""
        monkeypatch.setenv('ENMAAS_KEY', 'test-key')
        analyzer = HybridFailureAnalyzer()
        # The model call fails (no stub), which proves force_ai reached
        # the AI path instead of returning the pre-classifier verdict.
        with patch.object(analyzer_module.requests, 'post',
                          side_effect=analyzer_module.requests.exceptions.Timeout('stub')):
            result = analyzer.analyze_failure(
                'OCP-37362', GOLANG_MISMATCH, log_url='', platform='aws',
                version='4.22', force_ai=True)
        assert result.get('pre_classifier') != 'wmco_golang_mismatch_detector'


class TestReviewFlagSurvivesTheCache:
    """A cached analysis must answer the same as a fresh one: the review
    verdict has to be persisted (CodeRabbit finding on #293)."""

    @pytest.fixture
    def db(self, tmp_path):
        from storage.database import DashboardDatabase
        return DashboardDatabase(str(tmp_path / "cache.db"))

    def _analysis(self, **overrides):
        base = {
            'root_cause': 'operator built with a different Go minor',
            'component': 'windows-machine-config-operator (build toolchain)',
            'confidence': 90,
            'failure_type': 'product_bug',
            'analysis_mode': 'pre-classifier',
            'cost': 0.0,
            'needs_human_review': True,
            'review_reason': 'confirm the operator-side Go version',
        }
        base.update(overrides)
        return base

    def test_needs_human_review_round_trips(self, db):
        db.save_ai_analysis('OCP-37362', '4.22', None,
                            analysis=self._analysis())
        cached = db.get_ai_analysis('OCP-37362', '4.22')
        assert cached is not None
        assert cached['needs_human_review'] is True
        assert cached['review_reason'] == 'confirm the operator-side Go version'

    def test_absent_flag_stays_false(self, db):
        """Negative: an analysis without the flag must not inherit True."""
        db.save_ai_analysis('OCP-99999', '4.22', None,
                            analysis=self._analysis(needs_human_review=False,
                                                    review_reason=None))
        cached = db.get_ai_analysis('OCP-99999', '4.22')
        assert cached['needs_human_review'] is False
        assert 'review_reason' not in cached

    def test_low_confidence_result_keeps_its_review_flag(self, db):
        """The finding is not WMCO-specific: every analysis that sets the
        flag must round-trip."""
        db.save_ai_analysis('OCP-11111', '4.21', None,
                            analysis=self._analysis(
                                failure_type='unknown', confidence=30,
                                analysis_mode='glm-ai'))
        cached = db.get_ai_analysis('OCP-11111', '4.21')
        assert cached['needs_human_review'] is True

    def test_old_database_gets_the_columns(self, tmp_path):
        """A pre-existing database must be migrated, not crashed on."""
        import sqlite3
        path = str(tmp_path / "old.db")
        conn = sqlite3.connect(path)
        conn.execute("""CREATE TABLE ai_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_name TEXT NOT NULL, version TEXT NOT NULL, platform TEXT,
            analysis_date DATETIME NOT NULL, root_cause TEXT, component TEXT,
            confidence INTEGER, failure_type TEXT, platform_specific INTEGER,
            affected_platforms TEXT, evidence TEXT, suggested_action TEXT,
            issue_title TEXT, issue_description TEXT, analysis_mode TEXT,
            cost REAL, error_message_hash TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(test_name, version, analysis_date))""")
        conn.commit()
        conn.close()

        from storage.database import DashboardDatabase
        db = DashboardDatabase(path)          # runs the migration
        db.save_ai_analysis('OCP-22222', '4.22', None,
                            analysis=self._analysis())
        cached = db.get_ai_analysis('OCP-22222', '4.22')
        assert cached['needs_human_review'] is True


class TestPrecedenceOverSshClassifier:
    """The product signal must outrank the log-based SSH classifier."""

    def test_mismatch_wins_over_an_ssh_line_in_the_logs(self, monkeypatch):
        """CodeRabbit finding: with pass_rate None and 'SSH attempt 1
        failed' in the logs, detect_ssh_flake returned transient before
        the mismatch check ran."""
        monkeypatch.setenv('ENMAAS_KEY', 'test-key')
        monkeypatch.setattr(analyzer_module, '_fetch_logs',
                            lambda url: 'SSH attempt 1 failed: connection refused')
        analyzer = HybridFailureAnalyzer()
        result = analyzer.analyze_failure(
            'OCP-37362', GOLANG_MISMATCH, log_url='https://example.com/log',
            platform='aws', version='4.22')
        assert result['failure_type'] == 'product_bug'
        assert result['pre_classifier'] == 'wmco_golang_mismatch_detector'

    def test_mismatch_does_not_fetch_logs(self, monkeypatch):
        """It only needs the error message, so it must not pay for a fetch."""
        monkeypatch.setenv('ENMAAS_KEY', 'test-key')
        calls = []
        monkeypatch.setattr(analyzer_module, '_fetch_logs',
                            lambda url: calls.append(url) or '')
        analyzer = HybridFailureAnalyzer()
        analyzer.analyze_failure('OCP-37362', GOLANG_MISMATCH,
                                 log_url='https://example.com/log',
                                 platform='aws', version='4.22')
        assert calls == []

    def test_ssh_classification_still_wins_when_no_mismatch(self, monkeypatch):
        """Negative: the reorder must not disable the SSH pre-classifier."""
        monkeypatch.setenv('ENMAAS_KEY', 'test-key')
        monkeypatch.setattr(analyzer_module, '_fetch_logs',
                            lambda url: 'SSH attempt 1 failed')
        analyzer = HybridFailureAnalyzer()
        result = analyzer.analyze_failure(
            'OCP-55555', 'provisioning failed: SSH attempt 1 failed',
            log_url='https://example.com/log', platform='aws', version='4.22')
        assert result['failure_type'] == 'transient'
        assert result['pre_classifier'] == 'ssh_flake_detector'


class TestLegacySchemaRecreatePath:
    """The platform-NOT-NULL migration recreates ai_analyses; the review
    columns must be part of the new table, not only added by ALTER."""

    def test_recreate_path_keeps_review_columns(self, tmp_path):
        import sqlite3
        path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(path)
        conn.execute("""CREATE TABLE ai_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_name TEXT NOT NULL, version TEXT NOT NULL,
            platform TEXT NOT NULL, analysis_date DATETIME NOT NULL,
            root_cause TEXT, component TEXT, confidence INTEGER,
            failure_type TEXT, platform_specific INTEGER,
            affected_platforms TEXT, evidence TEXT, suggested_action TEXT,
            issue_title TEXT, issue_description TEXT, analysis_mode TEXT,
            cost REAL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(test_name, version, analysis_date))""")
        conn.commit()
        conn.close()

        from storage.database import DashboardDatabase
        db = DashboardDatabase(path)
        db.save_ai_analysis('OCP-33333', '4.22', None, analysis={
            'root_cause': 'toolchain mismatch', 'confidence': 90,
            'failure_type': 'product_bug', 'analysis_mode': 'pre-classifier',
            'needs_human_review': True, 'review_reason': 'verify operator Go'})
        cached = db.get_ai_analysis('OCP-33333', '4.22')
        assert cached['needs_human_review'] is True
        assert cached['review_reason'] == 'verify operator Go'
