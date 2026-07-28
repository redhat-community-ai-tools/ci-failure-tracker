"""Tests for Jira integration.

Validates issue creation error propagation and configuration.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from src.integrations.jira_integration import (
    JiraIntegration,
    JiraConfig,
    get_jira_integration,
)


@pytest.fixture
def jira_config():
    return JiraConfig(
        url="https://issues.example.com",
        project_key="TEST",
    )


@pytest.fixture
def jira(jira_config):
    with patch.dict(os.environ, {'JIRA_API_TOKEN': 'test-token'}):
        return JiraIntegration(jira_config)


class TestCreateIssueErrorPropagation:
    """Tests that create_issue raises descriptive errors instead of
    returning None, so callers can surface the reason to users."""

    @patch('src.integrations.jira_integration.requests.post')
    def test_raises_on_auth_failure(self, mock_post, jira):
        """401 from Jira API should raise with status and response text."""
        # search returns no existing issue
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            MagicMock(status_code=401, text='Unauthorized'),
        ]

        with pytest.raises(RuntimeError, match="401"):
            jira.create_issue(
                test_name="OCP-1234",
                test_description="desc",
                version="4.22",
            )

    @patch('src.integrations.jira_integration.requests.post')
    def test_raises_on_forbidden(self, mock_post, jira):
        """403 from Jira API should raise with status and response text."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            MagicMock(status_code=403, text='Forbidden'),
        ]

        with pytest.raises(RuntimeError, match="403"):
            jira.create_issue(
                test_name="OCP-1234",
                test_description="desc",
                version="4.22",
            )

    @patch('src.integrations.jira_integration.requests.post')
    def test_raises_on_not_found(self, mock_post, jira):
        """404 from Jira API should raise with status and response text."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            MagicMock(status_code=404, text='Project not found'),
        ]

        with pytest.raises(RuntimeError, match="404"):
            jira.create_issue(
                test_name="OCP-1234",
                test_description="desc",
                version="4.22",
            )

    @patch('src.integrations.jira_integration.requests.post')
    def test_raises_on_network_error(self, mock_post, jira):
        """Network errors should raise RuntimeError with details."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            ConnectionError("Connection refused"),
        ]

        with pytest.raises(RuntimeError, match="Connection refused"):
            jira.create_issue(
                test_name="OCP-1234",
                test_description="desc",
                version="4.22",
            )

    @patch('src.integrations.jira_integration.requests.post')
    def test_error_message_includes_response_text(self, mock_post, jira):
        """Error message should include the Jira API response body."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            MagicMock(status_code=400, text='Field "priority" is required'),
        ]

        with pytest.raises(RuntimeError, match="priority"):
            jira.create_issue(
                test_name="OCP-1234",
                test_description="desc",
                version="4.22",
            )

    @patch('src.integrations.jira_integration.requests.post')
    def test_success_returns_issue_key(self, mock_post, jira):
        """Successful creation should still return the issue key."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            MagicMock(
                status_code=201,
                json=lambda: {'key': 'TEST-42'},
            ),
        ]

        result = jira.create_issue(
            test_name="OCP-1234",
            test_description="desc",
            version="4.22",
        )
        assert result == "TEST-42"

    @patch('src.integrations.jira_integration.requests.post')
    def test_raises_on_redirect_failure(self, mock_post, jira):
        """Failed redirect should raise with details."""
        mock_post.side_effect = [
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            MagicMock(
                status_code=302,
                headers={'Location': 'https://new.example.com/api'},
            ),
            MagicMock(status_code=500, text='Internal Server Error'),
        ]

        with pytest.raises(RuntimeError, match="Redirect failed"):
            jira.create_issue(
                test_name="OCP-1234",
                test_description="desc",
                version="4.22",
            )


class TestCreateIssueContent:
    """Tests that filed Jira issues contain all required content fields."""

    def _capture_issue_data(self, mock_post, jira, **kwargs):
        """Helper: call create_issue and return the JSON body sent to Jira."""
        mock_post.side_effect = [
            # search returns no existing issue
            MagicMock(status_code=200, json=lambda: {'issues': []}),
            # create succeeds
            MagicMock(status_code=201, json=lambda: {'key': 'TEST-99'}),
        ]
        defaults = dict(
            test_name="OCP-33612",
            test_description="check kubelet version",
            version="5.0",
            platforms=["aws", "azure", "vsphere"],
            error_message="fail: kubelet version mismatch Windows v1.35.3 vs Linux v1.36.2",
            job_url="https://prow.example.com/view/gs/qe-private-deck/logs/periodic-ci-test/123",
            failure_rate=0.0,
            runs=6,
            failures=6,
        )
        defaults.update(kwargs)
        jira.create_issue(**defaults)
        # The second call is the create POST
        create_call = mock_post.call_args_list[1]
        return create_call.kwargs.get('json') or create_call[1].get('json')

    @patch('src.integrations.jira_integration.requests.post')
    def test_error_not_truncated_to_500(self, mock_post, jira):
        """Error message should preserve at least 2000 chars, not 500."""
        long_error = "x" * 1500
        issue_data = self._capture_issue_data(
            mock_post, jira, error_message=long_error
        )
        desc = issue_data['fields']['description']
        # Find the error paragraph — contains "Error: " strong text
        error_texts = []
        for block in desc['content']:
            for node in block.get('content', []):
                if node.get('text', '').startswith('Error:'):
                    # Next sibling is the error text
                    continue
                marks = node.get('marks', [])
                mark_types = [m.get('type') for m in marks]
                if 'strong' not in mark_types:
                    error_texts.append(node.get('text', ''))
        error_body = ''.join(error_texts)
        # The full 1500-char message should be preserved (not truncated)
        assert len(error_body) >= 1500

    @patch('src.integrations.jira_integration.requests.post')
    def test_error_truncated_at_2000(self, mock_post, jira):
        """Error messages longer than 2000 chars should be truncated."""
        long_error = "y" * 3000
        issue_data = self._capture_issue_data(
            mock_post, jira, error_message=long_error
        )
        desc = issue_data['fields']['description']
        all_text = _extract_all_text(desc)
        # Should contain truncated text ending with "..."
        assert "..." in all_text
        # But should not contain all 3000 chars
        assert "y" * 2001 not in all_text

    @patch('src.integrations.jira_integration.requests.post')
    def test_pass_rate_label(self, mock_post, jira):
        """Rate label should say 'Pass Rate', not 'Failure Rate'."""
        issue_data = self._capture_issue_data(mock_post, jira)
        desc = issue_data['fields']['description']
        all_text = _extract_all_text(desc)
        assert "Pass Rate:" in all_text
        assert "Failure Rate:" not in all_text

    @patch('src.integrations.jira_integration.requests.post')
    def test_job_url_in_body(self, mock_post, jira):
        """job_url should appear as a hyperlink in the ADF body."""
        job_url = "https://prow.example.com/view/gs/qe-private-deck/logs/periodic-ci-test/123"
        issue_data = self._capture_issue_data(
            mock_post, jira, job_url=job_url
        )
        desc = issue_data['fields']['description']
        # Check that job_url appears in the ADF as linked text
        links = _extract_links(desc)
        assert job_url in links

    @patch('src.integrations.jira_integration.requests.post')
    def test_ai_analysis_in_body(self, mock_post, jira):
        """AI analysis root cause should appear in the filed bug."""
        ai = {
            'root_cause': 'Windows kubelet version mismatch with Linux',
            'failure_type': 'version_mismatch',
            'suggested_action': 'Align kubelet versions',
        }
        issue_data = self._capture_issue_data(
            mock_post, jira, ai_analysis=ai
        )
        desc = issue_data['fields']['description']
        all_text = _extract_all_text(desc)
        assert 'Windows kubelet version mismatch with Linux' in all_text
        assert 'version_mismatch' in all_text
        assert 'Align kubelet versions' in all_text

    @patch('src.integrations.jira_integration.requests.post')
    def test_ai_analysis_absent_when_none(self, mock_post, jira):
        """When no AI analysis is provided, body should not mention it."""
        issue_data = self._capture_issue_data(
            mock_post, jira, ai_analysis=None
        )
        desc = issue_data['fields']['description']
        all_text = _extract_all_text(desc)
        assert 'Root Cause (AI analysis)' not in all_text

    @patch('src.integrations.jira_integration.requests.post')
    def test_summary_includes_error_context(self, mock_post, jira):
        """Title should incorporate error context, not be generic."""
        issue_data = self._capture_issue_data(mock_post, jira)
        summary = issue_data['fields']['summary']
        # Should NOT be the old generic format
        assert "Test failure on" not in summary or "kubelet" in summary
        # Should contain part of the error message
        assert "kubelet" in summary.lower() or "mismatch" in summary.lower()

    @patch('src.integrations.jira_integration.requests.post')
    def test_summary_uses_ai_root_cause(self, mock_post, jira):
        """When AI analysis exists, title should prefer its root cause."""
        ai = {'root_cause': 'Windows kubelet v1.35.3 != Linux v1.36.2'}
        issue_data = self._capture_issue_data(
            mock_post, jira, ai_analysis=ai
        )
        summary = issue_data['fields']['summary']
        assert 'kubelet' in summary.lower()

    @patch('src.integrations.jira_integration.requests.post')
    def test_summary_length_limit(self, mock_post, jira):
        """Summary should not exceed 255 chars (Jira field limit)."""
        ai = {'root_cause': 'A' * 300}
        issue_data = self._capture_issue_data(
            mock_post, jira, ai_analysis=ai
        )
        summary = issue_data['fields']['summary']
        assert len(summary) <= 255

    @patch('src.integrations.jira_integration.requests.post')
    def test_artifacts_url_in_body(self, mock_post, jira):
        """gcsweb artifacts URL should appear in the body."""
        artifacts = "https://gcsweb-qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/qe-private-deck/logs/test/123/artifacts/"
        issue_data = self._capture_issue_data(
            mock_post, jira, artifacts_url=artifacts
        )
        desc = issue_data['fields']['description']
        links = _extract_links(desc)
        assert artifacts in links

    @patch('src.integrations.jira_integration.requests.post')
    def test_per_platform_breakdown(self, mock_post, jira):
        """Per-platform failure counts should appear in the body."""
        stats = [
            {'platform': 'aws', 'failed': 3, 'total': 3},
            {'platform': 'azure', 'failed': 2, 'total': 3},
            {'platform': 'vsphere', 'failed': 1, 'total': 1},
        ]
        issue_data = self._capture_issue_data(
            mock_post, jira, platform_stats=stats
        )
        desc = issue_data['fields']['description']
        all_text = _extract_all_text(desc)
        assert "aws (3/3 failed)" in all_text
        assert "azure (2/3 failed)" in all_text
        assert "vsphere (1/1 failed)" in all_text

    @patch('src.integrations.jira_integration.requests.post')
    def test_pass_rate_value_correct(self, mock_post, jira):
        """Pass rate value should reflect passes, not failures."""
        issue_data = self._capture_issue_data(
            mock_post, jira, failure_rate=0.0, runs=6, failures=6
        )
        desc = issue_data['fields']['description']
        all_text = _extract_all_text(desc)
        # 0 out of 6 passed
        assert "0/6 runs passed" in all_text


def _extract_all_text(adf_doc):
    """Extract all text content from an ADF document."""
    texts = []
    for block in adf_doc.get('content', []):
        for node in block.get('content', []):
            texts.append(node.get('text', ''))
    return ''.join(texts)


def _extract_links(adf_doc):
    """Extract all link hrefs from an ADF document."""
    links = []
    for block in adf_doc.get('content', []):
        for node in block.get('content', []):
            for mark in node.get('marks', []):
                if mark.get('type') == 'link':
                    links.append(mark.get('attrs', {}).get('href', ''))
    return links
