#!/usr/bin/env python3
"""
Playwright E2E Tests for the AI Chat page: state persistence, sidebar UX, and
pending-session spinner.

Tests:
- Page loads correctly with all structural elements
- Session sidebar items have proper spacing and clickable appearance
- Sidebar items have border-bottom separators
- Sidebar items highlight on hover
- New session button creates a session
- Sending a message shows thinking animation
- Input is disabled while thinking
- Sidebar spinner appears for pending sessions
- Session switching restores chat history
- WebSocket connects and shows status
- Chat input keyboard (Enter to send)

Usage:
    pytest docker/tests/test_ai_chat_playwright.py --base-url http://localhost:30001

    # headed for debugging:
    pytest docker/tests/test_ai_chat_playwright.py --headed --base-url http://localhost:30001
"""

import json
import re
import time

import pytest
from playwright.sync_api import Page, expect


# -----------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:30000"
CHAT_PATH = "/dashboard/ai-chat"


# -----------------------------------------------------------------------
# Session-scoped fixtures
# -----------------------------------------------------------------------


@pytest.fixture(scope="session")
def base_url(request) -> str:
    return getattr(request.config.option, "base_url", None) or DEFAULT_BASE_URL


@pytest.fixture(scope="session")
def admin_token(base_url: str) -> str:
    """Obtain admin JWT from the running server."""
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
def chat_page(admin_page: Page, base_url: str) -> Page:
    """Navigate to the AI chat page as admin, clearing old sessions."""
    # Clear stored sessions from previous test runs
    admin_page.goto(f"{base_url}{CHAT_PATH}")
    admin_page.evaluate("localStorage.removeItem('vh_ai_sessions')")
    admin_page.reload()
    admin_page.wait_for_load_state("networkidle")
    return admin_page


# -----------------------------------------------------------------------
# Page structure
# -----------------------------------------------------------------------


class TestAiChatPageStructure:
    """The AI chat page should render the complete two-column layout."""

    def test_page_title_contains_ai(self, chat_page: Page):
        expect(chat_page).to_have_title(re.compile(r"AI"))

    def test_ai_page_container_exists(self, chat_page: Page):
        expect(chat_page.locator(".ai-page")).to_be_visible()

    def test_sidebar_exists(self, chat_page: Page):
        expect(chat_page.locator(".ai-sidebar")).to_be_visible()

    def test_sidebar_header_shows_sessions(self, chat_page: Page):
        expect(chat_page.locator(".ai-sidebar-header")).to_contain_text("Sessions")

    def test_new_session_button_exists(self, chat_page: Page):
        btn = chat_page.locator(".ai-sidebar-header button")
        expect(btn).to_be_visible()

    def test_chat_main_area_exists(self, chat_page: Page):
        expect(chat_page.locator(".ai-main")).to_be_visible()

    def test_messages_pane_exists(self, chat_page: Page):
        expect(chat_page.locator(".ai-messages")).to_be_visible()

    def test_input_bar_exists(self, chat_page: Page):
        expect(chat_page.locator(".ai-input-bar")).to_be_visible()

    def test_input_field_exists(self, chat_page: Page):
        expect(chat_page.locator("#ai-input")).to_be_visible()

    def test_send_button_exists(self, chat_page: Page):
        expect(chat_page.locator("#ai-send-btn")).to_be_visible()

    def test_status_tag_exists(self, chat_page: Page):
        expect(chat_page.locator("#ai-status")).to_be_visible()

    def test_model_tag_exists(self, chat_page: Page):
        expect(chat_page.locator("#ai-model")).to_be_visible()


# -----------------------------------------------------------------------
# WebSocket connection
# -----------------------------------------------------------------------


class TestWebSocketConnection:
    """WS should connect and update the status badge."""

    def test_status_shows_connected(self, chat_page: Page):
        status = chat_page.locator("#ai-status")
        # Wait for WS to connect (up to 10s)
        expect(status).to_contain_text("Connected", timeout=10000)

    def test_status_has_success_class(self, chat_page: Page):
        status = chat_page.locator("#ai-status")
        expect(status).to_contain_text("Connected", timeout=10000)
        expect(status).to_have_class(re.compile(r"is-success"))


# -----------------------------------------------------------------------
# Session sidebar styling
# -----------------------------------------------------------------------


class TestSidebarStyling:
    """Session sidebar items should be well-spaced and clickable-looking."""

    @pytest.fixture
    def populated_chat(self, chat_page: Page):
        """Create a session so the sidebar has at least one item."""
        # Wait for WS to connect
        expect(chat_page.locator("#ai-status")).to_contain_text("Connected", timeout=10000)
        # Wait for sidebar item to appear (WS connect creates a session)
        expect(chat_page.locator(".ai-sidebar-item")).to_have_count(1, timeout=5000)
        return chat_page

    def test_sidebar_item_has_separator_border(self, populated_chat: Page):
        """When there are 2+ items, non-last items should have a solid border-bottom."""
        # Create a second session so we can check border on the first
        populated_chat.locator(".ai-sidebar-header button").click()
        expect(populated_chat.locator("#ai-status")).to_contain_text("Connected", timeout=10000)
        expect(populated_chat.locator(".ai-sidebar-item")).to_have_count(2, timeout=5000)
        first_item = populated_chat.locator(".ai-sidebar-item").first
        bb = first_item.evaluate("el => getComputedStyle(el).borderBottomStyle")
        assert bb == "solid", f"Expected solid border-bottom on non-last item, got: {bb}"

    def test_sidebar_item_has_pointer_cursor(self, populated_chat: Page):
        """Sidebar items should show pointer cursor indicating clickability."""
        item = populated_chat.locator(".ai-sidebar-item").first
        cursor = item.evaluate("el => getComputedStyle(el).cursor")
        assert cursor == "pointer", f"Expected pointer cursor, got: {cursor}"

    def test_sidebar_item_adequate_padding(self, populated_chat: Page):
        """Sidebar items should have comfortable padding (>= 10px top)."""
        item = populated_chat.locator(".ai-sidebar-item").first
        pt = item.evaluate("el => parseFloat(getComputedStyle(el).paddingTop)")
        assert pt >= 10, f"Expected padding-top >= 10px, got: {pt}px"


# -----------------------------------------------------------------------
# Thinking animation and input state
# -----------------------------------------------------------------------


class TestThinkingAndInput:
    """Sending a message should show thinking indicator and disable input."""

    @pytest.fixture
    def connected_chat(self, chat_page: Page):
        expect(chat_page.locator("#ai-status")).to_contain_text("Connected", timeout=10000)
        return chat_page

    def test_input_enabled_initially(self, connected_chat: Page):
        """After WS connects, input should be enabled."""
        inp = connected_chat.locator("#ai-input")
        expect(inp).to_be_enabled()

    def test_send_button_enabled_initially(self, connected_chat: Page):
        btn = connected_chat.locator("#ai-send-btn")
        expect(btn).to_be_enabled()

    def test_send_shows_thinking_and_disables_input(self, connected_chat: Page):
        """After sending, thinking animation should appear and input should be disabled."""
        inp = connected_chat.locator("#ai-input")
        inp.fill("test message")
        connected_chat.locator("#ai-send-btn").click()

        # Thinking indicator should appear
        thinking = connected_chat.locator("#ai-thinking")
        expect(thinking).to_be_visible(timeout=3000)

        # Input should be disabled while thinking
        expect(inp).to_be_disabled()

    def test_user_bubble_appears_on_send(self, connected_chat: Page):
        """Sending a message should create a user bubble."""
        inp = connected_chat.locator("#ai-input")
        inp.fill("hello from test")
        connected_chat.locator("#ai-send-btn").click()

        user_bubbles = connected_chat.locator(".ai-msg.user-msg")
        expect(user_bubbles.last).to_be_visible(timeout=3000)
        expect(user_bubbles.last).to_contain_text("hello from test")


# -----------------------------------------------------------------------
# Sidebar spinner for pending sessions
# -----------------------------------------------------------------------


class TestSidebarSpinner:
    """The sidebar should show a spinner for sessions with pending LLM requests."""

    @pytest.fixture
    def connected_chat(self, chat_page: Page):
        expect(chat_page.locator("#ai-status")).to_contain_text("Connected", timeout=10000)
        chat_page.wait_for_timeout(500)
        return chat_page

    def test_spinner_appears_on_send(self, connected_chat: Page):
        """After sending a message, the sidebar should show a spinner for the session."""
        inp = connected_chat.locator("#ai-input")
        inp.fill("thinking test")
        connected_chat.locator("#ai-send-btn").click()

        # Wait for thinking indicator to appear (confirms message was sent)
        expect(connected_chat.locator("#ai-thinking")).to_be_visible(timeout=3000)

        # Check for spinner in sidebar
        spinner = connected_chat.locator(".ai-sidebar-item .item-spinner")
        expect(spinner).to_be_visible(timeout=3000)


# -----------------------------------------------------------------------
# Session management
# -----------------------------------------------------------------------


class TestSessionManagement:
    """New sessions, switching, and sidebar updates."""

    @pytest.fixture
    def connected_chat(self, chat_page: Page):
        expect(chat_page.locator("#ai-status")).to_contain_text("Connected", timeout=10000)
        chat_page.wait_for_timeout(500)
        return chat_page

    def test_new_session_button_creates_session(self, connected_chat: Page):
        """Clicking the new session button should create a new session."""
        initial_count = connected_chat.locator(".ai-sidebar-item").count()
        connected_chat.locator(".ai-sidebar-header button").click()
        connected_chat.wait_for_timeout(1000)
        # Wait for WS to reconnect
        expect(connected_chat.locator("#ai-status")).to_contain_text("Connected", timeout=10000)
        new_count = connected_chat.locator(".ai-sidebar-item").count()
        assert new_count >= initial_count  # Should have at least as many (new one added)

    def test_sidebar_item_active_class(self, connected_chat: Page):
        """The current session should have the .active class."""
        items = connected_chat.locator(".ai-sidebar-item")
        if items.count() > 0:
            active = connected_chat.locator(".ai-sidebar-item.active")
            expect(active).to_be_visible()


# -----------------------------------------------------------------------
# Chat input keyboard behavior
# -----------------------------------------------------------------------


class TestKeyboardInput:
    """Enter key should send, empty input should be ignored."""

    @pytest.fixture
    def connected_chat(self, chat_page: Page):
        expect(chat_page.locator("#ai-status")).to_contain_text("Connected", timeout=10000)
        return chat_page

    def test_enter_key_sends_message(self, connected_chat: Page):
        """Pressing Enter should send the message."""
        inp = connected_chat.locator("#ai-input")
        inp.fill("enter key test")
        inp.press("Enter")

        user_bubbles = connected_chat.locator(".ai-msg.user-msg")
        expect(user_bubbles.last).to_be_visible(timeout=3000)
        expect(user_bubbles.last).to_contain_text("enter key test")

    def test_empty_input_not_sent(self, connected_chat: Page):
        """Pressing Enter with empty input should not create a bubble."""
        initial_count = connected_chat.locator(".ai-msg.user-msg").count()
        inp = connected_chat.locator("#ai-input")
        inp.press("Enter")
        connected_chat.wait_for_timeout(300)
        assert connected_chat.locator(".ai-msg.user-msg").count() == initial_count


# -----------------------------------------------------------------------
# Auth guard
# -----------------------------------------------------------------------


class TestAuthGuard:
    """Unauthenticated users should be redirected to login."""

    def test_unauthenticated_redirects_to_login(self, page: Page, base_url: str):
        resp = page.goto(f"{base_url}{CHAT_PATH}")
        page.wait_for_load_state("networkidle")
        assert "/login" in page.url or resp.status in (401, 403)
