"""Config-driven tests for version tracking in config.yaml.

Validates that tracked versions, job patterns, and job names in
config.yaml include all expected version entries (AGENTS.md rule 11).
"""

import os

import yaml
import pytest


@pytest.fixture
def config():
    """Load the dashboard config.yaml."""
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    with open(config_path) as f:
        return yaml.safe_load(f)


class TestVersion51InConfig:
    """Config-driven test: assert version 5.1 is tracked (rule 11)."""

    def test_version_5_1_in_tracking_versions(self, config):
        """tracking.versions includes 5.1."""
        versions = config['tracking']['versions']
        assert '5.1' in versions

    def test_version_5_1_in_gcsweb_job_names(self, config):
        """gcsweb job_names includes a release-5.1 pattern."""
        job_names = config['collector']['gcsweb']['job_names']
        assert any('release-5.1' in j for j in job_names)

    def test_version_5_1_in_prow_gcs_openshift_tests_private(self, config):
        """prow_gcs job_patterns includes openshift-tests-private 5.1."""
        patterns = config['collector']['prow_gcs']['job_patterns']
        assert any(
            'openshift-tests-private-release-5.1' in p for p in patterns
        )

    def test_version_5_1_in_prow_gcs_wmco(self, config):
        """prow_gcs job_patterns includes WMCO 5.1."""
        patterns = config['collector']['prow_gcs']['job_patterns']
        assert any(
            'windows-machine-config-operator-release-5.1' in p
            for p in patterns
        )


class TestBranchVersionMap:
    """Config-driven test: assert branch_version_map entries (rule 11)."""

    def test_main_maps_to_5_1(self, config):
        """branch_version_map maps main to 5.1 (WMCO 11.1.0 = OCP 5.1)."""
        branch_map = config['tracking']['branch_version_map']
        assert branch_map['main'] == '5.1'


class TestPostsubmitBucket:
    """Config-driven test: postsubmit bucket must be public (rule 11)."""

    def test_postsubmit_bucket_is_public(self, config):
        """postsubmit_gcsweb.bucket must be the public bucket.

        FBC postsubmit jobs are in the public origin-ci-test / test-platform-
        results-public bucket.  Anonymous access to the private bucket returns
        HTTP 401, preventing job discovery.
        """
        bucket = config['collector']['gcsweb']['postsubmit_gcsweb']['bucket']
        assert bucket == 'test-platform-results-public', (
            f"FBC postsubmit bucket must be public; got '{bucket}'"
        )
