"""Verify AGENTS.md includes POC design decisions rule.

This test ensures AGENTS.md rule 24 instructs review agents to accept
documented POC trade-offs in dashboard/openshift/poc/ files rather than
re-flagging intentional security posture choices (see issue #265).
"""

import os


AGENTS_MD_PATH = os.path.join(os.path.dirname(__file__), '..', 'AGENTS.md')


class TestAgentsMdPocDesignDecisionsRule:
    """Verify AGENTS.md contains POC design decisions rule content."""

    def _read_agents_md(self):
        with open(AGENTS_MD_PATH) as f:
            return f.read()

    def test_poc_path_referenced(self):
        """Rule must reference the POC manifest directory."""
        content = self._read_agents_md()
        assert 'dashboard/openshift/poc/' in content

    def test_design_decisions_referenced(self):
        """Rule must reference the Design Decisions documentation."""
        content = self._read_agents_md()
        assert 'Design Decisions' in content

    def test_documentation_gap_guidance(self):
        """Rule must instruct flagging documentation gaps, not security choices."""
        content = self._read_agents_md()
        assert 'documentation gap' in content

    def test_intentional_poc_tradeoffs(self):
        """Rule must mention intentional POC trade-offs."""
        content = self._read_agents_md()
        assert 'intentional POC' in content

    def test_not_flag_security_posture(self):
        """Rule must instruct not flagging documented security posture choices."""
        content = self._read_agents_md()
        assert 'security posture' in content.lower() or \
            'security choice' in content.lower()
