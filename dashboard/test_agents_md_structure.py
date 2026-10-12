"""Verify AGENTS.md structural integrity.

Guards against numbering collisions when multiple PRs append rules
concurrently (see issue #234).
"""

import os
import re


AGENTS_MD_PATH = os.path.join(os.path.dirname(__file__), '..', 'AGENTS.md')


class TestAgentsMdRuleNumbering:
    """Verify AGENTS.md rules under ## Rules are numbered sequentially."""

    def _get_rule_numbers(self):
        """Extract rule numbers from the ## Rules section of AGENTS.md."""
        with open(AGENTS_MD_PATH) as f:
            content = f.read()
        # Find the ## Rules section
        rules_match = re.search(r'^## Rules\s*$', content, re.MULTILINE)
        assert rules_match is not None, '## Rules section not found in AGENTS.md'
        rules_section = content[rules_match.end():]
        # Stop at the next heading (## or #) if any
        next_heading = re.search(r'^#', rules_section, re.MULTILINE)
        if next_heading:
            rules_section = rules_section[:next_heading.start()]
        # Extract top-level rule numbers (lines starting with digits
        # followed by a dot at the start of a line)
        numbers = [
            int(m.group(1))
            for m in re.finditer(r'^(\d+)\.\s', rules_section, re.MULTILINE)
        ]
        return numbers

    def test_rule_numbers_are_sequential(self):
        """Rule numbers must increase by 1 with no gaps or duplicates."""
        numbers = self._get_rule_numbers()
        assert len(numbers) > 0, 'No numbered rules found in AGENTS.md'
        for i in range(1, len(numbers)):
            assert numbers[i] == numbers[i - 1] + 1, (
                f'Rule numbering not sequential: rule {numbers[i - 1]} '
                f'followed by {numbers[i]}'
            )

    def test_first_rule_starts_at_one(self):
        """The first rule should be numbered 1."""
        numbers = self._get_rule_numbers()
        assert len(numbers) > 0, 'No numbered rules found in AGENTS.md'
        assert numbers[0] == 1, (
            f'First rule should be numbered 1, got {numbers[0]}'
        )

    def test_no_duplicate_rule_numbers(self):
        """No two rules should share the same number."""
        numbers = self._get_rule_numbers()
        duplicates = [n for n in set(numbers) if numbers.count(n) > 1]
        assert len(duplicates) == 0, (
            f'Duplicate rule numbers found: {sorted(duplicates)}'
        )
