"""Tests for AI failure analyzer.

Validates pre-classifiers (SSH, DNS, quota), confidence thresholds,
is_product_bug derivation, and response parsing.
"""

import json
import pytest

from src.ai.analyzer import (
    detect_ssh_flake,
    detect_infra_flake,
    detect_timeout_flake,
    detect_known_flaky_test,
    _apply_confidence_review,
    _derive_is_product_bug,
    LOW_CONFIDENCE_THRESHOLD,
    HybridFailureAnalyzer,
)


class TestDetectSshFlake:
    """Tests for SSH flake pre-classifier."""

    def test_ssh_exit_status_255(self):
        result = detect_ssh_flake("exit status 255", pass_rate=90.0)
        assert result is not None
        assert result['classification'] == 'transient'
        assert result['is_product_bug'] is False
        assert result['pre_classifier'] == 'ssh_flake_detector'

    def test_ssh_connection_refused(self):
        result = detect_ssh_flake(
            "ssh: connection refused to host", pass_rate=80.0
        )
        assert result is not None
        assert result['failure_type'] == 'transient'

    def test_ssh_bastion_timeout(self):
        result = detect_ssh_flake("bastion timed out", pass_rate=85.0)
        assert result is not None
        assert result['component'] == 'test-infrastructure (SSH connectivity)'

    def test_no_ssh_pattern(self):
        result = detect_ssh_flake("pod crashed with OOMKilled")
        assert result is None

    def test_empty_message(self):
        result = detect_ssh_flake("")
        assert result is None

    def test_none_message(self):
        result = detect_ssh_flake(None)
        assert result is None

    def test_low_pass_rate_skips(self):
        """Low pass rate means failure is likely not transient."""
        result = detect_ssh_flake("exit status 255", pass_rate=50.0)
        assert result is None

    def test_assertion_without_ssh_in_error_skips(self):
        """Assertion in error but SSH only in logs should skip."""
        msg = "Expected pod to be running\no.Expect(status)"
        result = detect_ssh_flake(msg, pass_rate=90.0)
        assert result is None

    def test_ssh_exit_status_1(self):
        """SSH with non-255 exit status should still match."""
        result = detect_ssh_flake(
            "ssh command failed: exit status 1", pass_rate=90.0
        )
        assert result is not None
        assert result['classification'] == 'transient'

    def test_ssh_command_timed_out(self):
        """SSH command timeout should match."""
        result = detect_ssh_flake(
            "ssh command timed out after 49 minutes", pass_rate=85.0
        )
        assert result is not None
        assert result['failure_type'] == 'transient'

    def test_ssh_connection_closed(self):
        """SSH connection closed should match."""
        result = detect_ssh_flake(
            "ssh: connection closed by remote host", pass_rate=90.0
        )
        assert result is not None

    def test_dial_tcp_22_io_timeout(self):
        """TCP dial to port 22 with i/o timeout should match."""
        result = detect_ssh_flake(
            "dial tcp 192.0.2.1:22: i/o timeout", pass_rate=88.0
        )
        assert result is not None
        assert result['classification'] == 'transient'

    def test_ssh_exit_status_no_false_positive(self):
        """Non-SSH exit status should not match SSH patterns."""
        result = detect_ssh_flake(
            "command failed: exit status 1", pass_rate=90.0
        )
        assert result is None


class TestDetectTimeoutFlake:
    """Tests for timeout/precondition flake pre-classifier."""

    def test_provisioning_phase_timeout(self):
        """Precondition timeout waiting for Provisioning phase."""
        msg = (
            "Failed to check Windows machine should be in "
            "Provisioning phase and not reconciled after waiting "
            "up to 5 minutes"
        )
        result = detect_timeout_flake(msg, pass_rate=90.0)
        assert result is not None
        assert result['classification'] == 'transient'
        assert result['is_product_bug'] is False
        assert result['pre_classifier'] == 'timeout_flake_detector'

    def test_waiting_up_to_n_minutes(self):
        """Generic 'waiting up to N minutes' with high pass rate."""
        result = detect_timeout_flake(
            "condition not met after waiting up to 10 minutes",
            pass_rate=85.0,
        )
        assert result is not None
        assert result['failure_type'] == 'transient'

    def test_timed_out_waiting_for_node(self):
        """Timeout waiting for node readiness."""
        result = detect_timeout_flake(
            "timed out waiting for node to become ready",
            pass_rate=80.0,
        )
        assert result is not None
        assert result['classification'] == 'transient'

    def test_context_deadline_exceeded(self):
        """Context deadline exceeded pattern."""
        result = detect_timeout_flake(
            "context deadline exceeded", pass_rate=75.0
        )
        assert result is not None
        assert result['pre_classifier'] == 'timeout_flake_detector'

    def test_did_not_become_ready(self):
        """Did not become ready within timeout."""
        result = detect_timeout_flake(
            "pod did not become ready within 5m0s", pass_rate=92.0
        )
        assert result is not None

    def test_low_pass_rate_skips(self):
        """Low pass rate should not pre-classify as transient."""
        result = detect_timeout_flake(
            "timed out waiting for machine to become ready",
            pass_rate=40.0,
        )
        assert result is None

    def test_borderline_pass_rate_classifies(self):
        """66.7% pass rate (2/3 passing) should still pre-classify."""
        result = detect_timeout_flake(
            "Failed to check Windows machine should be in "
            "Provisioning phase and not reconciled after waiting "
            "up to 5 minutes",
            pass_rate=66.7,
        )
        assert result is not None
        assert result['classification'] == 'transient'
        assert 'WINC-1931' in result['suggested_action']

    def test_cert_rotation_timeout(self):
        """Kubelet CA rotation timeout should match."""
        result = detect_timeout_flake(
            "Windows nodes remained not ready after 10-minute "
            "timeout following kubelet CA certificate rotation",
            pass_rate=75.0,
        )
        assert result is not None
        assert result['classification'] == 'transient'
        assert 'WINC-1931' in result['suggested_action']

    def test_not_ready_after_minutes(self):
        """Node not ready after N minutes should match."""
        result = detect_timeout_flake(
            "node was not ready after 10-minute timeout",
            pass_rate=80.0,
        )
        assert result is not None

    def test_service_expected_running_got_stopped(self):
        """OCP-84267: service Expected Running Got Stopped during cert rotation."""
        result = detect_timeout_flake(
            'Expected: "Running"\n    Got: "Stopped\\r\\n"',
            pass_rate=75.0,
        )
        assert result is not None
        assert result['classification'] == 'transient'
        assert 'WINC-1931' in result['suggested_action']

    def test_timed_out_waiting_for_the_condition(self):
        """OCP-84267: 'timed out waiting for the condition' with article."""
        result = detect_timeout_flake(
            "timed out waiting for the condition",
            pass_rate=87.0,
        )
        assert result is not None
        assert result['classification'] == 'transient'
        assert 'WINC-1931' in result['suggested_action']

    def test_csi_daemonset_not_ready_5m0s(self):
        """OCP-66352: CSI daemonset 'not ready after waiting up to 5m0s'."""
        result = detect_timeout_flake(
            "Windows CSI Driver vmware-vsphere-csi-driver-node-windows "
            "daemonset is not ready after waiting up to 5m0s minutes",
            pass_rate=50.0,
        )
        assert result is not None
        assert result['classification'] == 'transient'
        assert 'WINC-1931' in result['suggested_action']

    def test_no_pass_rate_skips(self):
        """Missing pass rate should not pre-classify."""
        result = detect_timeout_flake(
            "timed out waiting for node to become ready",
            pass_rate=None,
        )
        assert result is None

    def test_assertion_with_timeout_skips(self):
        """Timeout with assertion failure should fall through to AI."""
        msg = (
            "timed out waiting for condition\n"
            "Expected pod to be running\n"
            "o.Expect(status)"
        )
        result = detect_timeout_flake(msg, pass_rate=90.0)
        assert result is None

    def test_no_timeout_pattern(self):
        """Non-timeout error should not match."""
        result = detect_timeout_flake(
            "pod crashed with OOMKilled", pass_rate=90.0
        )
        assert result is None

    def test_empty_message(self):
        result = detect_timeout_flake("", pass_rate=90.0)
        assert result is None

    def test_none_message(self):
        result = detect_timeout_flake(None, pass_rate=90.0)
        assert result is None

    def test_product_assertion_not_matched(self):
        """Real product assertion with timeout wording should not match
        when assertion patterns are present."""
        msg = (
            "Expected \"Running\"\nGot: \"Stopped\\r\\n\"\n"
            "Unexpected error: unexpected output"
        )
        result = detect_timeout_flake(msg, pass_rate=90.0)
        assert result is None


class TestDetectInfraFlake:
    """Tests for DNS and quota pre-classifiers."""

    def test_dns_no_such_host(self):
        result = detect_infra_flake("dial tcp: lookup foo: no such host")
        assert result is not None
        assert result['classification'] == 'system_issue'
        assert result['pre_classifier'] == 'dns_flake_detector'
        assert result['is_product_bug'] is False

    def test_dns_temporary_failure(self):
        result = detect_infra_flake(
            "Temporary failure in name resolution"
        )
        assert result is not None
        assert result['failure_type'] == 'system_issue'

    def test_quota_exceeded(self):
        result = detect_infra_flake("Error: quota exceeded for project")
        assert result is not None
        assert result['classification'] == 'system_issue'
        assert result['pre_classifier'] == 'quota_detector'

    def test_insufficient_capacity(self):
        result = detect_infra_flake(
            "InsufficientInstanceCapacity: not enough capacity"
        )
        assert result is not None
        assert result['failure_type'] == 'system_issue'

    def test_broad_limit_exceeded_not_matched(self):
        """Generic 'limit exceeded' should not match quota patterns."""
        result = detect_infra_flake("timeout limit exceeded")
        assert result is None

    def test_resource_limit_exceeded_matched(self):
        """Resource-qualified 'limit exceeded' should match."""
        result = detect_infra_flake("cpu limit exceeded for instance")
        assert result is not None
        assert result['pre_classifier'] == 'quota_detector'

    def test_server_misbehaving_without_lookup_not_matched(self):
        """Plain 'server misbehaving' should not match DNS patterns."""
        result = detect_infra_flake("API server misbehaving")
        assert result is None

    def test_server_misbehaving_with_lookup_matched(self):
        """DNS-context 'lookup ... server misbehaving' should match."""
        result = detect_infra_flake(
            "dial tcp: lookup api.cluster on 10.0.0.1:53: server misbehaving"
        )
        assert result is not None
        assert result['pre_classifier'] == 'dns_flake_detector'

    def test_no_infra_pattern(self):
        result = detect_infra_flake("assertion failed: expected 3, got 5")
        assert result is None

    def test_empty_message(self):
        result = detect_infra_flake("")
        assert result is None

    def test_none_message(self):
        result = detect_infra_flake(None)
        assert result is None

    def test_dns_with_assertion_only_in_error_skips(self):
        """DNS only in logs + assertion in error message should skip."""
        msg = "Expected pod to be ready\no.Expect(status)"
        result = detect_infra_flake(msg)
        assert result is None


class TestApplyConfidenceReview:
    """Tests for confidence threshold flagging."""

    def test_low_confidence_flagged(self):
        analysis = {'confidence': 40, 'is_product_bug': False}
        result = _apply_confidence_review(analysis)
        assert result['needs_human_review'] is True
        assert 'Low confidence' in result['review_reason']

    def test_high_confidence_not_flagged(self):
        analysis = {'confidence': 85, 'is_product_bug': False}
        result = _apply_confidence_review(analysis)
        assert result['needs_human_review'] is False

    def test_threshold_boundary_flagged(self):
        analysis = {'confidence': LOW_CONFIDENCE_THRESHOLD - 1}
        result = _apply_confidence_review(analysis)
        assert result['needs_human_review'] is True

    def test_threshold_boundary_not_flagged(self):
        analysis = {'confidence': LOW_CONFIDENCE_THRESHOLD}
        result = _apply_confidence_review(analysis)
        assert result['needs_human_review'] is False

    def test_low_confidence_clears_product_bug(self):
        analysis = {
            'confidence': 30,
            'is_product_bug': True,
        }
        result = _apply_confidence_review(analysis)
        assert result['is_product_bug'] is False
        assert 'Product bug flag cleared' in result['review_reason']

    def test_missing_confidence_treated_as_zero(self):
        analysis = {}
        result = _apply_confidence_review(analysis)
        assert result['needs_human_review'] is True

    def test_preserves_existing_review_reason(self):
        """Existing review_reason should not be overwritten."""
        analysis = {
            'confidence': 30,
            'review_reason': 'AI response could not be parsed as JSON.',
        }
        result = _apply_confidence_review(analysis)
        assert 'AI response could not be parsed as JSON.' in result['review_reason']
        assert 'Low confidence' in result['review_reason']


class TestDeriveIsProductBug:
    """Tests for is_product_bug derivation."""

    def test_product_bug_high_confidence(self):
        analysis = {
            'classification': 'product_bug',
            'confidence': 85,
        }
        result = _derive_is_product_bug(analysis)
        assert result['is_product_bug'] is True

    def test_product_bug_low_confidence(self):
        analysis = {
            'classification': 'product_bug',
            'confidence': 40,
        }
        result = _derive_is_product_bug(analysis)
        assert result['is_product_bug'] is False

    def test_transient_not_product_bug(self):
        analysis = {
            'classification': 'transient',
            'confidence': 95,
        }
        result = _derive_is_product_bug(analysis)
        assert result['is_product_bug'] is False

    def test_system_issue_not_product_bug(self):
        analysis = {
            'classification': 'system_issue',
            'confidence': 90,
        }
        result = _derive_is_product_bug(analysis)
        assert result['is_product_bug'] is False

    def test_automation_bug_not_product_bug(self):
        analysis = {
            'classification': 'automation_bug',
            'confidence': 80,
        }
        result = _derive_is_product_bug(analysis)
        assert result['is_product_bug'] is False

    def test_uses_failure_type_fallback(self):
        analysis = {
            'failure_type': 'product_bug',
            'confidence': 85,
        }
        result = _derive_is_product_bug(analysis)
        assert result['is_product_bug'] is True


class TestParseAnalysisResponse:
    """Tests for AI response JSON parsing."""

    def test_plain_json(self):
        response = json.dumps({
            'root_cause': 'Pod crashed',
            'failure_type': 'product_bug',
            'confidence': 80,
        })
        result = HybridFailureAnalyzer._parse_analysis_response(
            response, 'aws'
        )
        assert result['root_cause'] == 'Pod crashed'
        assert result['classification'] == 'product_bug'

    def test_json_in_code_fence(self):
        response = '```json\n{"root_cause": "DNS fail", "failure_type": "system_issue"}\n```'
        result = HybridFailureAnalyzer._parse_analysis_response(
            response, 'gcp'
        )
        assert result['root_cause'] == 'DNS fail'
        assert result['classification'] == 'system_issue'

    def test_json_in_plain_code_fence(self):
        response = '```\n{"root_cause": "test bug", "failure_type": "automation_bug"}\n```'
        result = HybridFailureAnalyzer._parse_analysis_response(
            response, 'azure'
        )
        assert result['root_cause'] == 'test bug'

    def test_unparseable_response(self):
        response = "I cannot determine the cause of this failure."
        result = HybridFailureAnalyzer._parse_analysis_response(
            response, 'vsphere'
        )
        assert result['classification'] == 'to_investigate'
        assert result['confidence'] == 30
        assert result['needs_human_review'] is True
        assert 'vsphere' in result['affected_platforms']

    def test_empty_response(self):
        result = HybridFailureAnalyzer._parse_analysis_response(
            '', 'aws'
        )
        assert result['classification'] == 'to_investigate'

    def test_classification_mapped_from_failure_type(self):
        response = json.dumps({
            'root_cause': 'Flaky',
            'failure_type': 'transient',
        })
        result = HybridFailureAnalyzer._parse_analysis_response(
            response, 'aws'
        )
        assert result['classification'] == 'transient'


class TestAnalyzeFailureIntegration:
    """Integration tests for the full analyze_failure flow."""

    def test_ssh_flake_skips_vertex_ai(self):
        """SSH flake should be pre-classified without calling Vertex AI."""
        analyzer = HybridFailureAnalyzer()
        result = analyzer.analyze_failure(
            test_name='OCP-12345',
            error_message='SSH attempt 3 failed with exit status 255',
            log_url='',
            platform='aws',
            version='4.22',
            pass_rate=90.0,
        )
        assert result['pre_classified'] is True
        assert result['classification'] == 'transient'
        assert result['cost'] == 0.0

    def test_dns_flake_skips_vertex_ai(self):
        """DNS failure should be pre-classified."""
        analyzer = HybridFailureAnalyzer()
        result = analyzer.analyze_failure(
            test_name='OCP-99999',
            error_message='dial tcp: lookup api.cluster: no such host',
            log_url='',
            platform='gcp',
            version='4.23',
        )
        assert result['pre_classified'] is True
        assert result['classification'] == 'system_issue'
        assert result['cost'] == 0.0

    def test_quota_flake_skips_vertex_ai(self):
        """Quota exceeded should be pre-classified."""
        analyzer = HybridFailureAnalyzer()
        result = analyzer.analyze_failure(
            test_name='OCP-88888',
            error_message='QUOTA_EXCEEDED: cpu quota exceeded',
            log_url='',
            platform='gcp',
            version='4.22',
        )
        assert result['pre_classified'] is True
        assert result['classification'] == 'system_issue'
        assert result['pre_classifier'] == 'quota_detector'

    def test_timeout_flake_skips_vertex_ai(self):
        """Timeout flake should be pre-classified without Vertex AI."""
        analyzer = HybridFailureAnalyzer()
        result = analyzer.analyze_failure(
            test_name='OCP-55555',
            error_message=(
                'Failed to check Windows machine should be in '
                'Provisioning phase and not reconciled after '
                'waiting up to 5 minutes'
            ),
            log_url='',
            platform='gcp',
            version='X.Y',
            pass_rate=90.0,
        )
        assert result['pre_classified'] is True
        assert result['classification'] == 'transient'
        assert result['cost'] == 0.0
        assert result['pre_classifier'] == 'timeout_flake_detector'

    def test_timeout_low_pass_rate_falls_through(self):
        """Timeout with low pass rate should NOT be pre-classified."""
        analyzer = HybridFailureAnalyzer()
        result = analyzer.analyze_failure(
            test_name='OCP-77777',
            error_message='timed out waiting for machine to become ready',
            log_url='',
            platform='azure',
            version='4.18',
            pass_rate=40.0,
        )
        # Should fall through to Vertex AI (which fails without client)
        assert result.get('pre_classified') is not True

    def test_known_flaky_test_skips_vertex_ai(self):
        """Cert rotation test with corrupted output caught by test name."""
        analyzer = HybridFailureAnalyzer()
        result = analyzer.analyze_failure(
            test_name='OCP-50924',
            error_message='CharChunk corrupted output garbage',
            log_url='',
            platform='vsphere',
            version='5.0',
            pass_rate=60.0,
            test_description='Windows instances react to kubelet CA rotation',
        )
        assert result['pre_classified'] is True
        assert result['pre_classifier'] == 'known_flaky_test_detector'
        assert 'WINC-1931' in result['suggested_action']
        assert result['cost'] == 0.0

    def test_no_client_returns_failed(self):
        """Without Vertex AI client, non-infra failures return error."""
        analyzer = HybridFailureAnalyzer()
        result = analyzer.analyze_failure(
            test_name='OCP-11111',
            error_message='pod CrashLoopBackOff',
            log_url='',
            platform='aws',
            version='4.22',
        )
        assert result['analysis_mode'] == 'failed'
        assert result['needs_human_review'] is True


class TestDetectKnownFlakyTest:
    """Tests for test-name-based pre-classifier."""

    def test_kubelet_ca_rotation(self):
        result = detect_known_flaky_test(
            'OCP-50924',
            'Windows instances react to kubelet CA rotation',
            pass_rate=60.0,
        )
        assert result is not None
        assert result['pre_classifier'] == 'known_flaky_test_detector'
        assert 'WINC-1931' in result['suggested_action']

    def test_cert_rotation(self):
        result = detect_known_flaky_test(
            'OCP-84267',
            'Certificate rotation test for hybrid overlay',
            pass_rate=75.0,
        )
        assert result is not None
        assert result['pre_classifier'] == 'known_flaky_test_detector'

    def test_ca_rotation(self):
        result = detect_known_flaky_test(
            'OCP-99999',
            'Windows nodes handle CA rotation gracefully',
            pass_rate=80.0,
        )
        assert result is not None

    def test_no_match_normal_test(self):
        result = detect_known_flaky_test(
            'OCP-11111',
            'Windows pod networking basic connectivity',
            pass_rate=90.0,
        )
        assert result is None

    def test_low_pass_rate_not_classified(self):
        result = detect_known_flaky_test(
            'OCP-50924',
            'Windows instances react to kubelet CA rotation',
            pass_rate=30.0,
        )
        assert result is None

    def test_no_pass_rate_not_classified(self):
        result = detect_known_flaky_test(
            'OCP-50924',
            'Windows instances react to kubelet CA rotation',
            pass_rate=None,
        )
        assert result is None

    def test_name_contains_rotation_keyword(self):
        result = detect_known_flaky_test(
            'cert rotation during upgrade',
            '',
            pass_rate=70.0,
        )
        assert result is not None
