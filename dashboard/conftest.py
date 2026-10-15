"""Shared test configuration.

Adds the src directory to sys.path so that server.py's internal imports
(e.g. ``from storage.database import DashboardDatabase``) resolve correctly
when tests are run from the ``dashboard/`` directory.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))


def extract_function_body(script, func_name):
    """Extract the body of a named JS function from the script source.

    Handles both sync and async function declarations.  Returns the
    full brace-delimited body (including the outer braces).
    """
    pattern = (
        r'(?:async\s+)?function\s+'
        + re.escape(func_name)
        + r'\s*\([^)]*\)\s*\{'
    )
    match = re.search(pattern, script)
    if not match:
        raise AssertionError(f"Function {func_name} not found in script")

    brace_start = match.end() - 1
    depth = 1
    pos = brace_start + 1
    while pos < len(script) and depth > 0:
        if script[pos] == '{':
            depth += 1
        elif script[pos] == '}':
            depth -= 1
        pos += 1

    return script[brace_start:pos]
