"""Verify AGENTS.md includes accessibility requirements for interactive UI.

This test ensures AGENTS.md rule 22 contains the required accessibility
keywords so that agents producing interactive UI components in
dashboard.html include proper ARIA attributes and keyboard handlers.
"""

import os


AGENTS_MD_PATH = os.path.join(os.path.dirname(__file__), '..', 'AGENTS.md')


class TestAgentsMdAccessibilityRule:
    """Verify AGENTS.md contains accessibility rule content."""

    def _read_agents_md(self):
        with open(AGENTS_MD_PATH) as f:
            return f.read()

    def test_aria_haspopup_required(self):
        """Rule must require aria-haspopup on menu triggers."""
        content = self._read_agents_md()
        assert 'aria-haspopup' in content

    def test_aria_expanded_required(self):
        """Rule must require aria-expanded toggling."""
        content = self._read_agents_md()
        assert 'aria-expanded' in content

    def test_role_menu_required(self):
        """Rule must require role='menu' on dropdown containers."""
        content = self._read_agents_md()
        assert 'role="menu"' in content or "role='menu'" in content

    def test_role_menuitem_required(self):
        """Rule must require role='menuitem' on menu items."""
        content = self._read_agents_md()
        assert 'role="menuitem"' in content or "role='menuitem'" in content

    def test_escape_key_required(self):
        """Rule must require Escape key to close dropdowns."""
        content = self._read_agents_md()
        assert 'Escape' in content

    def test_noopener_noreferrer_required(self):
        """Rule must require rel='noopener noreferrer' on target='_blank' links."""
        content = self._read_agents_md()
        assert 'noopener noreferrer' in content
