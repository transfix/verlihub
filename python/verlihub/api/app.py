"""
Main FastAPI application for Thin Verlihub.

This module sets up the FastAPI application with:
- Hub core integration via SWIG bindings
- Database connection via SQLModel
- REST API endpoints
- WebSocket support for real-time events (future)
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from verlihub.api import api_router
from verlihub.api.deps import get_hub_context, set_hub_context  # Re-export for compatibility
from verlihub.models.database import DatabaseConfig, init_database, close_database

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan manager.
    
    Handles startup and shutdown of:
    - Database connection (Python-managed: SQLite, PostgreSQL, or MySQL)
    - Hub context (C++ core, database-free NMDC protocol server)
    """
    logger.info("Starting Thin Verlihub...")
    
    # Get configuration directory from environment or use default
    config_dir = os.getenv("VH_CONFIG_DIR", "/etc/verlihub")
    
    # Initialize database (Python-side, supports any backend)
    try:
        # Use environment variables for database config
        logger.info("Using environment variables for database config")
        config = DatabaseConfig()
        await init_database(config=config)
        logger.info("Database connected")
    except Exception as e:
        logger.error("Failed to connect to database: %s", e)
        # Continue anyway - API will work with limited functionality
    
    # Initialize hub context (if SWIG module is available)
    # The C++ core is now database-free - it only handles NMDC protocol
    try:
        from verlihub.core import HubContext
        
        # Ensure config directory exists
        Path(config_dir).mkdir(parents=True, exist_ok=True)
        
        ctx = HubContext.create(config_dir)
        if ctx is not None:
            set_hub_context(ctx)
            if ctx.initialize():
                # Auto-start hub if VH_AUTO_START is set
                if os.getenv("VH_AUTO_START", "0") == "1":
                    port = int(os.getenv("VH_PORT", "411"))
                    listen_ip = os.getenv("VH_LISTEN_IP", "0.0.0.0")
                    if ctx.start(port, listen_ip):
                        logger.info("Hub started on %s:%d", listen_ip, port)
                    else:
                        logger.error("Failed to start hub")
                else:
                    logger.info("Hub initialized (use /api/v1/hub/start to start)")
            else:
                logger.error("Failed to initialize hub context")
        else:
            logger.warning("Failed to create hub context")
    except ImportError:
        logger.warning(
            "SWIG module not available - running in API-only mode. "
            "Build with -DBUILD_PYTHON_BINDINGS=ON to enable hub control."
        )
    except Exception as e:
        logger.error("Failed to initialize hub: %s", e)
    
    yield
    
    # Shutdown
    logger.info("Shutting down Thin Verlihub...")
    
    # Stop hub if running
    ctx = get_hub_context()
    if ctx is not None and ctx.is_running:
        logger.info("Stopping hub...")
        ctx.stop()
    
    # Close database
    await close_database()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Thin Verlihub",
        description="REST API for Verlihub DC hub management",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    
    # Configure CORS
    allowed_origins = os.getenv("VH_CORS_ORIGINS", "*").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include API router
    app.include_router(api_router)
    
    # Include dashboard router
    from verlihub.dashboard import dashboard_router
    from verlihub.dashboard.websocket import ws_router
    app.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])
    app.include_router(ws_router, prefix="/ws", tags=["websocket"])
    
    # Health check endpoint
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        ctx = get_hub_context()
        return {
            "status": "healthy",
            "hub_initialized": ctx is not None,
            "hub_running": ctx.is_running if ctx else False,
        }
    
    return app


# Create default app instance
app = create_app()


# Entry point for running directly
if __name__ == "__main__":
    import uvicorn
    
    host = os.getenv("VH_API_HOST", "0.0.0.0")
    port = int(os.getenv("VH_API_PORT", "8000"))
    
    uvicorn.run(
        "verlihub.api.app:app",
        host=host,
        port=port,
        reload=os.getenv("VH_DEBUG", "0") == "1",
    )
