# Verlihub 1.7.0.0

Verlihub is advanced NMDC protocol server for Linux operating systems that provides high level functionality such as:

  * Low RAM and CPU usage
  * It can hold more than 25k users
  * TLS-encrypted connection via NMDCS protocol, requires Go or FearTLS
  * Utility scripts for simple installation and hub management
  * Export and import of hub setting via MySQL or MariaDB
  * User management with privilegies
  * Logging of user IPs and complete MyINFOs, requires Ledokol
  * Clients and connections management
  * Extend functionality with Lua and Python scripts
  * Generate statistics for your hub, requires Ledokol
  * Import users from PtokaX, Aquila or YnHub
  * Web dashboard with real-time hub monitoring
  * REST API for programmatic hub management
  * Plus much more

# Python Stack (verlihub-py)

The `python/` directory contains a complete Python-based management stack:

  * **NMDC Hub Server** — database-free NMDC server with SQLite or MySQL backend
  * **REST API** — FastAPI-based API for hub management (users, bots, plugins, console)
  * **Web Dashboard** — Jinja2 + SPA dashboard with real-time monitoring
  * **YAML Configuration** — single config file for hub, API, plugins, Lua, and more

## Quick Start

```bash
cd python
pip install -e .
verlihub-py --config config.yml
```

See `python/config.example.yml` for all available configuration options.

# Lua Scripts & Ledokol

Verlihub supports Lua scripting through the `liblua_pi.so` plugin. Scripts are
loaded at runtime with `!luaload <script>` and managed with `!lualist`,
`!luaunload`, and `!luareload`.

## Ledokol

[Ledokol](https://github.com/Verlihub/ledokol) by RoLex is the flagship Lua
script for Verlihub with 70+ features including:

  * Chat management — message history, chat clearing, triggers
  * Security — anti-spam, anti-flood, connection protection, nick guards
  * Content — hub news, releases, auto-responses, replacement strings
  * Entertainment — calculator, jokes, quotes
  * Administration — user registration, hub lists, right-click menus

Ledokol is fetched directly from GitHub to ensure you always get the latest
version. It is **not** bundled in this repository.

### Loading Ledokol

```
!luaload ledokol.lua
!ledohelp          # show all commands
!ledoconf          # show current config
!ledostats         # show statistics
!ledoset <key> <value>  # change a setting
```

### YAML Configuration

Both `production.example.yml` and `python/config.example.yml` support a `lua:`
section:

```yaml
lua:
  enabled: true
  github_scripts:
    - repo: Verlihub/ledokol
      files:
        - ledokol.lua
  autoload:
    - ledokol.lua
  script_config:
    ledokol:
      calculator: "1"
      hubchat_history: "50"
      anti_spam: "1"
```

When `lua.enabled` is `true`:
  1. Scripts listed in `github_scripts` are cloned from GitHub on startup
  2. The Lua plugin (`liblua_pi.so`) is registered and loaded
  3. Scripts in `autoload` are loaded via `!luaload` after the hub starts
  4. Per-script settings in `script_config` are applied via `!ledoset` (for ledokol) or equivalent commands

## Dashboard Integration

The web dashboard at `/dashboard/plugins` provides a full UI for managing Lua
scripts alongside Python scripts:

  * Load, unload, and reload Lua scripts
  * Dedicated **Ledokol** management panel with:
    - Live status indicator (loaded / not loaded)
    - Quick-action buttons for common commands
    - Feature cards for Chat, Content, Security, Users, and Hub management
    - Free-form command input with output display

# Docker Production Deployment

The `run_production.sh` script and `docker-compose` setup provide a complete
production deployment:

```bash
cp production.example.yml production.yml
# Edit production.yml with your settings
./run_production.sh start
```

The production runtime handles:
  * MySQL database initialization
  * Plugin registration (Python, Lua)
  * Ledokol download from GitHub
  * Script autoloading
  * API server with authentication
  * TLS proxy (optional)

See `production.example.yml` for the full configuration reference.

# TLS proxy

Currently there are two supported libraries:

  * Build Go TLS library using `USE_TLS_PROXY` flag: https://github.com/verlihub/tls-proxy#readme
  * Use FearTLS library using `USE_FEARTLS_PROXY` flag: https://github.com/rolex/feartls#readme

# Testing

```bash
# Unit tests (Python stack)
cd python && pytest tests/ -v

# Ledokol integration tests (Docker)
./run_ledokol_tests.sh

# Full integration tests
./run_integration_tests.sh
```

# Links

  * Website: https://github.com/verlihub
  * Wiki: https://github.com/verlihub/verlihub/wiki
  * Crash: https://crash.verlihub.net
  * Translate: https://explore.transifex.com/feardc/verlihub
  * Ledokol: https://ledo.feardc.net
  * MMDB: https://ledo.feardc.net/mmdb
  * Support: nmdcs://hub.verlihub.net:7777
  * Hublist: https://te-home.net/?do=hublist

# Donation

PayPal: [webmaster@feardc.net](https://www.paypal.com/paypalme/feardc/)
