"""Verify AGENTS.md rule #24 references both SAML_AUTHENTICATION.md and POC README.

Rule 24 must guide agents to check both the oauth-proxy sidecar docs
and the POC README when modifying deployment manifests, so that
deployment-level changes are documented in the same commit.
"""

import os
import re


AGENTS_MD_PATH = os.path.join(os.path.dirname(__file__), '..', 'AGENTS.md')


def _get_rule_24_text():
    """Extract the text of rule 24 from AGENTS.md."""
    with open(AGENTS_MD_PATH) as f:
        content = f.read()
    match = re.search(
        r'24\.\s+\*\*.*?\*\*.*?(?=\n\d+\.|\Z)', content, re.DOTALL
    )
    assert match, 'Rule 24 not found in AGENTS.md'
    return match.group(0)


def test_rule_24_mentions_saml_authentication():
    """Rule 24 must reference docs/SAML_AUTHENTICATION.md."""
    rule_text = _get_rule_24_text()
    assert 'SAML_AUTHENTICATION.md' in rule_text


def test_rule_24_mentions_poc_readme():
    """Rule 24 must reference the POC README for deployment-level changes."""
    rule_text = _get_rule_24_text()
    assert 'README.md' in rule_text


def test_rule_24_mentions_deployment_level_config():
    """Rule 24 must mention deployment-level configuration examples."""
    rule_text = _get_rule_24_text()
    assert 'strategy' in rule_text.lower()
    assert 'replicas' in rule_text.lower()
