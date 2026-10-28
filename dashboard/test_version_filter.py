"""Tests for server.py version filtering of gcsweb postsubmit jobs.

Validates that FBC postsubmit jobs with WMCO version suffixes (e.g.,
v11-0 -> OCP 5.0) are included when filtering by their resolved OCP
version, not dropped because they lack 'release-{version}' in the
job name.

Includes negative test cases per AGENTS.md rule 8.
"""

import yaml

from src.collectors.gcsweb import GCSWebCollector


def _load_config():
    """Load the project config.yaml for config-driven assertions."""
    import os
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def _build_collector(config=None):
    """Build a GCSWebCollector with version-resolution config."""
    if config is None:
        config = _load_config()
    gcsweb_cfg = config['collector']['gcsweb'].copy()
    tracking = config.get('tracking', {})
    gcsweb_cfg['branch_version_map'] = tracking.get('branch_version_map', {})
    return GCSWebCollector(gcsweb_cfg)


def _filter_jobs(all_job_names, version_filter, collector):
    """Reproduce the server.py version filtering logic after the fix.

    This mirrors the patched code in run_collection_background() so
    tests validate the same algorithm the server uses.
    """
    expanded = [
        j for j in all_job_names
        if collector._extract_metadata(j)['version'] == version_filter
    ]
    if not expanded:
        expanded = all_job_names
    return expanded


class TestVersionFilterIncludesPostsubmit:
    """FBC postsubmit jobs should be included by version filter."""

    def test_fbc_v11_0_included_for_version_5_0(self):
        """FBC postsubmit with v11-0 suffix resolves to OCP 5.0 and
        should be included when filtering for version 5.0."""
        collector = _build_collector()
        all_jobs = [
            'periodic-ci-openshift-openshift-tests-private-release-5.0-amd64-nightly-aws-ipi-ovn-winc-f7',
            'branch-ci-openshift-windows-machine-config-operator-fbc-main-v11-0-aws-ipi-ovn-winc',
            'periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-nightly-aws-ipi-ovn-winc-f7',
        ]
        result = _filter_jobs(all_jobs, '5.0', collector)
        assert 'branch-ci-openshift-windows-machine-config-operator-fbc-main-v11-0-aws-ipi-ovn-winc' in result
        assert 'periodic-ci-openshift-openshift-tests-private-release-5.0-amd64-nightly-aws-ipi-ovn-winc-f7' in result

    def test_fbc_v10_21_included_for_version_4_21(self):
        """FBC postsubmit with v10-21 suffix resolves to OCP 4.21."""
        collector = _build_collector()
        all_jobs = [
            'periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-nightly-aws-ipi-ovn-winc-f7',
            'branch-ci-openshift-windows-machine-config-operator-fbc-main-v10-21-aws-ipi-ovn-winc',
        ]
        result = _filter_jobs(all_jobs, '4.21', collector)
        assert 'branch-ci-openshift-windows-machine-config-operator-fbc-main-v10-21-aws-ipi-ovn-winc' in result

    def test_fbc_no_version_suffix_uses_default(self):
        """FBC postsubmit without version suffix uses fbc_default_version.

        config.yaml sets fbc_default_version to 5.1, so these jobs
        should be included when filtering for 5.1.
        """
        config = _load_config()
        fbc_default = config['collector']['gcsweb']['fbc_default_version']
        assert fbc_default == '5.1', (
            "Config-driven: fbc_default_version should be 5.1"
        )
        collector = _build_collector(config)
        all_jobs = [
            'branch-ci-openshift-windows-machine-config-operator-fbc-main-aws-ipi-ovn-winc',
            'periodic-ci-openshift-openshift-tests-private-release-5.1-amd64-nightly-aws-ipi-ovn-winc-f7',
        ]
        result = _filter_jobs(all_jobs, fbc_default, collector)
        assert 'branch-ci-openshift-windows-machine-config-operator-fbc-main-aws-ipi-ovn-winc' in result

    def test_release_pattern_jobs_still_included(self):
        """Regular release-X.Y jobs are still correctly filtered."""
        collector = _build_collector()
        all_jobs = [
            'periodic-ci-openshift-openshift-tests-private-release-5.0-amd64-nightly-aws-ipi-ovn-winc-f7',
            'periodic-ci-openshift-openshift-tests-private-release-5.0-amd64-nightly-gcp-ipi-ovn-winc-f7',
            'periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-nightly-aws-ipi-ovn-winc-f7',
        ]
        result = _filter_jobs(all_jobs, '5.0', collector)
        assert len(result) == 2
        assert all('release-5.0' in j for j in result)


class TestVersionFilterExcludesWrongVersion:
    """Negative tests: jobs that resolve to a different version must
    be excluded (AGENTS.md rule 8)."""

    def test_fbc_v11_0_excluded_for_version_5_1(self):
        """FBC postsubmit with v11-0 resolves to 5.0, NOT 5.1."""
        collector = _build_collector()
        all_jobs = [
            'branch-ci-openshift-windows-machine-config-operator-fbc-main-v11-0-aws-ipi-ovn-winc',
            'periodic-ci-openshift-openshift-tests-private-release-5.1-amd64-nightly-aws-ipi-ovn-winc-f7',
        ]
        result = _filter_jobs(all_jobs, '5.1', collector)
        assert 'branch-ci-openshift-windows-machine-config-operator-fbc-main-v11-0-aws-ipi-ovn-winc' not in result
        assert 'periodic-ci-openshift-openshift-tests-private-release-5.1-amd64-nightly-aws-ipi-ovn-winc-f7' in result

    def test_fbc_no_suffix_excluded_for_version_5_0(self):
        """FBC postsubmit without version suffix maps to fbc_default_version
        (5.1), so it should NOT be included when filtering for 5.0."""
        collector = _build_collector()
        all_jobs = [
            'branch-ci-openshift-windows-machine-config-operator-fbc-main-aws-ipi-ovn-winc',
            'periodic-ci-openshift-openshift-tests-private-release-5.0-amd64-nightly-aws-ipi-ovn-winc-f7',
        ]
        result = _filter_jobs(all_jobs, '5.0', collector)
        assert 'branch-ci-openshift-windows-machine-config-operator-fbc-main-aws-ipi-ovn-winc' not in result

    def test_release_4_22_excluded_for_version_5_0(self):
        """Release-4.22 jobs should not be included for version 5.0."""
        collector = _build_collector()
        all_jobs = [
            'periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-nightly-aws-ipi-ovn-winc-f7',
            'periodic-ci-openshift-openshift-tests-private-release-5.0-amd64-nightly-aws-ipi-ovn-winc-f7',
        ]
        result = _filter_jobs(all_jobs, '5.0', collector)
        assert len(result) == 1
        assert 'release-5.0' in result[0]

    def test_adjacent_wmco_version_not_matched(self):
        """v11-1 resolves to OCP 5.1, not 5.0 -- must not be included
        for version 5.0."""
        collector = _build_collector()
        all_jobs = [
            'branch-ci-openshift-windows-machine-config-operator-fbc-main-v11-1-aws-ipi-ovn-winc',
            'branch-ci-openshift-windows-machine-config-operator-fbc-main-v11-0-aws-ipi-ovn-winc',
        ]
        result = _filter_jobs(all_jobs, '5.0', collector)
        assert len(result) == 1
        assert 'v11-0' in result[0]


class TestVersionFilterWildcardPatterns:
    """Wildcard patterns in job_names should also be filtered correctly."""

    def test_wildcard_with_release_version_matched(self):
        """Wildcard patterns containing release-X.Y are matched."""
        collector = _build_collector()
        all_jobs = [
            'periodic-ci-openshift-openshift-tests-private-release-5.0-*-winc-*',
            'periodic-ci-openshift-openshift-tests-private-release-4.22-*-winc-*',
        ]
        result = _filter_jobs(all_jobs, '5.0', collector)
        assert len(result) == 1
        assert 'release-5.0' in result[0]

    def test_main_branch_wildcard_maps_to_configured_version(self):
        """Wildcard pattern with 'main' maps to branch_version_map version."""
        config = _load_config()
        branch_map = config['tracking']['branch_version_map']
        main_version = branch_map.get('main')
        assert main_version is not None, (
            "Config-driven: branch_version_map must have a 'main' entry"
        )
        collector = _build_collector(config)
        all_jobs = [
            'periodic-ci-openshift-openshift-tests-private-main-*-winc-*',
        ]
        result = _filter_jobs(all_jobs, main_version, collector)
        assert len(result) == 1


class TestVersionFilterFallback:
    """When no jobs match the version filter, all jobs should be returned."""

    def test_fallback_returns_all_when_no_match(self):
        """If no job resolves to the requested version, return all."""
        collector = _build_collector()
        all_jobs = [
            'periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-nightly-aws-ipi-ovn-winc-f7',
        ]
        result = _filter_jobs(all_jobs, '99.99', collector)
        assert result == all_jobs


class TestConfigWmcoVersionMapDriven:
    """Config-driven assertions: verify that wmco_version_map in
    config.yaml has the expected mappings (AGENTS.md rule 11)."""

    def test_wmco_version_map_has_expected_entries(self):
        config = _load_config()
        wmco_map = config['collector']['gcsweb']['wmco_version_map']
        assert wmco_map.get('10') == '4', (
            "WMCO major 10 should map to OCP major 4"
        )
        assert wmco_map.get('11') == '5', (
            "WMCO major 11 should map to OCP major 5"
        )
