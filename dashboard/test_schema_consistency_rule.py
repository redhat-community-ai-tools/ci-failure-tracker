"""Verify AGENTS.md includes database schema consistency rule.

This test ensures AGENTS.md rule 24 contains the required keywords so
that agents modifying database schemas check all schema definition sites
in _create_tables() for consistency.
"""

import os


AGENTS_MD_PATH = os.path.join(os.path.dirname(__file__), '..', 'AGENTS.md')


class TestAgentsMdSchemaConsistencyRule:
    """Verify AGENTS.md contains database schema consistency rule content."""

    def _read_agents_md(self):
        with open(AGENTS_MD_PATH) as f:
            return f.read()

    def test_create_tables_mentioned(self):
        """Rule must reference _create_tables() method."""
        content = self._read_agents_md()
        assert '_create_tables()' in content

    def test_create_table_mentioned(self):
        """Rule must reference CREATE TABLE statements."""
        content = self._read_agents_md()
        assert 'CREATE TABLE' in content

    def test_alter_table_mentioned(self):
        """Rule must reference ALTER TABLE migrations."""
        content = self._read_agents_md()
        assert 'ALTER TABLE' in content

    def test_schema_consistency_concept(self):
        """Rule must mention checking all schema definition sites."""
        content = self._read_agents_md()
        assert 'ALL code paths' in content or 'all code paths' in content

    def test_column_set_agreement(self):
        """Rule must require all definitions to agree on columns."""
        content = self._read_agents_md()
        assert 'agree on the column' in content
