"""Tests for version-filtered job selection.

Covers AGENTS.md rule 8: every matching rule needs negative cases that
prove adjacent-but-wrong job names are not selected.
"""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from collectors.gcsweb import GCSWebCollector
from web.server import filter_jobs_by_version

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.yaml')

FBC_422 = ('branch-ci-openshift-windows-machine-config-operator-fbc-'
           'main-v10-22-aws-ipi-ovn-winc')
FBC_421 = ('branch-ci-openshift-windows-machine-config-operator-fbc-'
           'main-v10-21-aws-ipi-ovn-winc')
FBC_51 = ('branch-ci-openshift-windows-machine-config-operator-fbc-'
          'main-aws-ipi-ovn-winc')
PERIODIC_422 = ('periodic-ci-openshift-openshift-tests-private-release-4.22-'
                'amd64-nightly-aws-ipi-ovn-winc-f7')
PERIODIC_421 = ('periodic-ci-openshift-openshift-tests-private-release-4.21-'
                'amd64-nightly-aws-ipi-ovn-winc-f14')


@pytest.fixture
def gcsweb_config():
    """The gcsweb collector config actually shipped in config.yaml."""
    with open(CONFIG_PATH) as handle:
        return yaml.safe_load(handle)['collector']['gcsweb']


@pytest.fixture
def collector(gcsweb_config):
    """Collector built from the shipped config (no network at init)."""
    config = dict(gcsweb_config)
    config['api_token'] = 'test-token'
    return GCSWebCollector(config)


def _filter(names, version, collector, branch_map=None):
    return filter_jobs_by_version(
        names, version,
        version_for_job=collector.job_version,
        branch_map=branch_map or {})


class TestVersionFilterSelectsFbcJobs:
    """FBC jobs encode a WMCO version, not release-X.Y, and used to be
    dropped from every version-filtered run."""

    def test_fbc_job_selected_for_its_ocp_version(self, collector):
        assert _filter([FBC_422], '4.22', collector) == [FBC_422]

    def test_fbc_job_not_selected_for_neighbour_version(self, collector):
        """Negative: the 4.21 FBC job must not appear in a 4.22 run and
        vice versa, even though both are 'v10-2X' names."""
        assert _filter([FBC_421], '4.22', collector) == []
        assert _filter([FBC_422], '4.21', collector) == []

    def test_version_only_in_name_does_not_match_wmco_lookalike(
            self, collector):
        """Negative: v10-22 must not be read as 10.22 or 22."""
        assert _filter([FBC_422], '10.22', collector) == []
        assert _filter([FBC_422], '22', collector) == []

    def test_fbc_job_without_version_maps_to_default(
            self, collector, gcsweb_config):
        """A version-less FBC job belongs to fbc_default_version only."""
        default = gcsweb_config['fbc_default_version']
        assert _filter([FBC_51], default, collector) == [FBC_51]
        assert _filter([FBC_51], '4.22', collector) == []


class TestVersionFilterPatterns:
    """Wildcard patterns survive only when their text pins the version."""

    def test_pattern_for_version_kept(self, collector):
        names = ['periodic-ci-openshift-openshift-tests-private-release-4.22-*-winc-*']
        assert _filter(names, '4.22', collector) == names

    def test_pattern_for_other_version_dropped(self, collector):
        """Negative: the 4.21 pattern must not be kept for 4.22."""
        names = ['periodic-ci-openshift-openshift-tests-private-release-4.21-*-winc-*']
        assert _filter(names, '4.22', collector) == []

    def test_prefix_pattern_dropped(self, collector):
        """A pattern with no version token cannot be version-filtered,
        so it is dropped rather than treated as matching everything."""
        names = ['branch-ci-openshift-windows-machine-config-operator-fbc-']
        assert _filter(names, '4.22', collector) == []


class TestVersionFilterReleaseJobs:
    """release-X.Y jobs keep working as before."""

    def test_periodic_job_selected(self, collector):
        assert _filter([PERIODIC_422], '4.22', collector) == [PERIODIC_422]

    def test_neighbour_version_not_selected(self, collector):
        """Negative: 4.21 periodic job excluded from 4.22."""
        assert _filter([PERIODIC_421], '4.22', collector) == []

    def test_prefix_similarity_not_matched(self, collector):
        """Negative: 4.220 and 4.2 must not match a 4.22 filter."""
        lookalike = ('periodic-ci-openshift-openshift-tests-private-'
                     'release-4.220-amd64-nightly-aws-ipi-ovn-winc-f7')
        assert _filter([lookalike], '4.22', collector) == []


class TestVersionFilterBranchMap:
    """branch_version_map jobs (e.g. 'main') resolve by branch."""

    def test_branch_job_selected_via_map(self, collector):
        job = 'some-main-winc-job'
        result = _filter([job], '5.1', collector, branch_map={'main': '5.1'})
        assert result == [job]

    def test_branch_job_not_selected_without_map(self, collector):
        """Negative: without branch_version_map the bare branch name is
        not enough to claim the version."""
        job = 'some-main-winc-job'
        assert _filter([job], '5.1', collector) == []


class TestVersionFilterAgainstShippedConfig:
    """The filter must return a usable job list for a version that only
    has public FBC coverage, using the real config.yaml (rule 11: assert
    against the configured value, not a copy)."""

    def test_422_selects_public_fbc_jobs(self, collector, gcsweb_config):
        names = list(gcsweb_config['job_names'])
        discovered = ['branch-ci-openshift-windows-machine-config-operator-'
                      'fbc-main-v10-22-aws-ipi-ovn-winc']
        selected = _filter(names + discovered, '4.22', collector)

        assert discovered[0] in selected
        # every selected pattern must be version-scoped for 4.22
        for name in selected:
            assert 'release-4.22' in name or collector.job_version(name) == '4.22'

    def test_422_selection_is_not_empty(self, collector, gcsweb_config):
        """Regression: a 4.22 run used to resolve to zero jobs and save
        nothing while reporting success."""
        names = list(gcsweb_config['job_names'])
        names.append('branch-ci-openshift-windows-machine-config-operator-'
                     'fbc-main-v10-22-aws-ipi-ovn-winc')
        assert _filter(names, '4.22', collector)

    def test_unknown_version_selects_nothing(self, collector, gcsweb_config):
        """Negative: an unconfigured version must yield no jobs so the
        caller raises a visible error instead of running unfiltered."""
        names = list(gcsweb_config['job_names'])
        names.append('branch-ci-openshift-windows-machine-config-operator-'
                     'fbc-main-v10-22-aws-ipi-ovn-winc')
        assert _filter(names, '9.9', collector) == []
