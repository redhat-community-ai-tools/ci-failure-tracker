"""Tests for the POC deployment guide and script.

The guide documents the operational traps that are easy to rediscover the
hard way: the ConfigMap is a separate resource from the image, :latest does
not move on its own, and the deployed config is the working tree copy.
These tests keep the script and the guide from drifting apart.
"""

import os
import stat

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, 'scripts', 'deploy-poc.sh')
GUIDE = os.path.join(REPO_ROOT, 'DEPLOY-POC.md')
README = os.path.join(REPO_ROOT, 'dashboard', 'README.md')


@pytest.fixture(scope='module')
def script_source():
    with open(SCRIPT) as f:
        return f.read()


@pytest.fixture(scope='module')
def guide_source():
    with open(GUIDE) as f:
        return f.read()


class TestScriptShape:
    def test_exists_and_is_executable(self):
        assert os.path.isfile(SCRIPT)
        mode = os.stat(SCRIPT).st_mode
        assert mode & stat.S_IXUSR, 'deploy-poc.sh must be executable'

    def test_bash_strict_mode(self, script_source):
        assert script_source.startswith('#!/usr/bin/env bash')
        assert 'set -euo pipefail' in script_source

    def test_no_non_ascii_output(self, script_source):
        """Repository rule: plain text only, no emoji or box drawing."""
        bad = [c for c in script_source if ord(c) > 127]
        assert not bad, f'non-ASCII characters in deploy-poc.sh: {bad[:5]}'

    def test_configmap_uses_apply_not_replace(self, script_source):
        assert 'create configmap' in script_source
        assert 'apply -f -' in script_source
        assert 'replace -f' not in script_source

    def test_build_failure_is_detected_before_tagging(self, script_source):
        for phase in ('Failed', 'Error', 'Cancelled'):
            assert phase in script_source
        assert '.status.output.to.imageDigest' in script_source

    def test_rollout_is_waited_on(self, script_source):
        assert 'rollout restart' in script_source
        assert 'rollout status' in script_source

    def test_verifies_pod_image_matches_build(self, script_source):
        assert 'containerStatuses' in script_source
        assert 'image matches build' in script_source

    def test_has_cluster_guard(self, script_source):
        assert 'EXPECTED_SERVER' in script_source

    def test_skips_config_on_request(self, script_source):
        assert 'SKIP_CONFIG' in script_source


class TestGuide:
    def test_documents_both_resources(self, guide_source):
        assert 'ConfigMap' in guide_source
        assert 'image' in guide_source.lower()

    def test_warns_about_working_tree_config(self, guide_source):
        assert 'working-tree' in guide_source
        assert 'SKIP_CONFIG=1' in guide_source

    def test_documents_rescue_path(self, guide_source):
        assert 'backfill_from_deck.py' in guide_source
        assert 'prowjobs.js' in guide_source

    def test_documents_browser_cache(self, guide_source):
        assert 'Cmd+Shift+R' in guide_source

    def test_documents_token_expiry(self, guide_source):
        assert 'token-renewal.md' in guide_source

    def test_links_the_script(self, guide_source):
        assert 'scripts/deploy-poc.sh' in guide_source

    def test_no_non_ascii_content(self, guide_source):
        bad = [c for c in guide_source if ord(c) > 127]
        assert not bad, f'non-ASCII characters in DEPLOY-POC.md: {bad[:5]}'


class TestReadmeLink:
    def test_readme_points_at_the_guide(self):
        with open(README) as f:
            content = f.read()
        assert 'DEPLOY-POC.md' in content
