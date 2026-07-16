"""Static checks for dashboard configuration completeness."""

import ast
import re
from pathlib import Path

import yaml


DASHBOARD_DIR = Path(__file__).parent
CONFIG_PATH = DASHBOARD_DIR / 'config.yaml'
SERVER_PATH = DASHBOARD_DIR / 'src' / 'web' / 'server.py'


def _server_config_get_sections():
    """Return top-level sections read via config.get('section', ...)."""
    tree = ast.parse(SERVER_PATH.read_text())
    sections = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == 'get'
            and isinstance(func.value, ast.Name)
            and func.value.id == 'config'
        ):
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if (
            isinstance(first_arg, ast.Constant)
            and isinstance(first_arg.value, str)
        ):
            sections.add(first_arg.value)

    return sections


def _documented_top_level_sections():
    """Return top-level config sections, including commented-out sections."""
    section_pattern = re.compile(r'^(?:#\s*)?([A-Za-z_][\w-]*):(?:\s|$)')
    sections = set()

    for line in CONFIG_PATH.read_text().splitlines():
        match = section_pattern.match(line)
        if match:
            sections.add(match.group(1))

    return sections


def test_server_config_get_sections_are_documented():
    """Every top-level config.get section in server.py is documented."""
    referenced_sections = _server_config_get_sections()
    documented_sections = _documented_top_level_sections()

    missing_sections = referenced_sections - documented_sections
    assert not missing_sections


def test_backfill_rate_limit_documented_default_is_0_3():
    """backfill.rate_limit is documented with the server.py default."""
    config = yaml.safe_load(CONFIG_PATH.read_text())

    assert config['backfill']['rate_limit'] == 0.3
