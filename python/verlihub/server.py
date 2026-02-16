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
    
    try:
        await run_hub_server(
            config_dir=config_dir,
            port=hub_port,
            listen_ip=config.hub.listen_host,
        )
    except Exception as e:
        logger.error("Hub error: %s", e)
        sys.exit(1)


async def run_both(config: "VerlihubConfig", args: argparse.Namespace) -> None:
    """Run both API server and hub concurrently."""
    import threading
    
    # Run API in a thread
    api_thread = threading.Thread(
        target=run_api_server,
        args=(config, args),
        daemon=True,
    )
    api_thread.start()
    
    # Run hub in main asyncio loop
    await run_hub(config, args)


def main() -> None:
    """Main entry point."""
    args = parse_args()
    
    # Version
    if args.version:
        try:
            from verlihub import __version__
            print(f"verlihub {__version__}")
        except ImportError:
            print("verlihub 0.1.0")
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
    
    # Apply config to environment
    config.apply_to_env()
    
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
    
    # Handle shutdown signals
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal, exiting...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run based on mode
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
