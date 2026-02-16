"""
Shared dependencies for API routes.

This module provides dependency injection functions that can be imported
by routes without creating circular imports.
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from verlihub.core.context import HubContext

# Global hub context (set during startup)
_hub_context: Optional["HubContext"] = None


def get_hub_context() -> Optional["HubContext"]:
    """Get the global hub context."""
    return _hub_context


def set_hub_context(ctx: Optional["HubContext"]) -> None:
    """Set the global hub context."""
    global _hub_context
    _hub_context = ctx
