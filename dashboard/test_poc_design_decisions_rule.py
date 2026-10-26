"""Verify AGENTS.md includes POC design decisions rule.

This test ensures AGENTS.md rule 24 instructs review agents to accept
documented POC trade-offs in dashboard/openshift/poc/ files rather than
re-flagging intentional security posture choices (see issue #265).
"""

import os
import re


AGENTS_MD_PATH = os.path.join(os.path.dirname(__file__), '..', 'AGENTS.md')


def _extract_rule_24():
    """Extract the text of rule 24 from AGENTS.md."""
    with open(AGENTS_MD_PATH) as f:
        content = f.read()
    # Find rule 24 and extract until the next numbered rule or end of file
    match = re.search(
        r'^24\.\s+\*\*Documented POC design decisions\.\*\*(.+?)(?=^\d+\.\s|\Z)',
        content,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, 'Rule 24 (Documented POC design decisions) not found in AGENTS.md'
    return match.group(0)


class TestAgentsMdPocDesignDecisionsRule:
    """Verify AGENTS.md rule 24 contains required POC design decisions content."""

    def test_poc_path_referenced(self):
        """Rule must reference the POC manifest directory."""
        rule = _extract_rule_24()
        assert 'dashboard/openshift/poc/' in rule

    def test_design_decisions_referenced(self):
        """Rule must reference the Design Decisions documentation."""
        rule = _extract_rule_24()
        assert 'Design Decisions' in rule

    def test_documentation_gap_guidance(self):
        """Rule must instruct flagging documentation gaps, not security choices."""
        rule = _extract_rule_24()
        assert 'documentation gap' in rule

    def test_intentional_poc_tradeoffs(self):
        """Rule must mention intentional POC trade-offs."""
        rule = _extract_rule_24()
        assert 'intentional POC' in rule

    def test_not_flag_security_posture(self):
        """Rule must instruct not flagging documented security posture choices."""
        rule = _extract_rule_24()
        assert 'security posture' in rule or 'security choice' in rule

    def test_same_pr_guard(self):
        """Rule must require documentation to exist on target branch, not same PR."""
        rule = _extract_rule_24()
        assert 'documentation introduced by the same changeset does not' in rule

    def test_verification_covers_both_sources(self):
        """Verification clause must cover both README and inline YAML comments."""
        rule = _extract_rule_24()
        assert 'Design Decisions section or the inline YAML' in rule

    def test_sunset_clause(self):
        """Rule must include a sunset clause for production promotion."""
        rule = _extract_rule_24()
        assert 're-evaluated or removed' in rule
        assert 'promoted to production' in rule
