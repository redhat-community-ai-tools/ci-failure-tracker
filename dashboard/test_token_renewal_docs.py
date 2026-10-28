"""Tests for token-renewal.md documentation coverage.

Verifies that the token renewal guide documents both the l2s4 and
build10 clusters, including console URLs, token request URLs, and
troubleshooting commands.
"""

import os


DOCS_PATH = os.path.join(
    os.path.dirname(__file__), 'docs', 'token-renewal.md'
)


def _read_doc():
    with open(DOCS_PATH) as f:
        return f.read()


class TestClusterCoverage:
    """Verify both l2s4 and build10 clusters are documented."""

    def test_l2s4_cluster_mentioned(self):
        content = _read_doc()
        assert 'l2s4' in content, 'Missing l2s4 cluster documentation'

    def test_build10_cluster_mentioned(self):
        content = _read_doc()
        assert 'build10' in content, 'Missing build10 cluster documentation'

    def test_l2s4_token_url(self):
        content = _read_doc()
        assert (
            'https://oauth-openshift.apps.ci.l2s4.p1.openshiftapps.com'
            in content
        )

    def test_build10_console_url(self):
        content = _read_doc()
        assert (
            'https://console-openshift-console.apps.build10.ci.devcluster.openshift.com'
            in content
        )


class TestClusterIdentificationSection:
    """Verify the cluster identification section exists."""

    def test_which_cluster_section(self):
        content = _read_doc()
        assert '## Which Cluster?' in content

    def test_cluster_table_has_both_entries(self):
        content = _read_doc()
        assert '| **l2s4**' in content
        assert '| **build10**' in content


class TestTroubleshootingCoverage:
    """Verify troubleshooting section covers both clusters."""

    def test_unauthorized_mentions_build10(self):
        """The Unauthorized troubleshooting entry references build10."""
        content = _read_doc()
        # Find the Unauthorized section
        section_start = content.find('Unauthorized')
        assert section_start > 0
        section = content[section_start:section_start + 600]
        assert 'build10' in section

    def test_token_test_curl_build10(self):
        """Documentation coverage: the manual token-test curl section includes a build10 URL.

        This verifies the docs mention the build10 Prow endpoint, not that
        the endpoint is reachable.
        """
        content = _read_doc()
        assert (
            'https://qe-private-deck-ci.apps.build10.ci.devcluster.openshift.com'
            in content
        )


class TestAutoRenewalLimitations:
    """Verify the auto-renewal limitations section documents known constraints."""

    def test_limitations_section_exists(self):
        content = _read_doc()
        assert '## Auto-Renewal Limitations' in content

    def test_service_account_limitation_documented(self):
        """Verify the service account approach failure is documented."""
        content = _read_doc()
        section_start = content.find('## Auto-Renewal Limitations')
        assert section_start > 0
        section = content[section_start:]
        assert 'service account' in section.lower()
        assert '403' in section

    def test_programmatic_token_limitation_documented(self):
        """Verify the programmatic user token limitation is documented."""
        content = _read_doc()
        section_start = content.find('## Auto-Renewal Limitations')
        assert section_start > 0
        section = content[section_start:]
        assert 'interactive' in section.lower()
        assert 'sso' in section.lower()

    def test_resolution_path_documented(self):
        """Verify the resolution path describes what deck operators need to do."""
        content = _read_doc()
        section_start = content.find('### Resolution path')
        assert section_start > 0
        section = content[section_start:]
        assert 'oauth' in section.lower()

    def test_no_misleading_service_account_instructions(self):
        """Verify the doc does not suggest service account tokens work."""
        content = _read_doc()
        # The old section suggested 'oc create token' would solve the problem.
        # The updated doc should not contain this misleading suggestion.
        assert 'oc create token dashboard-collector' not in content
