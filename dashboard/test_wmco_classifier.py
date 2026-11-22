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
    """The readiness race stays an automation bug."""

    def test_real_failure_is_automation_bug(self):
        result = detect_wmco_not_ready(WMCO_NOT_READY)
        assert result is not None
        assert result['failure_type'] == 'automation_bug'
        assert result['is_product_bug'] is False

    def test_blames_the_missing_wait(self):
        result = detect_wmco_not_ready(WMCO_NOT_READY)
        assert 'wait' in result['suggested_action'].lower()

    def test_pod_after_operator_in_text(self):
        """Order of the two markers in the message must not matter."""
        text = ('container "manager" is waiting to start: '
                'ContainerCreating for deployment '
                'windows-machine-config-operator')
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
        assert result['failure_type'] == 'automation_bug'

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
