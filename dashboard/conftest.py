"""Shared test configuration.

Adds the src directory to sys.path so that server.py's internal imports
(e.g. ``from storage.database import DashboardDatabase``) resolve correctly
when tests are run from the ``dashboard/`` directory.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
