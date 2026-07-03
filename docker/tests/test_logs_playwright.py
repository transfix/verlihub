#!/usr/bin/env python3
"""
Playwright E2E Tests for the System Logs page.

Tests:
- Page loads correctly (title, header, log viewer, controls)
- Filter controls (level, type, search, auto-scroll checkbox)
- Log entries rendering (badges, levels, types)
- Clear logs button calls API
- Download buttons exist
- WebSocket connection status indicator
- Scrollability of the log viewer
- Ring buffer pre-populated entries display on page load

Usage:
    pytest docker/tests/test_logs_playwright.py --base-url http://localhost:30001

    # headed for debugging:
    pytest docker/tests/test_logs_playwright.py --headed --base-url http://localhost:30001
"""

import json
import re

import pytest
from playwright.sync_api import Page, expect


# -----------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:30000"
LOGS_PATH = "/dashboard/logs"
LOGIN_PATH = "/dashboard/login"


# -----------------------------------------------------------------------
# Helpers — REST API wrappers (run in the SAME server process)
# -----------------------------------------------------------------------

def _api_request(base_url: str, method: str, path: str, token: str,
                 body: dict | None = None, timeout: int = 5) -> dict:
    """Make an authenticated REST API request to the running server."""
    import urllib.request
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _clear_buffer(base_url: str, token: str):
    """Clear the log buffer via API."""
    _api_request(base_url, "DELETE", "/api/v1/logs", token)


def _inject_entries(base_url: str, token: str, entries: list[dict]):
    """Inject log entries via API."""
    _api_request(base_url, "POST", "/api/v1/logs", token, body={"entries": entries})


# -----------------------------------------------------------------------
# Session-scoped fixtures
# -----------------------------------------------------------------------


@pytest.fixture(scope="session")
def base_url(request) -> str:
    return getattr(request.config.option, "base_url", None) or DEFAULT_BASE_URL


@pytest.fixture(scope="session")
def admin_token(base_url: str) -> str:
    """Obtain a valid admin JWT from the running server.

    The production config has a known admin user; we authenticate via the
    REST endpoint so the token is signed with the *running* server's secret.
    """
    import urllib.request

    for nick, pwd in [("admin", "clownworld2026!"), ("admin", "admin")]:
        try:
            data = json.dumps({"nick": nick, "password": pwd}).encode()
            req = urllib.request.Request(
                f"{base_url}/api/v1/auth/login",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
                if "access_token" in body:
                    return body["access_token"]
        except Exception:
            pass

    pytest.fail("Could not obtain admin token from server API")


# -----------------------------------------------------------------------
# Page fixtures
# -----------------------------------------------------------------------


@pytest.fixture(scope="function")
def admin_page(page: Page, base_url: str, admin_token: str) -> Page:
    """Set an admin auth cookie and return the page."""
    page.context.add_cookies([{
        "name": "access_token",
        "value": f"Bearer {admin_token}",
        "url": base_url,
        "httpOnly": True,
        "sameSite": "Lax",
    }])
    return page


@pytest.fixture(scope="function")
def logs_page(admin_page: Page, base_url: str) -> Page:
    """Navigate to the logs page as an admin user."""
    admin_page.goto(f"{base_url}{LOGS_PATH}")
    admin_page.wait_for_load_state("networkidle")
    return admin_page


# -----------------------------------------------------------------------
# Log seeding / clearing via API (in-process, no docker exec)
# -----------------------------------------------------------------------


@pytest.fixture(scope="function")
def seeded_logs(base_url: str, admin_token: str):
    """Ensure the ring buffer has entries via the REST API.

    The running hub naturally populates the buffer on startup (MOTD,
    listening, hub started, user connects, etc.).  We check via API and
    inject more entries if the buffer is too small.
    """
    result = _api_request(base_url, "GET", "/api/v1/logs?limit=1", admin_token)
    if result.get("total", 0) < 5:
        _inject_entries(base_url, admin_token, [
            {"level": "info", "message": "Hub started successfully", "log_type": "system"},
            {"level": "debug", "message": "Debug trace from core", "log_type": "core"},
            {"level": "info", "message": "User connected: alice", "log_type": "connection"},
            {"level": "warning", "message": "Rate limit exceeded", "log_type": "system"},
            {"level": "info", "message": "PM from bob to carol", "log_type": "pm"},
        ])
    yield


# =======================================================================
# Page Loading
# =======================================================================


class TestLogsPageLoading:
    """Test basic page load and structure."""

    def test_page_title_contains_logs(self, logs_page: Page):
        expect(logs_page).to_have_title(re.compile(r".*Logs.*"))

    def test_page_header_visible(self, logs_page: Page):
        header = logs_page.locator("h1.title")
        expect(header).to_be_visible()
        expect(header).to_contain_text("System Logs")

    def test_log_viewer_container_exists(self, logs_page: Page):
        viewer = logs_page.locator("#log-container")
        expect(viewer).to_be_visible()

    def test_refresh_button_visible(self, logs_page: Page):
        btn = logs_page.locator("button", has_text="Refresh")
        expect(btn).to_be_visible()

    def test_clear_button_visible(self, logs_page: Page):
        btn = logs_page.locator("button", has_text="Clear")
        expect(btn).to_be_visible()


# =======================================================================
# Filter Controls
# =======================================================================


class TestFilterControls:
    """Test that all filter controls are present and interactive."""

    def test_log_level_dropdown(self, logs_page: Page):
        sel = logs_page.locator("#log-level")
        expect(sel).to_be_visible()
        # Should have at least "All Levels" + some levels
        options = sel.locator("option")
        assert options.count() >= 3

    def test_log_type_dropdown(self, logs_page: Page):
        sel = logs_page.locator("#log-type")
        expect(sel).to_be_visible()
        options = sel.locator("option")
        assert options.count() >= 3

    def test_search_input(self, logs_page: Page):
        inp = logs_page.locator("#log-search")
        expect(inp).to_be_visible()
        inp.fill("test")
        assert inp.input_value() == "test"

    def test_autoscroll_checkbox(self, logs_page: Page):
        cb = logs_page.locator("#log-autoscroll")
        expect(cb).to_be_visible()
        # Default should be checked
        assert cb.is_checked()


# =======================================================================
# Log Entries Rendering (with pre-seeded buffer)
# =======================================================================


class TestLogEntriesRendering:
    """Test that historical log entries are rendered correctly."""

    def test_entries_visible_on_load(self, admin_page: Page, base_url: str, seeded_logs):
        """Pre-seeded entries should appear on page load."""
        admin_page.goto(f"{base_url}{LOGS_PATH}")
        admin_page.wait_for_load_state("networkidle")
        entries = admin_page.locator(".log-entry")
        assert entries.count() >= 1

    def test_no_placeholder_when_entries_exist(self, admin_page: Page, base_url: str, seeded_logs):
        """The 'no logs' placeholder should be hidden when entries are present."""
        admin_page.goto(f"{base_url}{LOGS_PATH}")
        admin_page.wait_for_load_state("networkidle")
        placeholder = admin_page.locator("#no-logs-placeholder")
        assert placeholder.count() == 0  # not rendered by Jinja when logs exist

    def test_placeholder_shown_when_empty(self, admin_page: Page, base_url: str, admin_token: str):
        """The 'no logs' placeholder should appear when buffer is empty."""
        _clear_buffer(base_url, admin_token)
        admin_page.goto(f"{base_url}{LOGS_PATH}")
        admin_page.wait_for_load_state("networkidle")
        placeholder = admin_page.locator("#no-logs-placeholder")
        expect(placeholder).to_be_visible()

    def test_entry_has_level_badge(self, admin_page: Page, base_url: str, seeded_logs):
        """Each log entry should have a compact level indicator."""
        admin_page.goto(f"{base_url}{LOGS_PATH}")
        admin_page.wait_for_load_state("networkidle")
        badges = admin_page.locator(".log-entry .ll")
        assert badges.count() >= 1

    def test_entry_has_type_badge(self, admin_page: Page, base_url: str, seeded_logs):
        """Each log entry should have a log_type label."""
        admin_page.goto(f"{base_url}{LOGS_PATH}")
        admin_page.wait_for_load_state("networkidle")
        type_badges = admin_page.locator(".log-entry .lt")
        assert type_badges.count() >= 1

    def test_entry_has_data_attributes(self, admin_page: Page, base_url: str, seeded_logs):
        """Log entries should have data-level and data-type attributes for filtering."""
        admin_page.goto(f"{base_url}{LOGS_PATH}")
        admin_page.wait_for_load_state("networkidle")
        first_entry = admin_page.locator(".log-entry").first
        assert first_entry.get_attribute("data-level") is not None
        assert first_entry.get_attribute("data-type") is not None

    def test_log_count_in_status_bar(self, admin_page: Page, base_url: str, seeded_logs):
        """The status bar should show the correct entry count."""
        admin_page.goto(f"{base_url}{LOGS_PATH}")
        admin_page.wait_for_load_state("networkidle")
        count_span = admin_page.locator("#log-count")
        text = count_span.inner_text()
        assert int(text) >= 1


# =======================================================================
# Filtering
# =======================================================================


class TestLogFiltering:
    """Test that filter controls actually filter visible entries."""

    def test_filter_by_level_hides_non_matching(self, admin_page: Page, base_url: str, seeded_logs):
        """Selecting a specific level should hide non-matching entries."""
        admin_page.goto(f"{base_url}{LOGS_PATH}")
        admin_page.wait_for_load_state("networkidle")

        # Select "info" level (most likely to have entries)
        admin_page.select_option("#log-level", "info")
        admin_page.wait_for_timeout(200)

        visible_entries = admin_page.locator('.log-entry:visible')
        if visible_entries.count() > 0:
            for i in range(visible_entries.count()):
                level = visible_entries.nth(i).get_attribute("data-level")
                assert level == "info", f"Entry has level '{level}', expected 'info'"

    def test_filter_by_type(self, admin_page: Page, base_url: str, seeded_logs):
        """Selecting a specific type should hide non-matching entries."""
        admin_page.goto(f"{base_url}{LOGS_PATH}")
        admin_page.wait_for_load_state("networkidle")

        # Filter by "core" which we know exists from C++ startup logs
        admin_page.select_option("#log-type", "core")
        admin_page.wait_for_timeout(200)

        visible_entries = admin_page.locator('.log-entry:visible')
        if visible_entries.count() > 0:
            for i in range(visible_entries.count()):
                log_type = visible_entries.nth(i).get_attribute("data-type")
                assert log_type == "core"

    def test_search_filter(self, admin_page: Page, base_url: str, admin_token: str):
        """Typing in the search box should filter entries by text."""
        # Inject a distinctive entry we can search for
        _clear_buffer(base_url, admin_token)
        _inject_entries(base_url, admin_token, [
            {"level": "info", "message": "UniqueSearchTarget12345", "log_type": "system"},
            {"level": "debug", "message": "Unrelated debug noise", "log_type": "core"},
            {"level": "info", "message": "Another normal message", "log_type": "system"},
        ])

        admin_page.goto(f"{base_url}{LOGS_PATH}")
        admin_page.wait_for_load_state("networkidle")

        # All 3 entries visible before search
        assert admin_page.locator(".log-entry").count() == 3

        admin_page.fill("#log-search", "UniqueSearchTarget")
        admin_page.wait_for_timeout(300)

        visible_entries = admin_page.locator('.log-entry:visible')
        assert visible_entries.count() == 1
        text = visible_entries.first.inner_text()
        assert "uniquesearchtarget" in text.lower()

    def test_filter_all_shows_everything(self, admin_page: Page, base_url: str, seeded_logs):
        """Setting filters back to 'all' should show all entries."""
        admin_page.goto(f"{base_url}{LOGS_PATH}")
        admin_page.wait_for_load_state("networkidle")

        total = admin_page.locator(".log-entry").count()
        # Filter down
        admin_page.select_option("#log-level", "debug")
        admin_page.wait_for_timeout(200)
        # Reset
        admin_page.select_option("#log-level", "all")
        admin_page.wait_for_timeout(200)

        assert admin_page.locator(".log-entry:visible").count() == total


# =======================================================================
# Download Buttons
# =======================================================================


class TestDownloadButtons:
    """Test that download buttons exist."""

    def test_download_text_button(self, logs_page: Page):
        expect(logs_page.locator("button", has_text="Download as Text")).to_be_visible()

    def test_download_json_button(self, logs_page: Page):
        expect(logs_page.locator("button", has_text="Download as JSON")).to_be_visible()

    def test_download_csv_button(self, logs_page: Page):
        expect(logs_page.locator("button", has_text="Download as CSV")).to_be_visible()


# =======================================================================
# WebSocket Status
# =======================================================================


class TestWebSocketStatus:
    """Test the WebSocket connection status indicator."""

    def test_ws_status_element_exists(self, logs_page: Page):
        """The WS status span should be present in the status bar."""
        ws_status = logs_page.locator("#ws-status")
        expect(ws_status).to_be_visible()

    def test_ws_status_shows_connection_state(self, logs_page: Page):
        """The WS status should show some connection state text."""
        ws_status = logs_page.locator("#ws-status")
        text = ws_status.inner_text()
        # Should be "Live", "Connecting…", or "Disconnected — reconnecting…"
        assert len(text) > 0


# =======================================================================
# Clear Logs
# =======================================================================


class TestClearLogs:
    """Test the clear logs functionality."""

    def test_clear_button_empties_viewer(self, admin_page: Page, base_url: str, seeded_logs):
        """Clicking Clear should empty the log viewer via API call."""
        admin_page.goto(f"{base_url}{LOGS_PATH}")
        admin_page.wait_for_load_state("networkidle")

        # Pre-check: entries exist
        assert admin_page.locator(".log-entry").count() >= 1

        # Click the Clear button — this opens a confirmation modal
        admin_page.click("button:has-text('Clear')")

        # Click the Confirm button in the modal and intercept the DELETE call
        confirm_btn = admin_page.locator("#confirm-btn")
        confirm_btn.wait_for(state="visible", timeout=5000)

        with admin_page.expect_response(
            lambda r: "/api/v1/logs" in r.url and r.request.method == "DELETE"
        ) as resp_info:
            confirm_btn.click()

        resp = resp_info.value
        assert resp.status == 200
        body = resp.json()
        assert body["cleared"] >= 1


# =======================================================================
# Scrollability
# =======================================================================


class TestScrollability:
    """Test that the log viewer is scrollable."""

    def test_viewer_has_overflow_scroll(self, logs_page: Page):
        """The log container should have overflow-y: auto or scroll."""
        overflow = logs_page.locator("#log-container").evaluate(
            "el => getComputedStyle(el).overflowY"
        )
        assert overflow in ("auto", "scroll")

    def test_viewer_has_min_height(self, logs_page: Page):
        """The log container should have a reasonable minimum height."""
        min_h = logs_page.locator("#log-container").evaluate(
            "el => parseInt(getComputedStyle(el).minHeight, 10)"
        )
        assert min_h >= 300

    def test_many_entries_are_scrollable(self, admin_page: Page, base_url: str, admin_token: str):
        """With many entries, the viewer should be scrollable."""
        _clear_buffer(base_url, admin_token)
        _inject_entries(base_url, admin_token, [
            {"level": "info", "message": f"Scroll test entry {i}", "log_type": "system"}
            for i in range(200)
        ])

        admin_page.goto(f"{base_url}{LOGS_PATH}")
        admin_page.wait_for_load_state("networkidle")

        container = admin_page.locator("#log-container")
        scroll_height = container.evaluate("el => el.scrollHeight")
        client_height = container.evaluate("el => el.clientHeight")
        assert scroll_height > client_height, (
            f"Expected scrollable: scrollHeight={scroll_height} > clientHeight={client_height}"
        )
        # Clean up
        _clear_buffer(base_url, admin_token)


# =======================================================================
# Auth Guard
# =======================================================================


class TestLogsAuthGuard:
    """Test that the logs page requires authentication."""

    def test_unauthenticated_redirects_to_login(self, page: Page, base_url: str):
        """Accessing /dashboard/logs without auth should redirect to login."""
        # Use a fresh context with no cookies
        page.context.clear_cookies()
        resp = page.goto(f"{base_url}{LOGS_PATH}")
        # Should redirect to login or show 401/403
        url = page.url
        assert "/login" in url or resp.status in (401, 403), (
            f"Expected redirect to login, got {url} (status {resp.status})"
        )
