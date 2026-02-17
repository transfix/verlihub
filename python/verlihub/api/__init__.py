"""
FastAPI REST API for Verlihub management.

This module provides the REST API endpoints for managing the hub
from external applications or web dashboards.
"""
from __future__ import annotations

from fastapi import APIRouter

# Import route modules
from verlihub.api.routes import auth, hub, users, bans, console, stats

# Create main API router
api_router = APIRouter(prefix="/api/v1")

# Include sub-routers
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(hub.router, prefix="/hub", tags=["hub"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(bans.router, prefix="/bans", tags=["bans"])
api_router.include_router(stats.router, prefix="/stats", tags=["statistics"])
api_router.include_router(console.router, tags=["console"])

__all__ = ["api_router"]
