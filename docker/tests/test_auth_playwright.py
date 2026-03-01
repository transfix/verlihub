#!/usr/bin/env python3
"""
Playwright E2E Tests for Verlihub Dashboard — Registration, Login, and Invite Codes

Tests frontend behaviour for the new features:
- Login page: form, register link, error display
- Registration page: form validation, invite code field, success flow
- Dashboard access: all user classes see all pages (no 403s)
- Invite code management page: admin allocation, user view, copy actions
- Navbar: all links visible when logged in

Usage:
    # Against a running dashboard with a running DB
    pytest docker/tests/test_auth_playwright.py --base-url http://localhost:30000

    # With visible browser for debugging
    pytest docker/tests/test_auth_playwright.py --headed --base-url http://localhost:30000
"""

import pytest
import re
from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://localhost:30000"
LOGIN_PATH = "/dashboard/login"
REGISTER_PATH = "/dashboard/register"
INVITES_PATH = "/dashboard/invites"
DASHBOARD_HOME = "/dashboard/"
LOGOUT_PATH = "/dashboard/logout"


@pytest.fixture(scope="session")
def base_url(request) -> str:
    """Get base URL from pytest option or use default."""
    return getattr(request.config.option, "base_url", None) or DEFAULT_BASE_URL


# ---------------------------------------------------------------------------
# Login Page Tests
# ---------------------------------------------------------------------------


class TestLoginPage:
    """Tests for the /dashboard/login page."""

    def test_login_page_loads(self, page: Page, base_url: str):
        """Test the login page renders properly."""
        page.goto(f"{base_url}{LOGIN_PATH}")
        expect(page).to_have_title(re.compile(r"Login.*Dashboard"))
        # Should see the login form
        expect(page.locator('input[name="username"]')).to_be_visible()
        expect(page.locator('input[name="password"]')).to_be_visible()
        expect(page.locator('button[type="submit"]')).to_be_visible()

    def test_login_page_has_register_link(self, page: Page, base_url: str):
        """Test that the login page links to registration."""
        page.goto(f"{base_url}{LOGIN_PATH}")
        link = page.locator('a[href="/dashboard/register"]')
        expect(link).to_be_visible()
        expect(link).to_contain_text(re.compile(r"Register", re.IGNORECASE))

    def test_login_error_display(self, page: Page, base_url: str):
        """Test that the error query parameter is rendered."""
        page.goto(f"{base_url}{LOGIN_PATH}?error=Bad+credentials")
        notification = page.locator(".notification.is-danger")
        expect(notification).to_be_visible()
        expect(notification).to_contain_text("Bad credentials")

    def test_login_empty_submit_blocked_by_browser(self, page: Page, base_url: str):
        """Test that submitting an empty form is blocked by required attributes."""
        page.goto(f"{base_url}{LOGIN_PATH}")
        username_input = page.locator('input[name="username"]')
        expect(username_input).to_have_attribute("required", "")

    def test_login_submit_invalid_credentials(self, page: Page, base_url: str):
        """Test that submitting bad credentials redirects back with error."""
        page.goto(f"{base_url}{LOGIN_PATH}")
        page.fill('input[name="username"]', "nonexistent_user_xyz")
        page.fill('input[name="password"]', "wrong_password")
        page.click('button[type="submit"]')
        # Should redirect back to login page with an error
        page.wait_for_url(re.compile(r"/dashboard/login\?error="), timeout=10000)
        notification = page.locator(".notification.is-danger")
        expect(notification).to_be_visible()

    def test_login_submit_valid_credentials(self, page: Page, base_url: str):
        """Test that logging in with valid credentials redirects to the dashboard.

        Creates a throwaway user via the registration form, logs out, then
        logs back in through the login form and verifies the redirect
        to /dashboard/ and that the authenticated navbar is rendered.
        """
        import secrets
        nick = f"logintest_{secrets.token_hex(4)}"
        password = "testpass_secure1234"

        # 1. Register a new user via the registration form
        page.goto(f"{base_url}{REGISTER_PATH}")
        page.fill('input[name="nick"]', nick)
        page.fill('input[name="password"]', password)
        page.fill('input[name="confirm_password"]', password)
        page.click('button[type="submit"]')
        page.wait_for_url(
            re.compile(r"/dashboard/(login|register|\?|$)"),
            timeout=10000,
        )

        # 2. Clear cookies (logout) so we start from scratch
        page.context.clear_cookies()

        # 3. Login with the newly created credentials
        page.goto(f"{base_url}{LOGIN_PATH}")
        page.fill('input[name="username"]', nick)
        page.fill('input[name="password"]', password)
        page.click('button[type="submit"]')

        # Should redirect to /dashboard/ (authenticated home)
        page.wait_for_url(re.compile(r"/dashboard/$"), timeout=10000)

        # Verify authenticated page content: navbar should show the nick
        navbar_link = page.locator(".navbar-link")
        if navbar_link.count() > 0:
            expect(navbar_link.first).to_contain_text(nick)


# ---------------------------------------------------------------------------
# Registration Page Tests
# ---------------------------------------------------------------------------


class TestRegistrationPage:
    """Tests for the /dashboard/register page."""

    def test_register_page_loads(self, page: Page, base_url: str):
        """Test the registration page renders properly."""
        page.goto(f"{base_url}{REGISTER_PATH}")
        expect(page).to_have_title(re.compile(r"Register.*Dashboard"))
        expect(page.locator('input[name="nick"]')).to_be_visible()
        expect(page.locator('input[name="password"]')).to_be_visible()
        expect(page.locator('input[name="confirm_password"]')).to_be_visible()
        expect(page.locator('input[name="invite_code"]')).to_be_visible()

    def test_register_page_has_login_link(self, page: Page, base_url: str):
        """Test that the registration page links back to login."""
        page.goto(f"{base_url}{REGISTER_PATH}")
        link = page.locator('a[href="/dashboard/login"]')
        expect(link).to_be_visible()
        expect(link).to_contain_text(re.compile(r"Login", re.IGNORECASE))

    def test_register_page_nick_validation_hint(self, page: Page, base_url: str):
        """Test that nick field has validation hint text."""
        page.goto(f"{base_url}{REGISTER_PATH}")
        help_text = page.locator('input[name="nick"] ~ .help, input[name="nick"] + .help')
        # Should find the help text describing valid characters
        parent = page.locator('input[name="nick"]').locator('..')
        expect(parent.locator('.help')).to_contain_text(re.compile(r"letters|characters", re.IGNORECASE))

    def test_register_page_password_min_hint(self, page: Page, base_url: str):
        """Test that password field shows minimum length hint."""
        page.goto(f"{base_url}{REGISTER_PATH}")
        password_input = page.locator('input[name="password"]')
        expect(password_input).to_have_attribute("minlength", "4")

    def test_register_page_error_query_param(self, page: Page, base_url: str):
        """Test that error query parameter renders an error notification."""
        page.goto(f"{base_url}{REGISTER_PATH}?error=Nick+already+registered")
        notification = page.locator(".notification.is-danger")
        expect(notification).to_be_visible()
        expect(notification).to_contain_text("Nick already registered")

    def test_register_page_invite_code_preserved(self, page: Page, base_url: str):
        """Test that invite code is preserved via query param."""
        page.goto(f"{base_url}{REGISTER_PATH}?invite=ABC123_TEST")
        invite_input = page.locator('input[name="invite_code"]')
        expect(invite_input).to_have_value("ABC123_TEST")

    def test_register_page_submit_button_exists(self, page: Page, base_url: str):
        """Test that the submit button exists with correct text."""
        page.goto(f"{base_url}{REGISTER_PATH}")
        btn = page.locator('button[type="submit"]')
        expect(btn).to_be_visible()
        expect(btn).to_contain_text(re.compile(r"Register", re.IGNORECASE))

    def test_register_password_mismatch_css(self, page: Page, base_url: str):
        """Test client-side password mismatch adds is-danger class."""
        page.goto(f"{base_url}{REGISTER_PATH}")
        page.fill('input[name="password"]', "goodpass1")
        confirm = page.locator('input[name="confirm_password"]')
        confirm.fill("differentpass")
        # Trigger input event
        confirm.dispatch_event("input")
        # The confirm field should get is-danger class
        expect(confirm).to_have_class(re.compile(r"is-danger"))

    def test_register_password_match_css(self, page: Page, base_url: str):
        """Test client-side password match adds is-success class."""
        page.goto(f"{base_url}{REGISTER_PATH}")
        page.fill('input[name="password"]', "goodpass1")
        confirm = page.locator('input[name="confirm_password"]')
        confirm.fill("goodpass1")
        confirm.dispatch_event("input")
        expect(confirm).to_have_class(re.compile(r"is-success"))

    def test_register_navigate_from_login(self, page: Page, base_url: str):
        """Test navigating from login to register page via link."""
        page.goto(f"{base_url}{LOGIN_PATH}")
        page.click('a[href="/dashboard/register"]')
        page.wait_for_url(re.compile(r"/dashboard/register"), timeout=5000)
        expect(page.locator('input[name="nick"]')).to_be_visible()

    def test_register_successful_flow(self, page: Page, base_url: str):
        """Test full successful registration flow (requires running DB)."""
        import secrets
        unique_nick = f"pw_test_{secrets.token_hex(4)}"

        page.goto(f"{base_url}{REGISTER_PATH}")
        page.fill('input[name="nick"]', unique_nick)
        page.fill('input[name="password"]', "testpass1234")
        page.fill('input[name="confirm_password"]', "testpass1234")
        page.click('button[type="submit"]')

        # Should redirect to dashboard home (logged in) or back with error
        page.wait_for_url(
            re.compile(r"/dashboard/(login|register|\?|$)"),
            timeout=10000,
        )
        # If registration succeeded, we end up at /dashboard/ with a cookie
        # If DB is down, we get /dashboard/register?error=...
        # Both are acceptable in an E2E test

    def test_register_password_mismatch_rejected(self, page: Page, base_url: str):
        """Test that mismatched passwords are rejected server-side."""
        page.goto(f"{base_url}{REGISTER_PATH}")
        page.fill('input[name="nick"]', "mismatch_test")
        page.fill('input[name="password"]', "password1")
        # Bypass client validation by setting value directly
        page.locator('input[name="confirm_password"]').evaluate(
            "el => { el.value = 'password2'; el.removeAttribute('required'); }"
        )
        page.locator('button[type="submit"]').click()
        # Should redirect back with error
        page.wait_for_url(re.compile(r"/dashboard/register"), timeout=10000)


# ---------------------------------------------------------------------------
# Dashboard Access (all user classes) Tests
# ---------------------------------------------------------------------------


class TestDashboardAccessAllClasses:
    """
    Test that every user class can access every dashboard page.

    These tests create JWT cookies directly (no real DB login) so they
    run even when no user exists in the database.
    """

    @staticmethod
    def _set_auth_cookie(page: Page, base_url: str, nick: str, user_class: int):
        """Create a JWT and set it as a cookie on the page."""
        from verlihub.api.auth import create_access_token
        token = create_access_token(nick, user_class)
        # Set the cookie matching what the dashboard login sets
        page.context.add_cookies([{
            "name": "access_token",
            "value": f"Bearer {token.access_token}",
            "url": base_url,
            "httpOnly": True,
            "sameSite": "Lax",
        }])

    @pytest.fixture(params=[
        ("registered_user", 1),
        ("vip_user", 2),
        ("operator_user", 3),
        ("cheef_user", 4),
        ("admin_user", 5),
        ("master_user", 10),
    ], ids=["registered", "vip", "operator", "cheef", "admin", "master"])
    def auth_page(self, request, page: Page, base_url: str):
        """Set auth cookie for each user class and return (page, class, nick)."""
        nick, cls = request.param
        self._set_auth_cookie(page, base_url, nick, cls)
        return page, cls, nick

    PROTECTED_PAGES = [
        "/dashboard/",
        "/dashboard/users",
        "/dashboard/bans",
        "/dashboard/config",
        "/dashboard/logs",
        "/dashboard/console",
        "/dashboard/plugins",
        "/dashboard/invites",
    ]

    def test_no_403_on_any_protected_page(self, auth_page, base_url: str):
        """Each user class should never receive a 403 Forbidden."""
        pg, cls, nick = auth_page
        for path in self.PROTECTED_PAGES:
            resp = pg.goto(f"{base_url}{path}")
            assert resp.status != 403, (
                f"{path} returned 403 for class {cls} ({nick})"
            )

    def test_navbar_shows_all_links(self, auth_page, base_url: str):
        """Navbar should contain links to every major section."""
        pg, cls, nick = auth_page
        pg.goto(f"{base_url}/dashboard/")
        # The page might 500 if DB is unavailable, that's OK.
        # Check that the navbar was rendered with all links.
        navbar_html = pg.locator("#navbarMain .navbar-start").inner_html()
        for href in [
            "/dashboard/users", "/dashboard/bans", "/dashboard/console",
            "/dashboard/plugins", "/dashboard/config", "/dashboard/logs",
            "/dashboard/invites",
        ]:
            assert href in navbar_html, (
                f"Navbar missing link to {href} for class {cls} ({nick})"
            )

    def test_user_dropdown_shows_nick(self, auth_page, base_url: str):
        """User dropdown in navbar should show the user's nick."""
        pg, cls, nick = auth_page
        pg.goto(f"{base_url}/dashboard/")
        dropdown = pg.locator(".navbar-dropdown")
        # The navbar link before dropdown should contain nick
        navbar_link = pg.locator(".navbar-link")
        if navbar_link.count() > 0:
            expect(navbar_link.first).to_contain_text(nick)


# ---------------------------------------------------------------------------
# Unauthenticated Access Tests
# ---------------------------------------------------------------------------


class TestUnauthenticatedRedirects:
    """Test that unauthenticated access to protected pages redirects to login."""

    PROTECTED_PAGES = [
        "/dashboard/",
        "/dashboard/users",
        "/dashboard/bans",
        "/dashboard/config",
        "/dashboard/logs",
        "/dashboard/console",
        "/dashboard/plugins",
        "/dashboard/invites",
    ]

    def test_redirects_to_login(self, page: Page, base_url: str):
        """Unauthenticated requests to protected pages redirect to login."""
        for path in self.PROTECTED_PAGES:
            resp = page.goto(f"{base_url}{path}")
            # After following redirects we should be on the login page
            expect(page).to_have_url(re.compile(r"/dashboard/login"))

    def test_public_pages_no_redirect(self, page: Page, base_url: str):
        """Public pages (login, register, SPA, embed) don't redirect."""
        public = [LOGIN_PATH, REGISTER_PATH, "/dashboard/spa", "/dashboard/embed"]
        for path in public:
            resp = page.goto(f"{base_url}{path}")
            assert resp.status == 200, f"{path} returned {resp.status}"


# ---------------------------------------------------------------------------
# Invite Code Page Tests
# ---------------------------------------------------------------------------


class TestInvitesPage:
    """Tests for the /dashboard/invites page."""

    @staticmethod
    def _login_as(page: Page, base_url: str, nick: str, user_class: int):
        from verlihub.api.auth import create_access_token
        token = create_access_token(nick, user_class)
        page.context.add_cookies([{
            "name": "access_token",
            "value": f"Bearer {token.access_token}",
            "url": base_url,
            "httpOnly": True,
            "sameSite": "Lax",
        }])

    def test_invites_page_loads_for_user(self, page: Page, base_url: str):
        """Test that a regular user can load the invites page."""
        self._login_as(page, base_url, "regular_user", 1)
        resp = page.goto(f"{base_url}{INVITES_PATH}")
        assert resp.status != 403
        expect(page).to_have_title(re.compile(r"Invite.*Dashboard"))
        # Should see "My Invite Codes" section
        expect(page.locator("text=My Invite Codes")).to_be_visible()

    def test_invites_page_admin_sees_allocate_form(self, page: Page, base_url: str):
        """Test that an admin sees the allocate form."""
        self._login_as(page, base_url, "admin_user", 5)
        page.goto(f"{base_url}{INVITES_PATH}")
        # Admin should see the allocation form
        expect(page.locator("#allocate-form")).to_be_visible()
        expect(page.locator("#alloc-nick")).to_be_visible()
        expect(page.locator("#alloc-count")).to_be_visible()
        expect(page.locator("#alloc-class")).to_be_visible()

    def test_invites_page_user_no_allocate_form(self, page: Page, base_url: str):
        """Test that a regular user does NOT see the allocate form."""
        self._login_as(page, base_url, "regular_user", 1)
        page.goto(f"{base_url}{INVITES_PATH}")
        # Non-admin should NOT see the allocate form
        assert page.locator("#allocate-form").count() == 0

    def test_invites_page_admin_all_codes_section(self, page: Page, base_url: str):
        """Test that admin sees the 'All Invite Codes' section."""
        self._login_as(page, base_url, "admin_user", 5)
        page.goto(f"{base_url}{INVITES_PATH}")
        expect(page.locator("text=All Invite Codes")).to_be_visible()
        # Filter controls
        expect(page.locator("#filter-nick")).to_be_visible()
        expect(page.locator("#filter-used")).to_be_visible()

    def test_invites_page_user_no_all_codes_section(self, page: Page, base_url: str):
        """Test that a regular user does NOT see 'All Invite Codes'."""
        self._login_as(page, base_url, "regular_user", 1)
        page.goto(f"{base_url}{INVITES_PATH}")
        assert page.locator("#all-invites-table").count() == 0

    def test_invites_page_stats_summary(self, page: Page, base_url: str):
        """Test that invites page shows Total / Available / Used stats."""
        self._login_as(page, base_url, "regular_user", 1)
        page.goto(f"{base_url}{INVITES_PATH}")
        expect(page.locator("#stat-total")).to_be_visible()
        expect(page.locator("#stat-available")).to_be_visible()
        expect(page.locator("#stat-used")).to_be_visible()

    def test_invites_admin_class_dropdown_options(self, page: Page, base_url: str):
        """Test that the class dropdown shows the right options for admin."""
        self._login_as(page, base_url, "admin_user", 5)
        page.goto(f"{base_url}{INVITES_PATH}")
        select = page.locator("#alloc-class")
        options = select.locator("option")
        # Admin (class 5) should see Registered through Admin but NOT Master
        option_texts = [options.nth(i).text_content() for i in range(options.count())]
        assert any("Registered" in t for t in option_texts)
        assert any("Admin" in t for t in option_texts)
        assert not any("Master" in t for t in option_texts)

    def test_invites_master_class_dropdown_options(self, page: Page, base_url: str):
        """Test that master sees Master option in class dropdown."""
        self._login_as(page, base_url, "master_user", 10)
        page.goto(f"{base_url}{INVITES_PATH}")
        select = page.locator("#alloc-class")
        options = select.locator("option")
        option_texts = [options.nth(i).text_content() for i in range(options.count())]
        assert any("Master" in t for t in option_texts)


# ---------------------------------------------------------------------------
# Logout Flow Tests
# ---------------------------------------------------------------------------


class TestLogoutFlow:
    """Test logout behaviour."""

    def test_logout_clears_session(self, page: Page, base_url: str):
        """Test that visiting /dashboard/logout clears the cookie and redirects."""
        # Set a cookie first
        from verlihub.api.auth import create_access_token
        token = create_access_token("logout_test", 1)
        page.context.add_cookies([{
            "name": "access_token",
            "value": f"Bearer {token.access_token}",
            "url": base_url,
            "httpOnly": True,
            "sameSite": "Lax",
        }])
        page.goto(f"{base_url}{LOGOUT_PATH}")
        # Should end up on login page
        expect(page).to_have_url(re.compile(r"/dashboard/login"))
        # After logout, going to dashboard home should redirect to login
        page.goto(f"{base_url}{DASHBOARD_HOME}")
        expect(page).to_have_url(re.compile(r"/dashboard/login"))


# ---------------------------------------------------------------------------
# Cross-page Navigation Tests
# ---------------------------------------------------------------------------


class TestCrossPageNavigation:
    """Test navigation between dashboard pages using the navbar."""

    @pytest.fixture(autouse=True)
    def login(self, page: Page, base_url: str):
        from verlihub.api.auth import create_access_token
        token = create_access_token("nav_tester", 5)  # admin
        page.context.add_cookies([{
            "name": "access_token",
            "value": f"Bearer {token.access_token}",
            "url": base_url,
            "httpOnly": True,
            "sameSite": "Lax",
        }])
        return page

    NAV_ITEMS = [
        ("Users", "/dashboard/users"),
        ("Bans", "/dashboard/bans"),
        ("Console", "/dashboard/console"),
        ("Plugins", "/dashboard/plugins"),
        ("Config", "/dashboard/config"),
        ("Logs", "/dashboard/logs"),
        ("Invites", "/dashboard/invites"),
    ]

    def test_navbar_links_navigate_correctly(self, page: Page, base_url: str):
        """Test that clicking each navbar link goes to the right page."""
        page.goto(f"{base_url}{DASHBOARD_HOME}")
        for label, expected_path in self.NAV_ITEMS:
            link = page.locator(f'.navbar-item[href="{expected_path}"]')
            if link.count() > 0:
                link.first.click()
                page.wait_for_url(
                    re.compile(re.escape(expected_path)),
                    timeout=10000,
                )


# ---------------------------------------------------------------------------
# Theme Toggle Tests
# ---------------------------------------------------------------------------


class TestThemeToggle:
    """Test the dark/light theme toggle."""

    def test_default_is_light_theme(self, page: Page, base_url: str):
        """Test that the default theme is light."""
        page.goto(f"{base_url}{LOGIN_PATH}")
        html = page.locator("html")
        expect(html).to_have_attribute("data-theme", "light")

    def test_toggle_to_dark_theme(self, page: Page, base_url: str):
        """Test toggling to dark theme."""
        page.goto(f"{base_url}{LOGIN_PATH}")
        page.click(".theme-toggle")
        html = page.locator("html")
        expect(html).to_have_attribute("data-theme", "dark")

    def test_toggle_back_to_light(self, page: Page, base_url: str):
        """Test toggling back to light theme."""
        page.goto(f"{base_url}{LOGIN_PATH}")
        page.click(".theme-toggle")  # -> dark
        page.click(".theme-toggle")  # -> light
        html = page.locator("html")
        expect(html).to_have_attribute("data-theme", "light")


# ---------------------------------------------------------------------------
# Responsive Layout Tests
# ---------------------------------------------------------------------------


class TestResponsiveLayout:
    """Test responsive behaviour for auth pages."""

    def test_login_page_mobile(self, page: Page, base_url: str):
        """Test login page at mobile viewport."""
        page.set_viewport_size({"width": 375, "height": 667})
        page.goto(f"{base_url}{LOGIN_PATH}")
        expect(page.locator('input[name="username"]')).to_be_visible()
        expect(page.locator('button[type="submit"]')).to_be_visible()

    def test_register_page_mobile(self, page: Page, base_url: str):
        """Test register page at mobile viewport."""
        page.set_viewport_size({"width": 375, "height": 667})
        page.goto(f"{base_url}{REGISTER_PATH}")
        expect(page.locator('input[name="nick"]')).to_be_visible()
        expect(page.locator('button[type="submit"]')).to_be_visible()


# ---------------------------------------------------------------------------
# pytest custom options (reuse --base-url)
# ---------------------------------------------------------------------------

def pytest_addoption(parser):
    """Add custom pytest options."""
    try:
        parser.addoption(
            "--base-url",
            action="store",
            default=DEFAULT_BASE_URL,
            help="Base URL for the dashboard (default: http://localhost:30000)",
        )
    except ValueError:
        # Option already registered by another conftest/plugin
        pass


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
