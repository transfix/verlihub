"""
FastAPI REST API for Verlihub management.

This module provides the REST API endpoints for managing the hub
from external applications or web dashboards.
"""
from __future__ import annotations

from fastapi import APIRouter

# Import route modules
from verlihub.api.routes import auth, hub, users, bans, console, stats, invites, llm, logs, penalties, triggers, redirects, clients
from verlihub.hublist import hublist_router

# Create main API router
api_router = APIRouter(prefix="/api/v1")

# Include sub-routers
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(hub.router, prefix="/hub", tags=["hub"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(bans.router, prefix="/bans", tags=["bans"])
api_router.include_router(penalties.router, prefix="/penalties", tags=["penalties"])
api_router.include_router(stats.router, prefix="/stats", tags=["statistics"])
api_router.include_router(console.router, tags=["console"])
api_router.include_router(invites.router, prefix="/invites", tags=["invites"])
api_router.include_router(hublist_router, prefix="/hublist", tags=["hublist"])
api_router.include_router(llm.router, prefix="/llm", tags=["llm"])
api_router.include_router(logs.router, prefix="/logs", tags=["logs"])
api_router.include_router(triggers.router, prefix="/triggers", tags=["triggers"])
api_router.include_router(redirects.router, prefix="/redirects", tags=["redirects"])
api_router.include_router(clients.router, prefix="/clients", tags=["clients"])

__all__ = ["api_router"]
