"""
Verlihub Server Launcher.

Entry point for running Verlihub with YAML configuration.
Supports both production and QA/development environments.

Usage:
    verlihub-server                     # Use default config search
    verlihub-server -c config.yml       # Use specific config file
    verlihub-server --env qa            # Override environment
    verlihub-server --mode api          # Run API only
    verlihub-server --mode hub          # Run hub only (requires C++ core)
    verlihub-server --mode both         # Run API + hub
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Verlihub Server - DC++ Hub Management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start API server with default config
  verlihub-server
  
  # Start with specific config file
  verlihub-server -c /etc/verlihub/production.yml
  
  # Start in QA mode
  verlihub-server --env qa -c qa-config.yml
  
  # API only mode (no hub)
  verlihub-server --mode api
  
  # Hub + API (requires C++ core)
  verlihub-server --mode both --config-dir /etc/verlihub

Environment Variables:
  VH_CONFIG_FILE    - Path to YAML config file
  VH_CONFIG_DIR     - Directory containing config.yml
  VH_ENV            - Environment (development, qa, production)
  VH_MODE           - Run mode (api, hub, both)
        """,
    )
    
    parser.add_argument(
        "-c", "--config",
        help="Path to YAML configuration file",
        metavar="FILE",
    )
    
    parser.add_argument(
        "--config-dir",
        help="Directory containing config.yml and hub data",
        metavar="DIR",
    )
    
    parser.add_argument(
        "--env", "--environment",
        choices=["development", "qa", "production"],
        help="Environment mode (overrides config file)",
    )
    
    parser.add_argument(
        "--mode",
        choices=["api", "hub", "both"],
        help="Run mode: api (REST API only), hub (NMDC hub only), both",
    )
    
    parser.add_argument(
        "--host",
        help="API server host (overrides config)",
    )
    
    parser.add_argument(
        "--port",
        type=int,
        help="API server port (overrides config)",
    )
    
    parser.add_argument(
        "--hub-port",
        type=int,
        help="Hub NMDC port (overrides config)",
    )
    
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development",
    )
    
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes (default: 1)",
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v for INFO, -vv for DEBUG)",
    )
    
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress non-error output",
    )
    
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force YAML config values to overwrite existing database values",
    )
    
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate config and exit",
    )
    
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show version and exit",
    )
    
    return parser.parse_args()


def setup_logging(verbosity: int, quiet: bool) -> None:
    """Configure logging based on verbosity level."""
    if quiet:
        level = logging.ERROR
    elif verbosity >= 2:
        level = logging.DEBUG
    elif verbosity >= 1:
        level = logging.INFO
    else:
        level = logging.WARNING
    
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_api_server(config: "VerlihubConfig", args: argparse.Namespace) -> None:
    """Run the API server."""
    import uvicorn
    
    host = args.host or config.api.host
    port = args.port or config.api.port
    
    uvicorn_config = {
        "app": "verlihub.api.app:app",
        "host": host,
        "port": port,
        "log_level": "debug" if args.verbose >= 2 else "info",
    }
    
    if args.reload and config.environment == "development":
        uvicorn_config["reload"] = True
    
    if args.workers > 1 and config.environment != "development":
        uvicorn_config["workers"] = args.workers
    
    logger.info("Starting API server on %s:%d", host, port)
    logger.info("Environment: %s", config.environment)
    logger.info("Dashboard: http://%s:%d/dashboard", host, port)
    logger.info("API docs: http://%s:%d/docs", host, port)
    
    uvicorn.run(**uvicorn_config)


async def run_hub(config: "VerlihubConfig", args: argparse.Namespace) -> None:
    """Run the NMDC hub (requires C++ core)."""
    try:
        from verlihub.core import HubContext, run_hub_server
    except ImportError as e:
        logger.error("Hub mode requires C++ core bindings: %s", e)
        logger.error("Build with -DBUILD_PYTHON_BINDINGS=ON")
        sys.exit(1)
    
    config_dir = args.config_dir or os.getenv("VH_CONFIG_DIR", ".")
    hub_port = args.hub_port or config.hub.port
    
    logger.info("Starting hub on port %d", hub_port)
    _write_motd_file(config, config_dir)
    
    try:
        await run_hub_server(
            config_dir=config_dir,
            port=hub_port,
            listen_ip=config.hub.listen_host,
            hub_name=config.hub.name,
            hub_topic=config.hub.topic,
            hub_desc=config.hub.description,
            hub_owner=config.hub.owner,
            hub_encoding=config.hub.encoding,
        )
    except Exception as e:
        logger.error("Hub error: %s", e)
        sys.exit(1)


def _write_motd_file(config: "VerlihubConfig", config_dir: str) -> None:
    """Write the MOTD file to <config_dir>/motd so the C++ hub picks it up.

    Sources (in order):
    1. Database (SetupList ``config.hub_motd``) — survives restarts
    2. ``hub.motd_file`` — copy referenced file
    3. ``hub.motd`` — inline text from YAML
    4. ``hub.description`` — fallback to hub description
    """
    from pathlib import Path
    motd_path = Path(config_dir) / "motd"

    # 1. Check database for a persisted MOTD (set via LLM / admin)
    db_motd = _read_motd_from_db()
    if db_motd:
        motd_path.write_text(db_motd + "\n", encoding="utf-8")
        logger.info("Wrote MOTD from DB (%d chars) to %s", len(db_motd), motd_path)
        return

    # 2. Explicit file reference
    if config.hub.motd_file:
        src = Path(config.hub.motd_file)
        if src.exists():
            import shutil
            shutil.copy2(src, motd_path)
            logger.info("Copied MOTD from %s → %s", src, motd_path)
            return
        else:
            logger.warning("motd_file %s not found, falling back", src)

    # 3. Inline MOTD text or description
    motd_text = config.hub.motd or config.hub.description
    if motd_text:
        motd_path.write_text(motd_text + "\n", encoding="utf-8")
        logger.info("Wrote MOTD (%d chars) to %s", len(motd_text), motd_path)


def _read_motd_from_db() -> str:
    """Try to read the MOTD from the SetupList table. Returns '' on failure."""
    try:
        import asyncio
        from sqlmodel import select
        from verlihub.models import SetupList
        from verlihub.models.database import get_database

        db = get_database()

        async def _query():
            async with db._session_factory() as session:
                result = await session.execute(
                    select(SetupList).where(
                        SetupList.file == "config",
                        SetupList.var == "hub_motd",
                    )
                )
                entry = result.scalar_one_or_none()
                return entry.val if entry else ""

        # We might be called from an already-running loop (run_both)
        try:
            loop = asyncio.get_running_loop()
            # Already in async context — run directly
            import concurrent.futures
            future = asyncio.run_coroutine_threadsafe(_query(), loop)
            return future.result(timeout=5)
        except RuntimeError:
            # No running loop — run synchronously
            return asyncio.run(_query())
    except Exception:
        logger.debug("Could not read MOTD from DB (probably not initialized yet)")
        return ""


async def run_both(config: "VerlihubConfig", args: argparse.Namespace) -> None:
    """Run both API server and hub concurrently.

    The hub is created and started *first* so that ``set_hub_context()``
    is populated before uvicorn boots.  The API lifespan will detect the
    existing context and skip creating a second hub.
    """
    import threading
    from verlihub.core import HubContext, create_hub, setup_signal_handlers
    from verlihub.api.deps import get_hub_context

    config_dir = args.config_dir or os.getenv("VH_CONFIG_DIR", ".")
    hub_port = args.hub_port or config.hub.port

    # --- Initialise database BEFORE the hub starts accepting connections ---
    # This eliminates the race window where _sync_db_lookup would fail
    # because the DB/event-loop wasn't ready yet.
    try:
        from verlihub.models.database import DatabaseConfig, init_database
        db_url = config.database.get_url(config_dir)
        db_config = DatabaseConfig(url=db_url)
        await init_database(config=db_config)
        logger.info("Database pre-initialised for hub auth callbacks")

        # Seed users from YAML config
        try:
            from verlihub.config import apply_config_to_db
            await apply_config_to_db(config)
        except Exception as seed_err:
            logger.warning("Config-to-DB seed (pre-hub): %s", seed_err)
    except Exception as db_err:
        logger.error("Database pre-init failed: %s — auth will reject until API starts", db_err)

    async with create_hub(config_dir) as ctx:
        # Give the event handler the current loop so cross-thread DB
        # lookups (OnValidateNick, OnCheckPassword) work immediately.
        ctx.events.set_event_loop(asyncio.get_running_loop())

        # Feed YAML config through the director callback
        hub_section: dict[str, str] = {}
        if config.hub.name:
            hub_section["hub_name"] = config.hub.name
        if config.hub.topic:
            hub_section["hub_topic"] = config.hub.topic
        if config.hub.description:
            hub_section["hub_desc"] = config.hub.description
        if config.hub.owner:
            hub_section["hub_owner"] = config.hub.owner
        if config.hub.encoding:
            hub_section["hub_encoding"] = config.hub.encoding
        if hub_port:
            hub_section["listen_port"] = str(hub_port)
        if config.hub.listen_host:
            hub_section["listen_ip"] = config.hub.listen_host
        ctx.events.set_config({"hub": hub_section})

        # Write MOTD file so C++ picks it up on Start()
        _write_motd_file(config, config_dir)

        if not ctx.initialize():
            raise RuntimeError("HubContext.initialize() failed")

        setup_signal_handlers(ctx)

        if not ctx.start(port=hub_port, listen_ip=config.hub.listen_host):
            raise RuntimeError(
                f"HubContext.start(port={hub_port}) failed"
            )

        logger.info("Hub running on %s:%d", config.hub.listen_host or "0.0.0.0", hub_port)

        # Now start the API in a daemon thread — the lifespan will see
        # the hub context that was just published.
        api_thread = threading.Thread(
            target=run_api_server,
            args=(config, args),
            daemon=True,
        )
        api_thread.start()

        await ctx.wait_for_shutdown()
        logger.info("Hub stopped")


def main() -> None:
    """Main entry point."""
    args = parse_args()
    
    # Version
    if args.version:
        try:
            from verlihub import __version__
            print(f"verlihub {__version__}")
        except ImportError:
            print("verlihub 1.7.0.0")
        return
    
    # Setup logging
    setup_logging(args.verbose, args.quiet)
    
    # Load configuration
    from verlihub.config import load_config, VerlihubConfig
    
    try:
        config_file = args.config or os.getenv("VH_CONFIG_FILE")
        # Default config_dir to current working directory if not specified
        config_dir = args.config_dir or os.getenv("VH_CONFIG_DIR") or str(Path.cwd())
        
        config = load_config(config_file=config_file, config_dir=config_dir)
        
        # Store config_dir in config for use by database initialization
        config._config_dir = config_dir
        
    except FileNotFoundError as e:
        logger.error("Configuration error: %s", e)
        sys.exit(1)
    except Exception as e:
        logger.error("Failed to load configuration: %s", e)
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)
    
    # Override with command line arguments
    if args.env:
        config.environment = args.env
    if args.mode:
        config.mode = args.mode
    
    # Validate configuration
    issues = config.validate()
    for issue in issues:
        if issue.startswith("CRITICAL"):
            logger.error(issue)
        else:
            logger.warning(issue)
    
    if args.validate:
        if any(i.startswith("CRITICAL") for i in issues):
            print("Configuration validation FAILED")
            sys.exit(1)
        else:
            print("Configuration validation OK")
            if issues:
                print(f"  {len(issues)} warning(s)")
            return
    
    # Check for critical issues in production
    if config.environment == "production":
        critical = [i for i in issues if i.startswith("CRITICAL")]
        if critical:
            logger.error("Cannot start in production mode with critical issues")
            for issue in critical:
                logger.error("  - %s", issue)
            sys.exit(1)
    
    # Publish config as the in-process singleton so all modules can
    # access it directly instead of going through environment variables.
    from verlihub.config import set_config
    set_config(config)

    # Apply config to environment (only needed for subprocesses / Docker)
    config.apply_to_env()
    
    # Synchronize config with database (DB values preferred unless --force)
    try:
        from verlihub.config import apply_config_to_db
        from verlihub.models.database import init_database, close_database
        from verlihub.models.database import DatabaseConfig as DbConfig
        
        async def _sync_config():
            # Initialize database
            db_url = config.database.get_url(config._config_dir)
            db_cfg = DbConfig(url=db_url)
            await init_database(config=db_cfg)
            # Apply YAML config respecting DB precedence
            await apply_config_to_db(config, force=getattr(args, 'force', False))
            await close_database()
        
        asyncio.run(_sync_config())
        logger.info("Config-to-DB sync complete")
    except Exception as e:
        logger.warning("Config-to-DB sync skipped: %s", e)
        if args.verbose:
            import traceback
            traceback.print_exc()
    
    # Get database display name
    config_dir = getattr(config, '_config_dir', str(Path.cwd()))
    db_display = config.database.display_name(config_dir)
    # Truncate if too long
    if len(db_display) > 45:
        db_display = db_display[:42] + "..."
    
    # Log startup info
    if not args.quiet:
        print(f"""
╔════════════════════════════════════════════════════════════════╗
║                  Verlihub Python Server                        ║
╠════════════════════════════════════════════════════════════════╣
║  Environment: {config.environment:<47} ║
║  Mode:        {config.mode:<47} ║
║  API:         {config.api.host}:{config.api.port:<42} ║
║  Database:    {db_display:<47} ║
║  Config dir:  {config_dir:<47} ║
╚════════════════════════════════════════════════════════════════╝
""")
    
    # Run based on mode.
    # Signal handling is mode-specific:
    #   api  – uvicorn installs its own SIGINT/SIGTERM handlers.
    #   hub  – setup_signal_handlers() uses asyncio.loop.add_signal_handler
    #          so Ctrl-C sets the shutdown event and the coroutine exits.
    #   both – same as hub (API thread is daemon and dies with the process).
    mode = config.mode
    
    if mode == "api":
        run_api_server(config, args)
    elif mode == "hub":
        asyncio.run(run_hub(config, args))
    elif mode == "both":
        asyncio.run(run_both(config, args))
    else:
        logger.error("Unknown mode: %s", mode)
        sys.exit(1)


if __name__ == "__main__":
    main()
