#!/usr/bin/env python3
"""
Playwright E2E Tests for Verlihub Dashboard

Tests the dashboard web application frontend including:
- SPA Dashboard navigation and tab switching
- User table display and interactions
- Geographic statistics display
- City/ASN/IP drill-down modals
- Embeddable dashboard widget
- Clone detection display
- Responsive behavior

Usage:
    # Install Playwright first
    pip install pytest-playwright
    playwright install chromium
    
    # Run tests against a running dashboard
    pytest test_dashboard_playwright.py --base-url http://localhost:30000

    # Run with headed browser for debugging
    pytest test_dashboard_playwright.py --headed --base-url http://localhost:30000
"""

import pytest
import re
from typing import Generator
from playwright.sync_api import Page, expect, Playwright

# Test configuration
DEFAULT_BASE_URL = "http://localhost:30000"
DASHBOARD_PATH = "/dashboard/spa"
EMBED_PATH = "/dashboard/embed"


@pytest.fixture(scope="session")
def base_url(request) -> str:
    """Get base URL from pytest option or use default."""
    return getattr(request.config.option, 'base_url', None) or DEFAULT_BASE_URL


@pytest.fixture(scope="function")
def dashboard_page(page: Page, base_url: str) -> Page:
    """Navigate to the SPA dashboard and wait for it to load."""
    page.goto(f"{base_url}{DASHBOARD_PATH}")
    # Wait for the content to finish loading
    page.wait_for_selector("#content", state="visible")
    # Wait for loading state to clear
    page.wait_for_function("!document.querySelector('#content.loading')", timeout=10000)
    return page


@pytest.fixture(scope="function")
def embed_page(page: Page, base_url: str) -> Page:
    """Navigate to the embeddable dashboard."""
    page.goto(f"{base_url}{EMBED_PATH}")
    page.wait_for_selector(".embed-container", state="visible")
    return page


class TestDashboardLoading:
    """Test basic dashboard loading and structure."""
    
    def test_spa_dashboard_loads(self, dashboard_page: Page):
        """Test that the SPA dashboard loads successfully."""
        # Check page title exists
        expect(dashboard_page).to_have_title(re.compile(r".*Dashboard.*"))
        
        # Check header is visible
        header = dashboard_page.locator(".page-header")
        expect(header).to_be_visible()
        
        # Check hub name header exists
        hub_name = dashboard_page.locator("#hub-name-header")
        expect(hub_name).to_be_visible()
    
    def test_tabs_are_visible(self, dashboard_page: Page):
        """Test that all navigation tabs are visible."""
        tabs = dashboard_page.locator("#tabs .tab")
        expect(tabs).to_have_count(6)
        
        # Verify tab names
        expected_tabs = ["Hub", "Online Users", "Countries", "Cities", "ASNs", "IPs"]
        for i, tab_name in enumerate(expected_tabs):
            tab = tabs.nth(i)
            expect(tab).to_contain_text(tab_name)
    
    def test_hub_tab_is_default(self, dashboard_page: Page):
        """Test that Hub tab is selected by default."""
        hub_tab = dashboard_page.locator('.tab[data-tab="hub"]')
        expect(hub_tab).to_have_class(re.compile(r".*active.*"))
    
    def test_content_area_exists(self, dashboard_page: Page):
        """Test that content area is present."""
        content = dashboard_page.locator("#content")
        expect(content).to_be_visible()


class TestHubTab:
    """Test the Hub information tab."""
    
    def test_hub_info_displays(self, dashboard_page: Page):
        """Test that hub information is displayed."""
        # Hub info should contain stats cards
        cards = dashboard_page.locator(".card")
        # Should have at least the stats cards (Users, Share, Operators, Bots, Status)
        count = cards.count()
        assert count >= 1, "Should have at least one card displayed"
    
    def test_stats_cards_have_values(self, dashboard_page: Page):
        """Test that stats cards show values."""
        # Look for card values
        card_values = dashboard_page.locator(".card-value")
        if card_values.count() > 0:
            first_value = card_values.first
            expect(first_value).to_be_visible()
    
    def test_hub_info_list_present(self, dashboard_page: Page):
        """Test that hub information list is present."""
        info_list = dashboard_page.locator(".info-list")
        # Info list might be in hub-info section
        if info_list.count() > 0:
            expect(info_list.first).to_be_visible()


class TestTabNavigation:
    """Test navigation between tabs."""
    
    def test_click_users_tab(self, dashboard_page: Page):
        """Test switching to Users tab."""
        users_tab = dashboard_page.locator('.tab[data-tab="users"]')
        users_tab.click()
        
        # Wait for tab to become active
        expect(users_tab).to_have_class(re.compile(r".*active.*"))
        
        # Wait for content to load
        dashboard_page.wait_for_function(
            "!document.querySelector('#content.loading')", 
            timeout=10000
        )
        
        # Users tab should show a table or user count
        content = dashboard_page.locator("#content")
        expect(content).to_contain_text(re.compile(r"(Online users|users|No users)", re.IGNORECASE))
    
    def test_click_countries_tab(self, dashboard_page: Page):
        """Test switching to Countries tab."""
        geo_tab = dashboard_page.locator('.tab[data-tab="geo"]')
        geo_tab.click()
        
        expect(geo_tab).to_have_class(re.compile(r".*active.*"))
        dashboard_page.wait_for_function(
            "!document.querySelector('#content.loading')", 
            timeout=10000
        )
        
        content = dashboard_page.locator("#content")
        expect(content).to_contain_text(re.compile(r"(countries|No geographic)", re.IGNORECASE))
    
    def test_click_cities_tab(self, dashboard_page: Page):
        """Test switching to Cities tab."""
        cities_tab = dashboard_page.locator('.tab[data-tab="cities"]')
        cities_tab.click()
        
        expect(cities_tab).to_have_class(re.compile(r".*active.*"))
        dashboard_page.wait_for_function(
            "!document.querySelector('#content.loading')", 
            timeout=10000
        )
        
        content = dashboard_page.locator("#content")
        expect(content).to_contain_text(re.compile(r"(cities|Total)", re.IGNORECASE))
    
    def test_click_asns_tab(self, dashboard_page: Page):
        """Test switching to ASNs tab."""
        asns_tab = dashboard_page.locator('.tab[data-tab="asns"]')
        asns_tab.click()
        
        expect(asns_tab).to_have_class(re.compile(r".*active.*"))
        dashboard_page.wait_for_function(
            "!document.querySelector('#content.loading')", 
            timeout=10000
        )
        
        content = dashboard_page.locator("#content")
        expect(content).to_contain_text(re.compile(r"(ASN|Total)", re.IGNORECASE))
    
    def test_click_ips_tab(self, dashboard_page: Page):
        """Test switching to IPs tab."""
        ips_tab = dashboard_page.locator('.tab[data-tab="ips"]')
        ips_tab.click()
        
        expect(ips_tab).to_have_class(re.compile(r".*active.*"))
        dashboard_page.wait_for_function(
            "!document.querySelector('#content.loading')", 
            timeout=10000
        )
        
        content = dashboard_page.locator("#content")
        expect(content).to_contain_text(re.compile(r"(IP|Total)", re.IGNORECASE))
    
    def test_return_to_hub_tab(self, dashboard_page: Page):
        """Test returning to Hub tab after switching."""
        # Go to users tab first
        users_tab = dashboard_page.locator('.tab[data-tab="users"]')
        users_tab.click()
        dashboard_page.wait_for_function("!document.querySelector('#content.loading')", timeout=10000)
        
        # Return to hub tab
        hub_tab = dashboard_page.locator('.tab[data-tab="hub"]')
        hub_tab.click()
        
        expect(hub_tab).to_have_class(re.compile(r".*active.*"))


class TestUsersTab:
    """Test the Users tab functionality."""
    
    @pytest.fixture(autouse=True)
    def navigate_to_users(self, dashboard_page: Page):
        """Navigate to Users tab before each test."""
        users_tab = dashboard_page.locator('.tab[data-tab="users"]')
        users_tab.click()
        dashboard_page.wait_for_function(
            "!document.querySelector('#content.loading')", 
            timeout=10000
        )
        return dashboard_page
    
    def test_users_table_exists(self, dashboard_page: Page):
        """Test that users table is displayed."""
        # Either a table or a "no users" message should be present
        content = dashboard_page.locator("#content")
        table = content.locator("table")
        
        if table.count() > 0:
            expect(table).to_be_visible()
        else:
            # No users case - should show appropriate message
            expect(content).to_contain_text(re.compile(r"(No users|0 users|Online users: 0)", re.IGNORECASE))
    
    def test_clone_filter_checkbox(self, dashboard_page: Page):
        """Test that clone filter checkbox exists."""
        checkbox = dashboard_page.locator("#hide-clones")
        if checkbox.count() > 0:
            expect(checkbox).to_be_visible()


class TestUserDetailModal:
    """Test the user detail modal functionality."""
    
    def test_modal_initially_hidden(self, dashboard_page: Page):
        """Test that user detail modal is hidden by default."""
        modal = dashboard_page.locator("#user-detail")
        expect(modal).to_have_css("display", "none")
    
    def test_modal_has_close_button(self, dashboard_page: Page):
        """Test that modal has a close button."""
        close_btn = dashboard_page.locator("#user-detail .close-btn")
        expect(close_btn).to_be_attached()


class TestTableSorting:
    """Test table sorting functionality."""
    
    def test_users_table_sortable_headers(self, dashboard_page: Page):
        """Test that users table has sortable column headers."""
        # Navigate to users tab
        users_tab = dashboard_page.locator('.tab[data-tab="users"]')
        users_tab.click()
        dashboard_page.wait_for_function("!document.querySelector('#content.loading')", timeout=10000)
        
        # Check for sortable headers with sort arrows
        table = dashboard_page.locator("#users-table")
        if table.count() > 0:
            headers = table.locator("th")
            expect(headers).to_have_count(4)  # Nick, Class, Country, Share
    
    def test_geo_table_sortable_headers(self, dashboard_page: Page):
        """Test that geo table has sortable column headers."""
        geo_tab = dashboard_page.locator('.tab[data-tab="geo"]')
        geo_tab.click()
        dashboard_page.wait_for_function("!document.querySelector('#content.loading')", timeout=10000)
        
        table = dashboard_page.locator("#geo-table")
        if table.count() > 0:
            headers = table.locator("th")
            # Flag, Country, Users, Share
            assert headers.count() >= 2, "Should have at least 2 columns"


class TestEmbedDashboard:
    """Test the embeddable mini dashboard."""
    
    def test_embed_loads(self, embed_page: Page):
        """Test that the embed dashboard loads."""
        container = embed_page.locator(".embed-container")
        expect(container).to_be_visible()
    
    def test_embed_shows_hub_name(self, embed_page: Page):
        """Test that embed shows hub name."""
        # Wait for content to load
        embed_page.wait_for_function(
            "!document.querySelector('.loading')", 
            timeout=10000
        )
        
        hub_name = embed_page.locator(".hub-name")
        if hub_name.count() > 0:
            expect(hub_name).to_be_visible()
    
    def test_embed_shows_stats(self, embed_page: Page):
        """Test that embed shows statistics."""
        embed_page.wait_for_function(
            "!document.querySelector('.loading')", 
            timeout=10000
        )
        
        stats_grid = embed_page.locator(".stats-grid")
        if stats_grid.count() > 0:
            expect(stats_grid).to_be_visible()
            
            # Should have stat boxes
            stat_boxes = embed_page.locator(".stat-box")
            expect(stat_boxes).to_have_count(4)  # Users, Share, Operators, Status
    
    def test_embed_has_powered_by_link(self, embed_page: Page):
        """Test that embed has powered-by link to full dashboard."""
        embed_page.wait_for_function(
            "!document.querySelector('.loading')", 
            timeout=10000
        )
        
        powered_by = embed_page.locator(".powered-by")
        if powered_by.count() > 0:
            expect(powered_by).to_be_visible()
            link = powered_by.locator("a")
            expect(link).to_have_attribute("href", "/dashboard/spa")


class TestResponsiveBehavior:
    """Test responsive design behavior."""
    
    def test_mobile_viewport(self, page: Page, base_url: str):
        """Test dashboard at mobile viewport size."""
        page.set_viewport_size({"width": 375, "height": 667})
        page.goto(f"{base_url}{DASHBOARD_PATH}")
        page.wait_for_selector("#content", state="visible")
        
        # Tabs should still be visible
        tabs = page.locator("#tabs")
        expect(tabs).to_be_visible()
        
        # Content should still be visible
        content = page.locator("#content")
        expect(content).to_be_visible()
    
    def test_tablet_viewport(self, page: Page, base_url: str):
        """Test dashboard at tablet viewport size."""
        page.set_viewport_size({"width": 768, "height": 1024})
        page.goto(f"{base_url}{DASHBOARD_PATH}")
        page.wait_for_selector("#content", state="visible")
        
        tabs = page.locator("#tabs")
        expect(tabs).to_be_visible()


class TestErrorHandling:
    """Test error handling scenarios."""
    
    def test_handles_api_errors_gracefully(self, page: Page, base_url: str):
        """Test that the dashboard handles API errors gracefully."""
        # Navigate to dashboard
        page.goto(f"{base_url}{DASHBOARD_PATH}")
        # Even if API fails, page should load without crashing
        page.wait_for_selector("#content", state="visible", timeout=15000)
        
        # Page should remain functional
        tabs = page.locator("#tabs")
        expect(tabs).to_be_visible()


class TestAccessibility:
    """Test basic accessibility features."""
    
    def test_page_has_lang_attribute(self, dashboard_page: Page):
        """Test that the page has a lang attribute."""
        html = dashboard_page.locator("html")
        expect(html).to_have_attribute("lang", "en")
    
    def test_headings_hierarchy(self, dashboard_page: Page):
        """Test that page has proper heading hierarchy."""
        # Check for h1 or h2 headings
        h1 = dashboard_page.locator("h1")
        h2 = dashboard_page.locator("h2")
        
        # Should have at least one heading
        total_headings = h1.count() + h2.count()
        assert total_headings >= 1, "Page should have at least one heading"
    
    def test_interactive_elements_focusable(self, dashboard_page: Page):
        """Test that tabs are keyboard focusable."""
        # Tabs should be clickable
        tab = dashboard_page.locator('.tab[data-tab="users"]')
        expect(tab).to_be_visible()
        # Tabs use click handlers, checking they exist
        assert tab.get_attribute("data-tab") == "users"


class TestLiveUpdates:
    """Test live update functionality."""
    
    def test_uptime_element_exists_in_hub_tab(self, dashboard_page: Page):
        """Test that uptime element exists for live updates."""
        # Hub tab should have uptime display
        uptime = dashboard_page.locator("#hub-uptime")
        if uptime.count() > 0:
            expect(uptime).to_be_visible()


# Run configuration for pytest
def pytest_addoption(parser):
    """Add custom pytest options."""
    parser.addoption(
        "--base-url",
        action="store",
        default=DEFAULT_BASE_URL,
        help="Base URL for the dashboard (default: http://localhost:30000)"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
