"""Tests for the documentation dropdown menu in the dashboard header.

Verifies that:
1. The Docs dropdown button is present in the header navigation when
   GITHUB_REPO is configured.
2. The dropdown contains links to all documentation files with correct
   GitHub URLs.
3. The dropdown is hidden when GITHUB_REPO is not set.
4. The toggleDocsDropdown JavaScript function is defined.
5. The click-outside handler closes the docs dropdown.
"""

import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from unittest.mock import patch
from web.server import create_app


@pytest.fixture
def app_with_repo():
    """Create a test Flask app with GITHUB_REPO configured."""
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    with patch.dict(os.environ, {'GITHUB_REPO': 'org/test-repo'}):
        application = create_app(db_path=db_path, config_file=config_path)
        application.config['TESTING'] = True
        yield application
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def app_without_repo():
    """Create a test Flask app without GITHUB_REPO configured."""
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    env = {k: v for k, v in os.environ.items() if k != 'GITHUB_REPO'}
    with patch.dict(os.environ, env, clear=True):
        application = create_app(db_path=db_path, config_file=config_path)
        application.config['TESTING'] = True
        yield application
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client_with_repo(app_with_repo):
    return app_with_repo.test_client()


@pytest.fixture
def client_without_repo(app_without_repo):
    return app_without_repo.test_client()


def _get_html(client):
    response = client.get('/')
    assert response.status_code == 200
    return response.data.decode()


def _extract_script(html):
    """Extract the inline <script> block from dashboard HTML."""
    pattern = re.compile(
        r'<\s*script\b[^>]*>(.*?)<\s*/\s*script\b[^>]*>',
        re.IGNORECASE | re.DOTALL,
    )
    matches = pattern.findall(html)
    for block in reversed(matches):
        if 'fetchData' in block:
            return block
    raise AssertionError("No <script> block containing fetchData found")


class TestDocsDropdownPresent:
    """Verify the header contains a docs dropdown when GITHUB_REPO is set."""

    def test_docs_button_present(self, client_with_repo):
        """The Docs dropdown button appears in the header nav."""
        html = _get_html(client_with_repo)
        assert 'docsDropdownBtn' in html

    def test_docs_dropdown_menu_present(self, client_with_repo):
        """The dropdown menu container is rendered."""
        html = _get_html(client_with_repo)
        assert 'docsDropdownMenu' in html

    def test_readme_link(self, client_with_repo):
        """README.md link points to the correct GitHub URL."""
        html = _get_html(client_with_repo)
        expected = (
            'https://github.com/org/test-repo/blob/master'
            '/dashboard/README.md'
        )
        assert expected in html

    def test_user_guide_link(self, client_with_repo):
        """USER_GUIDE.md link is present with correct URL."""
        html = _get_html(client_with_repo)
        expected = (
            'https://github.com/org/test-repo/blob/master'
            '/dashboard/USER_GUIDE.md'
        )
        assert expected in html

    def test_quick_start_link(self, client_with_repo):
        """QUICK_START.md link is present with correct URL."""
        html = _get_html(client_with_repo)
        expected = (
            'https://github.com/org/test-repo/blob/master'
            '/dashboard/QUICK_START.md'
        )
        assert expected in html

    def test_vertex_ai_setup_link(self, client_with_repo):
        """VERTEX_AI_SETUP.md link is present with correct URL."""
        html = _get_html(client_with_repo)
        expected = (
            'https://github.com/org/test-repo/blob/master'
            '/dashboard/docs/VERTEX_AI_SETUP.md'
        )
        assert expected in html

    def test_ai_analysis_link(self, client_with_repo):
        """ai-analysis.md link is present with correct URL."""
        html = _get_html(client_with_repo)
        expected = (
            'https://github.com/org/test-repo/blob/master'
            '/dashboard/docs/ai-analysis.md'
        )
        assert expected in html

    def test_token_renewal_link(self, client_with_repo):
        """token-renewal.md link is present with correct URL."""
        html = _get_html(client_with_repo)
        expected = (
            'https://github.com/org/test-repo/blob/master'
            '/dashboard/docs/token-renewal.md'
        )
        assert expected in html

    def test_all_six_doc_links_present(self, client_with_repo):
        """All six documentation links are present in the dropdown."""
        html = _get_html(client_with_repo)
        base = 'https://github.com/org/test-repo/blob/master/dashboard'
        doc_paths = [
            '/README.md',
            '/USER_GUIDE.md',
            '/QUICK_START.md',
            '/docs/VERTEX_AI_SETUP.md',
            '/docs/ai-analysis.md',
            '/docs/token-renewal.md',
        ]
        for path in doc_paths:
            assert base + path in html, f"Missing doc link: {path}"

    def test_links_open_in_new_tab(self, client_with_repo):
        """Doc links use target='_blank' to open in a new tab."""
        html = _get_html(client_with_repo)
        # Find the docs dropdown section and check all links have
        # target="_blank"
        menu_start = html.find('docsDropdownMenu')
        assert menu_start > 0, "Docs dropdown menu not found"
        # Find the closing div after the menu
        menu_section = html[menu_start:menu_start + 2000]
        # Count links and target="_blank" within the section
        link_count = menu_section.count('href="https://github.com/')
        target_count = menu_section.count('target="_blank"')
        assert link_count == 6, f"Expected 6 doc links, found {link_count}"
        assert target_count >= 6, (
            f"Expected 6 target='_blank' attrs, found {target_count}"
        )


class TestDocsDropdownHidden:
    """Verify the docs dropdown is NOT rendered without GITHUB_REPO."""

    def test_no_docs_button_without_repo(self, client_without_repo):
        """The Docs dropdown button element should not appear when
        GITHUB_REPO is empty."""
        html = _get_html(client_without_repo)
        # Check the actual HTML element is absent, not just the ID
        # string (which may appear in JS code)
        assert 'id="docsDropdownBtn"' not in html

    def test_no_docs_menu_without_repo(self, client_without_repo):
        """The dropdown menu element should not appear when GITHUB_REPO
        is empty."""
        html = _get_html(client_without_repo)
        assert 'id="docsDropdownMenu"' not in html

    def test_no_doc_links_without_repo(self, client_without_repo):
        """No documentation links should be present without GITHUB_REPO."""
        html = _get_html(client_without_repo)
        assert 'USER_GUIDE.md' not in html
        assert 'QUICK_START.md' not in html


class TestDocsDropdownJavaScript:
    """Verify the JS toggle function and click-outside handler."""

    def test_toggle_function_defined(self, client_with_repo):
        """toggleDocsDropdown function is defined in the script block."""
        html = _get_html(client_with_repo)
        script = _extract_script(html)
        assert 'function toggleDocsDropdown' in script

    def test_toggle_function_toggles_show_class(self, client_with_repo):
        """toggleDocsDropdown toggles the 'show' class on the menu."""
        html = _get_html(client_with_repo)
        script = _extract_script(html)
        # Extract the function body
        func_start = script.find('function toggleDocsDropdown')
        assert func_start >= 0
        func_body = script[func_start:func_start + 500]
        assert 'classList.toggle' in func_body or "toggle('show')" in func_body

    def test_click_outside_closes_docs_menu(self, client_with_repo):
        """The click-outside handler removes the show class from
        docsDropdownMenu."""
        html = _get_html(client_with_repo)
        script = _extract_script(html)
        # The click handler should reference docsDropdownMenu
        assert 'docsDropdownMenu' in script
        assert "classList.remove('show')" in script
