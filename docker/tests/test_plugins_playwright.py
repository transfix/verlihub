#!/usr/bin/env python3
"""
Playwright E2E Tests for Verlihub Plugins Page — Lua Scripts & Ledokol UI

Tests the plugins management page frontend including:
- Plugin table display
- Python Scripts section
- Lua Scripts section (load/unload/reload buttons, new script input)
- Ledokol management panel (status, quick actions, feature cards, command input)
- Ledokol command execution and output display

Usage:
    # Against a running dashboard with auth
    pytest docker/tests/test_plugins_playwright.py --base-url http://localhost:30000

    # With visible browser for debugging
    pytest docker/tests/test_plugins_playwright.py --headed --base-url http://localhost:30000
"""

import pytest
import re
from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:30000"
LOGIN_PATH = "/dashboard/login"
PLUGINS_PATH = "/dashboard/plugins"

# Default admin credentials for test environment
ADMIN_USER = "admin"
ADMIN_PASS = "admin"


@pytest.fixture(scope="session")
def base_url(request) -> str:
    """Get base URL from pytest option or use default."""
    import os
    return os.environ.get("DASHBOARD_URL") or getattr(
        request.config.option, "base_url", None
    ) or DEFAULT_BASE_URL


@pytest.fixture(scope="function")
def authenticated_page(page: Page, base_url: str) -> Page:
    """Log in as admin and return the authenticated page."""
    page.goto(f"{base_url}{LOGIN_PATH}")
    page.fill('input[name="username"]', ADMIN_USER)
    page.fill('input[name="password"]', ADMIN_PASS)
    page.click('button[type="submit"]')
    # Wait for redirect after login
    page.wait_for_url(re.compile(r"/dashboard"), timeout=10000)
    return page


@pytest.fixture(scope="function")
def plugins_page(authenticated_page: Page, base_url: str) -> Page:
    """Navigate to the plugins page (authenticated)."""
    authenticated_page.goto(f"{base_url}{PLUGINS_PATH}")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# ---------------------------------------------------------------------------
# Plugins Page Structure
# ---------------------------------------------------------------------------


class TestPluginsPageStructure:
    """Test the overall plugins page structure."""

    def test_plugins_page_loads(self, plugins_page: Page):
        """Test that the plugins page loads successfully."""
        expect(plugins_page).to_have_title(re.compile(r"Plugin", re.IGNORECASE))

    def test_has_plugins_table(self, plugins_page: Page):
        """Test that a loaded-plugins table is present."""
        table = plugins_page.locator("#plugins-body")
        expect(table).to_be_visible()

    def test_has_python_scripts_section(self, plugins_page: Page):
        """Test that the Python Scripts section exists."""
        heading = plugins_page.locator("text=Python Scripts")
        expect(heading).to_be_visible()

    def test_has_python_scripts_table(self, plugins_page: Page):
        """Test that the Python Scripts table body exists."""
        table = plugins_page.locator("#scripts-body")
        expect(table).to_be_visible()

    def test_has_new_python_script_input(self, plugins_page: Page):
        """Test that the new-script input field exists."""
        input_el = plugins_page.locator("#new-script-path")
        expect(input_el).to_be_visible()
        expect(input_el).to_have_attribute("placeholder", re.compile(r"script"))


# ---------------------------------------------------------------------------
# Lua Scripts Section
# ---------------------------------------------------------------------------


class TestLuaScriptsSection:
    """Test the Lua Scripts UI section."""

    def test_has_lua_scripts_heading(self, plugins_page: Page):
        """Test that the Lua Scripts heading is present."""
        heading = plugins_page.locator("text=Lua Scripts")
        expect(heading).to_be_visible()

    def test_has_lua_scripts_table(self, plugins_page: Page):
        """Test that the Lua Scripts table body exists."""
        table = plugins_page.locator("#lua-scripts-body")
        expect(table).to_be_visible()

    def test_lua_table_has_columns(self, plugins_page: Page):
        """Test that the Lua table has Script, Status, and Actions columns."""
        lua_box = plugins_page.locator("#lua-scripts-body").locator("..")
        # Walk up to the table
        lua_table = lua_box.locator("xpath=ancestor::table[1]")
        headers = lua_table.locator("thead th")
        header_texts = [h.text_content().strip() for h in headers.all()]
        assert "Script" in header_texts
        assert "Status" in header_texts
        assert "Actions" in header_texts

    def test_has_new_lua_script_input(self, plugins_page: Page):
        """Test that the new Lua script input exists."""
        input_el = plugins_page.locator("#new-lua-script")
        expect(input_el).to_be_visible()
        expect(input_el).to_have_attribute(
            "placeholder", re.compile(r"ledokol\.lua|script", re.IGNORECASE)
        )

    def test_load_lua_button_exists(self, plugins_page: Page):
        """Test that the Load Lua Script button is present beside the input."""
        btn = plugins_page.locator("button:has-text('Load Lua Script')")
        expect(btn).to_be_visible()

    def test_lua_script_rows_have_action_buttons(self, plugins_page: Page):
        """If any Lua scripts are listed, they should have action buttons."""
        rows = plugins_page.locator("#lua-scripts-body tr")
        count = rows.count()
        if count == 0:
            pytest.skip("No Lua scripts listed")
        for i in range(count):
            row = rows.nth(i)
            # Each row should have at least one button (load, reload, or unload)
            buttons = row.locator("button")
            assert buttons.count() >= 1, f"Row {i} has no action buttons"

    def test_ledokol_link_present_if_listed(self, plugins_page: Page):
        """If ledokol.lua is listed, it should have a GitHub link."""
        row = plugins_page.locator("#lua-scripts-body tr:has-text('ledokol')")
        if row.count() == 0:
            pytest.skip("ledokol.lua not in script list")
        link = row.locator("a[href*='github.com']")
        expect(link).to_be_visible()


# ---------------------------------------------------------------------------
# Ledokol Management Panel
# ---------------------------------------------------------------------------


class TestLedokolPanel:
    """Test the Ledokol management section."""

    def test_ledokol_section_exists(self, plugins_page: Page):
        """Test that the ledokol management section is present."""
        section = plugins_page.locator("#ledokol-section")
        expect(section).to_be_visible()

    def test_ledokol_heading(self, plugins_page: Page):
        """Test that the Ledokol heading includes the name and GitHub link."""
        section = plugins_page.locator("#ledokol-section")
        heading = section.locator("h2")
        expect(heading).to_contain_text("Ledokol")
        github_link = heading.locator("a[href*='github.com/Verlihub/ledokol']")
        expect(github_link).to_be_visible()

    def test_ledokol_description(self, plugins_page: Page):
        """Test the descriptive paragraph is present."""
        section = plugins_page.locator("#ledokol-section")
        desc = section.locator("p:has-text('RoLex')")
        expect(desc).to_be_visible()
        expect(desc).to_contain_text("70+")

    def test_status_indicator_exists(self, plugins_page: Page):
        """Test the status indicator box is present."""
        status_box = plugins_page.locator("#ledokol-status-box")
        expect(status_box).to_be_visible()

    def test_status_indicator_has_content(self, plugins_page: Page):
        """Test the status indicator shows some content."""
        status = plugins_page.locator("#ledokol-status")
        expect(status).to_be_visible()
        # Could be 'Checking...', 'loaded', or 'not loaded'
        content = status.text_content()
        assert content is not None and len(content.strip()) > 0

    def test_quick_actions_section(self, plugins_page: Page):
        """Test quick action buttons are present."""
        section = plugins_page.locator("#ledokol-section")
        help_btn = section.locator("button:has-text('Help')")
        stats_btn = section.locator("button:has-text('Stats')")
        config_btn = section.locator("button:has-text('Config')")
        expect(help_btn).to_be_visible()
        expect(stats_btn).to_be_visible()
        expect(config_btn).to_be_visible()


class TestLedokolFeatureCards:
    """Test the Ledokol feature category cards."""

    def test_chat_card_exists(self, plugins_page: Page):
        """Test the Chat feature card is present."""
        card = plugins_page.locator(".card:has(.card-header-title:has-text('Chat'))")
        expect(card).to_be_visible()
        # Should have command buttons
        buttons = card.locator("button")
        assert buttons.count() >= 3, "Chat card should have multiple command buttons"

    def test_content_card_exists(self, plugins_page: Page):
        """Test the Content feature card is present."""
        card = plugins_page.locator(".card:has(.card-header-title:has-text('Content'))")
        expect(card).to_be_visible()

    def test_security_card_exists(self, plugins_page: Page):
        """Test the Security feature card is present."""
        card = plugins_page.locator(".card:has(.card-header-title:has-text('Security'))")
        expect(card).to_be_visible()

    def test_users_card_exists(self, plugins_page: Page):
        """Test the Users feature card is present."""
        card = plugins_page.locator(".card:has(.card-header-title:has-text('Users'))")
        expect(card).to_be_visible()

    def test_hub_card_exists(self, plugins_page: Page):
        """Test the Hub feature card is present."""
        card = plugins_page.locator(".card:has(.card-header-title:has-text('Hub'))")
        expect(card).to_be_visible()

    def test_chat_card_has_say_button(self, plugins_page: Page):
        """Test the Chat card has a !say button."""
        card = plugins_page.locator(".card:has(.card-header-title:has-text('Chat'))")
        say_btn = card.locator("button:has-text('!say')")
        expect(say_btn).to_be_visible()

    def test_security_card_has_antilist(self, plugins_page: Page):
        """Test the Security card has a !antilist button."""
        card = plugins_page.locator(".card:has(.card-header-title:has-text('Security'))")
        btn = card.locator("button:has-text('!antilist')")
        expect(btn).to_be_visible()


# ---------------------------------------------------------------------------
# Ledokol Command Input & Output
# ---------------------------------------------------------------------------


class TestLedokolCommandInterface:
    """Test the Ledokol command input and output display."""

    def test_command_input_exists(self, plugins_page: Page):
        """Test the command input field is present."""
        input_el = plugins_page.locator("#ledokol-command")
        expect(input_el).to_be_visible()
        expect(input_el).to_have_attribute(
            "placeholder", re.compile(r"ledoset|command", re.IGNORECASE)
        )

    def test_send_button_exists(self, plugins_page: Page):
        """Test the Send button is present."""
        btn = plugins_page.locator("button:has-text('Send')")
        expect(btn).to_be_visible()

    def test_output_hidden_initially(self, plugins_page: Page):
        """Test that the output section is hidden before any command is sent."""
        wrapper = plugins_page.locator("#ledokol-output-wrapper")
        expect(wrapper).to_be_hidden()

    def test_output_pre_exists(self, plugins_page: Page):
        """Test the output pre element is in the DOM."""
        output = plugins_page.locator("#ledokol-output")
        # It exists in DOM but the wrapper is hidden
        assert output.count() == 1

    def test_enter_key_triggers_send(self, plugins_page: Page):
        """Test that pressing Enter in the command input triggers submission."""
        input_el = plugins_page.locator("#ledokol-command")
        # Has onkeypress handler
        expect(input_el).to_have_attribute("onkeypress", re.compile(r"Enter"))

    def test_typing_command_preserves_value(self, plugins_page: Page):
        """Test that typing into the command input works."""
        input_el = plugins_page.locator("#ledokol-command")
        input_el.fill("!ledohelp")
        expect(input_el).to_have_value("!ledohelp")


# ---------------------------------------------------------------------------
# Interaction Tests (require live API)
# ---------------------------------------------------------------------------


class TestLedokolInteractions:
    """Test interactions that require a running API backend.

    These tests send actual commands and verify output display.
    They may be skipped if the API is not available.
    """

    def _check_api_available(self, page: Page, base_url: str) -> bool:
        """Check if the API is available."""
        try:
            resp = page.request.get(f"{base_url}/api/v1/health")
            return resp.ok
        except Exception:
            return False

    def test_quick_action_shows_output(self, plugins_page: Page, base_url: str):
        """Test that clicking a quick-action button displays output."""
        if not self._check_api_available(plugins_page, base_url):
            pytest.skip("API not available")

        # Click the Help button
        section = plugins_page.locator("#ledokol-section")
        help_btn = section.locator("button:has-text('Help')")
        help_btn.click()

        # Wait for output wrapper to become visible
        wrapper = plugins_page.locator("#ledokol-output-wrapper")
        wrapper.wait_for(state="visible", timeout=5000)

        output = plugins_page.locator("#ledokol-output")
        content = output.text_content()
        assert content is not None and len(content.strip()) > 0

    def test_send_command_shows_output(self, plugins_page: Page, base_url: str):
        """Test sending a custom command shows output."""
        if not self._check_api_available(plugins_page, base_url):
            pytest.skip("API not available")

        input_el = plugins_page.locator("#ledokol-command")
        input_el.fill("!ledostats")

        send_btn = plugins_page.locator("button:has-text('Send')")
        send_btn.click()

        # Output should appear
        wrapper = plugins_page.locator("#ledokol-output-wrapper")
        wrapper.wait_for(state="visible", timeout=5000)

        output = plugins_page.locator("#ledokol-output")
        content = output.text_content()
        assert content is not None and "!ledostats" in content

    def test_command_input_clears_after_send(self, plugins_page: Page, base_url: str):
        """Test that the command input is cleared after sending."""
        if not self._check_api_available(plugins_page, base_url):
            pytest.skip("API not available")

        input_el = plugins_page.locator("#ledokol-command")
        input_el.fill("!ledoconf")

        send_btn = plugins_page.locator("button:has-text('Send')")
        send_btn.click()

        # Input should be cleared
        plugins_page.wait_for_timeout(500)
        expect(input_el).to_have_value("")

    def test_feature_card_button_sends_command(self, plugins_page: Page, base_url: str):
        """Test that clicking a feature card button sends the command."""
        if not self._check_api_available(plugins_page, base_url):
            pytest.skip("API not available")

        card = plugins_page.locator(".card:has(.card-header-title:has-text('Chat'))")
        calc_btn = card.locator("button:has-text('+calc')")
        calc_btn.click()

        wrapper = plugins_page.locator("#ledokol-output-wrapper")
        wrapper.wait_for(state="visible", timeout=5000)

        output = plugins_page.locator("#ledokol-output")
        content = output.text_content()
        assert content is not None and len(content.strip()) > 0


# ---------------------------------------------------------------------------
# Responsive / Layout Tests
# ---------------------------------------------------------------------------


class TestPluginsPageLayout:
    """Test layout and responsive behaviour."""

    def test_feature_cards_in_columns(self, plugins_page: Page):
        """Test that feature cards are arranged in columns."""
        columns = plugins_page.locator("#ledokol-section .columns .column")
        assert columns.count() >= 5, "Should have at least 5 feature columns"

    def test_page_scrolls_to_ledokol(self, plugins_page: Page):
        """Test that the page can scroll to the ledokol section."""
        section = plugins_page.locator("#ledokol-section")
        section.scroll_into_view_if_needed()
        expect(section).to_be_in_viewport()

    def test_no_console_errors(self, plugins_page: Page):
        """Test that no JS errors are logged on page load."""
        errors = []
        plugins_page.on("pageerror", lambda exc: errors.append(str(exc)))
        # Reload to capture errors
        plugins_page.reload()
        plugins_page.wait_for_load_state("networkidle")
        assert len(errors) == 0, f"Page errors: {errors}"
