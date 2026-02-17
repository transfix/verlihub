"""
Pytest configuration for Playwright dashboard tests.

This module provides fixtures and configuration for running
Playwright E2E tests against the Verlihub dashboard.
"""

import pytest
from typing import Generator

# Default configuration
DEFAULT_BASE_URL = "http://localhost:30000"
DEFAULT_TIMEOUT = 30000


def pytest_addoption(parser):
    """Add custom pytest options for Playwright tests."""
    try:
        parser.addoption(
            "--base-url",
            action="store",
            default=DEFAULT_BASE_URL,
            help=f"Base URL for the dashboard (default: {DEFAULT_BASE_URL})"
        )
    except ValueError:
        # Option already added
        pass


@pytest.fixture(scope="session")
def base_url(request) -> str:
    """Get base URL from pytest option or environment."""
    import os
    return os.environ.get("DASHBOARD_URL") or request.config.getoption("--base-url") or DEFAULT_BASE_URL


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Configure browser context with default timeout."""
    return {
        **browser_context_args,
        "ignore_https_errors": True,
    }


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Configure browser launch args."""
    return {
        **browser_type_launch_args,
        "slow_mo": 100,  # Slow down for visibility in headed mode
    }
