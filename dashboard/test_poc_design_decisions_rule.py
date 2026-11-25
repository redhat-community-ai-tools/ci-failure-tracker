"""Verify AGENTS.md includes POC design decisions rule.

This test ensures the AGENTS.md "Documented POC design decisions" rule
instructs review agents to accept
documented POC trade-offs in dashboard/openshift/poc/ files rather than
re-flagging intentional security posture choices (see issue #265).
"""

import os
import re


AGENTS_MD_PATH = os.path.join(os.path.dirname(__file__), '..', 'AGENTS.md')


def _extract_rule():
    """Extract the "Documented POC design decisions" rule from AGENTS.md.

    The rule is located by title, not by number: rule numbers shift when
    other rules are added ahead of it.
    """
    with open(AGENTS_MD_PATH) as f:
        content = f.read()
    match = re.search(
        r'^\d+\.\s+\*\*Documented POC design decisions\.\*\*(.+?)(?=^\d+\.\s|\Z)',
        content,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, (
        'AGENTS.md rule "Documented POC design decisions" not found')
    number = re.match(r'^(\d+)\.', match.group(0))
    assert number is not None
    return match.group(0)


class TestAgentsMdPocDesignDecisionsRule:
    """Verify the POC design decisions rule contains the required content."""

    def test_poc_path_referenced(self):
        """Rule must reference the POC manifest directory."""
        rule = _extract_rule()
        assert 'dashboard/openshift/poc/' in rule

    def test_design_decisions_referenced(self):
        """Rule must reference the Design Decisions documentation."""
        rule = _extract_rule()
        assert 'Design Decisions' in rule

    def test_documentation_gap_guidance(self):
        """Rule must instruct flagging documentation gaps, not security choices."""
        rule = _extract_rule()
        assert 'documentation gap' in rule

    def test_intentional_poc_tradeoffs(self):
        """Rule must mention intentional POC trade-offs."""
        rule = _extract_rule()
        assert 'intentional POC' in rule

    def test_not_flag_security_posture(self):
        """Rule must instruct not flagging documented security posture choices."""
        rule = _extract_rule()
        assert 'security posture' in rule or 'security choice' in rule

    def test_same_pr_guard(self):
        """Rule must require documentation to exist on target branch, not same PR."""
        rule = _extract_rule()
        assert 'documentation introduced by the same changeset does not' in rule

    def test_verification_covers_both_sources(self):
        """Verification clause must cover both README and inline YAML comments."""
        rule = _extract_rule()
        assert 'Design Decisions section or the inline YAML' in rule

    def test_sunset_clause(self):
        """Rule must include a sunset clause for production promotion."""
        rule = _extract_rule()
        assert 're-evaluated or removed' in rule
        assert 'promoted to production' in rule
