"""Tests for the test-history evidence handed to the AI.

A bare "0.0%" invited the model to assert a systemic gap without saying
where the number came from. The prompt now always carries the window, the
sample size and the last observed pass/fail - and 0% is stated as
evidence of a persistent defect when the sample supports it.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from ai.analyzer import _build_history_context
from storage.database import DashboardDatabase
from web.server import HISTORY_MIN_SAMPLE, HISTORY_WINDOW_DAYS, create_app

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.yaml')


class TestHistoryContext:
    """The prompt must never see a percentage on its own."""

    def test_no_history_no_block(self):
        assert _build_history_context(None, None) == ""

    def test_zero_rate_with_a_real_sample_is_product_evidence(self):
        text = _build_history_context(0.0, {
            'window_days': 90, 'passed': 0, 'failed': 12, 'total': 12,
            'last_failure': '2026-11-20', 'min_sample': 5})
        assert '0 of 12 runs' in text
        assert 'persistent product or test defect' in text
        # Negative: it must not be softened into a flake
        assert 'transient' in text  # ...only as what NOT to conclude
        assert 'do not classify it as a transient flake' in text

    def test_zero_rate_with_a_small_sample_still_reports_it(self):
        text = _build_history_context(0.0, {
            'window_days': 90, 'passed': 0, 'failed': 2, 'total': 2,
            'min_sample': 5})
        assert '0 of 2' in text
        assert 'small' in text.lower()
        assert 'argue against a one-off flake' in text

    def test_window_and_counts_are_always_stated(self):
        text = _build_history_context(87.5, {
            'window_days': 90, 'passed': 7, 'failed': 1, 'total': 8,
            'last_pass': '2026-08-25', 'min_sample': 5})
        assert '87.5% over the last 90 days' in text
        assert '7 passed / 1 failed' in text
        assert 'last pass 2026-08-25' in text

    def test_high_rate_biases_away_from_longstanding_defect(self):
        text = _build_history_context(95.0, {
            'window_days': 30, 'passed': 19, 'failed': 1, 'total': 20,
            'min_sample': 5})
        assert 'usually passes' in text

    def test_low_but_nonzero_rate_biases_to_persistent(self):
        text = _build_history_context(20.0, {
            'window_days': 30, 'passed': 4, 'failed': 16, 'total': 20,
            'min_sample': 5})
        assert 'fails most of the time' in text
        # the "never transient" instruction stays, once the sample is real
        assert 'do not call it transient' in text
        assert 'do not suggest a retry' in text

    def test_low_rate_with_small_sample_does_not_forbid_transient(self):
        """Negative (CodeRabbit finding on #294): a 1 pass / 3 fail sample
        must not be told to classify as persistent and never transient."""
        text = _build_history_context(25.0, {
            'window_days': 30, 'passed': 1, 'failed': 3, 'total': 4,
            'min_sample': 5})
        assert 'do not call it transient' not in text
        assert 'do not suggest a retry' not in text

    def test_rate_without_counts_still_reports_the_rate(self):
        text = _build_history_context(50.0, {})
        assert '50.0%' in text


class TestServerComputesTheWindow:
    """The server must not use the 7-day display window for history."""

    @pytest.fixture
    def app_and_db(self):
        db_fd, db_path = tempfile.mkstemp(suffix='.db')
        application = create_app(db_path=db_path, config_file=CONFIG_PATH)
        application.config['TESTING'] = True
        db = DashboardDatabase(db_path)
        yield application, db
        db.close()
        os.close(db_fd)
        os.unlink(db_path)

    @staticmethod
    def _insert(db, days_ago, status):
        ts = (datetime.now() - timedelta(days=days_ago)).isoformat()
        db.conn.execute(
            "INSERT INTO test_results (test_name, version, platform, status,"
            " timestamp, error_message, log_url, job_name, build_id)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            ('OCP-37362', '4.22', 'aws', status, ts, 'err', 'https://x/y',
             'job', f'b-{days_ago}-{status}'))
        db.conn.commit()

    def _history(self, app_and_db, monkeypatch, days):
        application, db = app_and_db
        captured = {}
        import ai.analyzer as analyzer_module
        monkeypatch.setattr(analyzer_module, 'HybridFailureAnalyzer',
                            lambda: type('A', (), {
                                'analyze_failure': staticmethod(
                                    lambda **kw: captured.update(kw) or {
                                        'root_cause': 'x', 'failure_type':
                                        'product_bug'})})())
        client = application.test_client()
        client.post('/api/analyze-failure', json={
            'test_name': 'OCP-37362', 'version': '4.22', 'platform': 'aws',
            'days': days, 'use_cached': False,
            'error_message': 'Unmatching golang version'})
        return captured.get('pass_rate_context')

    def test_default_window_is_wider_than_the_display_range(
            self, app_and_db, monkeypatch):
        """An 80-day-old pass must count even when the UI shows 7 days."""
        application, db = app_and_db
        self._insert(db, days_ago=80, status='passed')
        self._insert(db, days_ago=3, status='failed')
        ctx = self._history(app_and_db, monkeypatch, days=7)
        assert ctx is not None
        assert ctx['window_days'] == max(7, HISTORY_WINDOW_DAYS)
        assert ctx['passed'] == 1, "the 80-day-old pass must be in the sample"
        assert ctx['failed'] == 1
        assert ctx['last_pass']

    def test_zero_pass_rate_is_reported_with_counts(self, app_and_db, monkeypatch):
        application, db = app_and_db
        for d in (5, 10, 20):
            self._insert(db, days_ago=d, status='failed')
        ctx = self._history(app_and_db, monkeypatch, days=7)
        assert ctx['passed'] == 0 and ctx['failed'] == 3 and ctx['total'] == 3
        assert ctx['min_sample'] == HISTORY_MIN_SAMPLE

    def test_skipped_results_do_not_affect_the_rate(self, app_and_db, monkeypatch):
        application, db = app_and_db
        self._insert(db, days_ago=5, status='passed')
        self._insert(db, days_ago=6, status='skipped')
        ctx = self._history(app_and_db, monkeypatch, days=7)
        assert ctx['total'] == 1


class TestPromptHasNoUnconditionalRateRules:
    """The rendered prompt must not carry a sample-blind "<50% is
    persistent" rule (CodeRabbit finding on #294)."""

    @staticmethod
    def _prompt(pass_rate, context):
        import ai.analyzer as analyzer_module
        analyzer = analyzer_module.HybridFailureAnalyzer.__new__(
            analyzer_module.HybridFailureAnalyzer)
        analyzer.timeout_seconds = 300
        history = analyzer_module._build_history_context(pass_rate, context)
        return analyzer._build_analysis_prompt(
            'OCP-37362', 'err', 'logs', 'aws', '4.22',
            history_context=history)

    def test_static_never_transient_rule_is_gone(self):
        prompt = self._prompt(None, None)
        assert 'Any failure with pass rate <50%' not in prompt
        assert 'When pass rate is low (<50%)' not in prompt

    def test_small_sample_does_not_get_persistent_instruction(self):
        prompt = self._prompt(25.0, {
            'window_days': 30, 'passed': 1, 'failed': 3, 'total': 4,
            'min_sample': 5})
        assert 'do not call it transient' not in prompt

    def test_large_low_rate_sample_does(self):
        prompt = self._prompt(20.0, {
            'window_days': 30, 'passed': 4, 'failed': 16, 'total': 20,
            'min_sample': 5})
        assert 'do not call it transient' in prompt
