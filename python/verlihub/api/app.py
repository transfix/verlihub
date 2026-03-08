"""
Main FastAPI application for Thin Verlihub.

This module sets up the FastAPI application with:
- Hub core integration via SWIG bindings
- Database connection via SQLModel
- REST API endpoints
- WebSocket support for real-time events (future)
"""
from __future__ import annotations

import asyncio
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


def _cfg():
    """Return the config singleton (or ``None`` when running headless tests)."""
    from verlihub.config import get_config_optional
    return get_config_optional()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan manager.
    
    Handles startup and shutdown of:
    - Database connection (Python-managed: SQLite, PostgreSQL, or MySQL)
    - Hub context (C++ core, database-free NMDC protocol server)
    """
    logger.info("Starting Thin Verlihub...")
    
    cfg = _cfg()
    config_dir = cfg._config_dir if cfg else os.getenv("VH_CONFIG_DIR", "/etc/verlihub")
    
    # Initialize database (Python-side, supports any backend)
    try:
        logger.info("Initialising database...")
        if cfg:
            db_url = cfg.database.get_url(config_dir)
            db_config = DatabaseConfig(url=db_url)
        else:
            db_config = DatabaseConfig(config_dir=config_dir)
        await init_database(config=db_config)
        logger.info("Database connected")

        # Seed user accounts from YAML config into the live DB
        try:
            from verlihub.config import apply_config_to_db, load_config
            yaml_cfg = cfg or load_config(config_dir=config_dir)
            if yaml_cfg is not None:
                await apply_config_to_db(yaml_cfg)
                logger.info("Config-to-DB sync complete (lifespan)")
        except Exception as seed_err:
            logger.warning("Admin seeding skipped in lifespan: %s", seed_err)
    except Exception as e:
        logger.error("Failed to connect to database: %s", e)
        # Continue anyway - API will work with limited functionality
    
    # Initialize hub context (if SWIG module is available)
    # The C++ core is now database-free - it only handles NMDC protocol
    #
    # In "both" mode the hub is started by run_both() / run_hub() *before*
    # uvicorn boots, so the global hub context is already populated.  We
    # must NOT create a second HubContext — just re-use the existing one.
    hub_started_by_lifespan = False
    existing_ctx = get_hub_context()

    if existing_ctx is not None:
        logger.info("Re-using existing hub context (started externally)")
        # Give the event handler a reference to *this* event loop so that
        # cross-thread DB lookups (OnValidateNick etc.) schedule coroutines
        # on the loop that owns the async DB engine.
        existing_ctx.events.set_event_loop(asyncio.get_running_loop())
    else:
        try:
            from verlihub.core import HubContext

            # Ensure config directory exists
            Path(config_dir).mkdir(parents=True, exist_ok=True)

            ctx = HubContext.create(config_dir)
            if ctx is not None:
                set_hub_context(ctx)
                ctx.events.set_event_loop(asyncio.get_running_loop())

                # Feed YAML config to the C++ director callback so
                # Initialize() → LoadConfiguration() sees the right values.
                if cfg:
                    hub_section: dict[str, str] = {
                        "hub_name": cfg.hub.name,
                        "hub_topic": cfg.hub.topic,
                        "hub_desc": cfg.hub.description,
                        "hub_owner": cfg.hub.owner,
                        "hub_encoding": cfg.hub.encoding,
                        "listen_port": str(cfg.hub.port),
                        "listen_ip": cfg.hub.listen_host,
                        "max_users": str(cfg.hub.max_users),
                    }
                    ctx.events.set_config({"hub": hub_section})

                if ctx.initialize():
                    # Auto-start when the lifespan is the sole manager
                    auto_start = (cfg and cfg.mode in ("both", "hub"))
                    if auto_start:
                        hub_port = cfg.hub.port if cfg else 411
                        listen_ip = cfg.hub.listen_host if cfg else "0.0.0.0"
                        if ctx.start(hub_port, listen_ip):
                            hub_started_by_lifespan = True
                            logger.info("Hub started on %s:%d", listen_ip, hub_port)
                        else:
                            logger.error(
                                "Failed to start hub on port %d — is another "
                                "instance already running on that port?",
                                hub_port,
                            )
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
    
    # Wire hub events to WebSocket broadcaster & start stats task
    ctx = get_hub_context()
    if ctx is not None:
        try:
            from verlihub.dashboard.websocket import (
                hub_event_broadcaster,
                start_stats_task,
            )
            ctx.events.register('user_connect', hub_event_broadcaster.on_user_connect)
            ctx.events.register('user_disconnect', hub_event_broadcaster.on_user_disconnect)
            ctx.events.register('user_login', hub_event_broadcaster.on_user_login)
            ctx.events.register('user_logout', hub_event_broadcaster.on_user_disconnect)
            ctx.events.register('chat_message', hub_event_broadcaster.on_chat_message)
            ctx.events.register('hub_started', hub_event_broadcaster.on_hub_started)
            ctx.events.register('hub_stopping', hub_event_broadcaster.on_hub_stopping)
            start_stats_task()
            logger.info("WebSocket event broadcasting enabled")
        except Exception as ws_err:
            logger.warning("WebSocket event wiring failed: %s", ws_err)

    # Wire LLM bot chat handler (PM + main chat → security bot)
    _bot_chat_handler = None
    if ctx is not None:
        try:
            llm_cfg = cfg.llm if cfg else None
            if llm_cfg and llm_cfg.enabled:
                from verlihub.bot_chat import BotChatHandler
                _bot_chat_handler = BotChatHandler(ctx, llm_cfg)
                _bot_chat_handler.register(ctx.events)
                logger.info("LLM bot chat handler enabled")
        except Exception as bot_err:
            logger.warning("Bot chat handler failed to start: %s", bot_err)

    # Start in-process MCP session manager (if enabled & SDK installed)
    _mcp_session_mgr = None
    _mcp_task = None
    try:
        mcp_cfg = cfg.mcp if cfg else None
        if mcp_cfg and mcp_cfg.enabled:
            from verlihub.api.routes.mcp import create_mcp_mount
            mcp_app, _mcp_session_mgr = create_mcp_mount()
            if _mcp_session_mgr is not None:
                _mcp_task = asyncio.create_task(_mcp_session_mgr.run())
                logger.info("In-process MCP session manager started")
    except Exception as mcp_err:
        logger.warning("MCP session manager failed to start: %s", mcp_err)

    # Start hublist registration client if configured
    _hublist_client = None
    try:
        from verlihub.hublist import HubListRegistrationClient, build_hub_info
        hublist_servers = []
        use_regserver = False
        if cfg:
            hublist_servers = cfg.hub.hublist_servers or []
            # Check C++ config or treat non-empty server list as enabled
            use_regserver = bool(hublist_servers)
        if ctx:
            try:
                use_regserver = ctx.get_config("config", "use_regserver", "0") == "1"
            except Exception:
                pass
        if use_regserver and hublist_servers:
            interval = getattr(getattr(cfg, 'hublist', None), 'registration_interval', 600) if cfg else 600
            _hublist_client = HubListRegistrationClient(
                servers=hublist_servers,
                interval=interval,
            )
            await _hublist_client.start(lambda: build_hub_info(ctx))
            logger.info("Hublist registration client started for %d servers", len(hublist_servers))
    except Exception as hl_err:
        logger.warning("Hublist registration client failed to start: %s", hl_err)

    yield
    
    # Shutdown
    logger.info("Shutting down Thin Verlihub...")

    # Stop bot chat handler
    if _bot_chat_handler is not None:
        try:
            ctx_shutdown = get_hub_context()
            if ctx_shutdown is not None:
                _bot_chat_handler.unregister(ctx_shutdown.events)
            _bot_chat_handler.shutdown()
        except Exception:
            pass

    # Stop in-process MCP session manager
    if _mcp_task is not None:
        _mcp_task.cancel()
        try:
            await _mcp_task
        except Exception:
            pass

    # Stop hublist registration client
    if _hublist_client is not None:
        try:
            await _hublist_client.stop()
        except Exception:
            pass
    
    # Stop stats broadcast
    try:
        from verlihub.dashboard.websocket import stop_stats_task
        stop_stats_task()
    except Exception:
        pass

    # Stop hub only if the lifespan started it (not if run_hub owns it)
    ctx = get_hub_context()
    if hub_started_by_lifespan and ctx is not None and ctx.is_running:
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
        version="1.7.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    
    # Configure CORS
    cfg = _cfg()
    allowed_origins = cfg.api.cors_origins if cfg else ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include API router
    app.include_router(api_router)

    # Mount in-process MCP endpoint (ASGI sub-app, not a FastAPI router)
    try:
        mcp_cfg_check = cfg.mcp if cfg else None
        if mcp_cfg_check and mcp_cfg_check.enabled:
            from verlihub.api.routes.mcp import create_mcp_mount
            mcp_app, _ = create_mcp_mount()
            if mcp_app is not None:
                from starlette.routing import Mount
                app.routes.append(Mount("/api/v1/mcp", app=mcp_app))
                logger.info("MCP endpoint mounted at /api/v1/mcp")
    except Exception as mcp_mount_err:
        logger.warning("MCP mount skipped: %s", mcp_mount_err)

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
    
    # Root redirect to dashboard
    @app.get("/", include_in_schema=False)
    async def root_redirect():
        """Redirect root to dashboard."""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/dashboard/")
    
    return app


# Create default app instance
app = create_app()


# Entry point for running directly
if __name__ == "__main__":
    import uvicorn

    cfg = _cfg()
    host = cfg.api.host if cfg else "0.0.0.0"
    port = cfg.api.port if cfg else 8000

    uvicorn.run(
        "verlihub.api.app:app",
        host=host,
        port=port,
    )
