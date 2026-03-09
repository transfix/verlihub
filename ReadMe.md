# Verlihub 1.7.0.0

Verlihub is an advanced NMDC protocol server for Linux operating systems that provides high level functionality such as:

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
  * GeoIP enrichment (MaxMind + ip-api.com fallback)
  * Full NMDC tag parsing (client, version, mode, slots, hubs, status flags)
  * Search result ($SR) routing and clone detection
  * Embeddable dashboard widget for external sites
  * Plus much more

This repository contains **two ways to run a Verlihub hub**:

| | **Legacy Verlihub** | **verlihub-py** |
|---|---|---|
| Entry point | `verlihub` binary (C++) | `verlihub-server` (Python) |
| Database | MySQL / MariaDB required | SQLite, MySQL, or PostgreSQL |
| Configuration | MySQL `SetupList` table + config dir | Single YAML file |
| Web interface | — | FastAPI dashboard + REST API |
| Build | `cmake && make && make install` | `pip install .` |
| Plugins | Lua, Python, Perl (shared objects) | Same Lua/Python `.so` plugins |

Both share the same C++ networking core (see [Shared Code Architecture](#shared-code-architecture) below).

---

# Legacy Verlihub (C++ standalone server)

The original, battle-tested NMDC hub server that talks directly to MySQL.

## Dependencies

```
Required:
  GCC >= 4.8    CMake >= 3.16    MySQL >= 5.7    OpenSSL >= 1.1
  LibICU >= 55   ZLib   PCRE   GetText   MaxMindDB   Make

Optional:
  Lua >= 5.2    Dialog (for vh --install wizard)
```

**Debian / Ubuntu:**

```bash
sudo apt install g++ make cmake \
  libssl-dev libmysqlclient-dev libmaxminddb-dev \
  libicu-dev libpcre3-dev zlib1g-dev gettext libasprintf-dev

# Optional: Lua plugin support
sudo apt install liblua5.4-dev    # or liblua5.2-dev
```

## Build & Install

```bash
git clone https://github.com/verlihub/verlihub.git
cd verlihub
mkdir build && cd build
cmake ..
make -j$(nproc)
sudo make install
sudo ldconfig
```

### CMake Options

| Option | Default | Description |
|--------|---------|-------------|
| `CMAKE_INSTALL_PREFIX` | `/usr/local` | Installation prefix |
| `LIB_INSTALL_DIR` | `lib` | Library directory relative to prefix |
| `PLUGIN_INSTALL_DIR` | `lib` | Plugin directory relative to prefix |
| `WITH_LUA` | `ON` | Build Lua plugin |
| `WITH_PYTHON` | `ON` | Build Python scripting plugin |
| `USE_TLS_PROXY` | `OFF` | Build Go TLS proxy |
| `USE_FEARTLS_PROXY` | `OFF` | Use FearTLS proxy |

## First-time Setup

After installing, use the interactive setup wizard:

```bash
vh --install
```

This walks you through:
  1. MySQL database creation and credentials
  2. Hub port, name, and address
  3. Master admin account
  4. Config directory (`/etc/verlihub` by default)

## Running the Legacy Server

```bash
# Start the hub (foreground)
verlihub -c /etc/verlihub

# Using the vh management script
vh --run                    # start hub
vh --stop                   # stop hub
vh --restart                # restart hub
vh --status                 # check if running
vh --install                # interactive setup wizard

# Register users from the command line
vh_regimporter --help       # import users from PtokaX/Aquila/YnHub

# vhm - Verlihub Manager
vhm                        # menu-based management interface
```

## Legacy Management Commands

Once connected as admin via a DC client:

```
!reload          # reload hub configuration
!reguser <nick> <class>  # register a user
!delreg <nick>   # remove a registered user
!topic <text>    # change hub topic
!kick <nick>     # kick a user
!ban <nick> <time> <reason>  # ban a user
!luaload <script>   # load a Lua script
!lualist            # list loaded Lua scripts
```

---

# Python Stack (verlihub-py)

The `python/` directory contains a complete Python-based management stack:

  * **NMDC Hub Server** — C++ NMDC protocol server with Python-managed database (SQLite, PostgreSQL, or MySQL)
  * **REST API** — FastAPI-based API for hub management (users, bots, plugins, console, config)
  * **Web Dashboard** — Jinja2 + SPA dashboard with real-time monitoring via WebSockets
  * **YAML Configuration** — single config file for hub, API, plugins, Lua, and more
  * **GeoIP Enrichment** — MaxMind .mmdb + ip-api.com fallback for country, city, ISP
  * **User Insights** — clone detection, share stats, geo distribution, status flags
  * **Embeddable Widget** — standalone dashboard embed for external sites (`/dashboard/embed`)

## Quick Start

```bash
cd python
pip install -e .
verlihub-py --config config.yml
```

See `python/config.example.yml` for all available configuration options.

## Programmatic Usage

You can instantiate and run a Verlihub server entirely from Python — in a
script or an interactive REPL:

```python
from verlihub.config import VerlihubConfig, load_config

# Option 1: load from a YAML file
config = VerlihubConfig.from_yaml("config.yml")

# Option 2: construct in-memory with all defaults (SQLite, localhost:8000)
config = VerlihubConfig()

# Option 3: construct from a plain dict
config = VerlihubConfig.from_dict({
    "database": {"type": "sqlite", "path": ":memory:"},
    "api": {"port": 9000, "username": "admin", "password": "secret"},
    "hub": {"name": "My Hub", "port": 411},
})

# Apply config to environment variables (consumed by FastAPI lifespan)
config.apply_to_env()
```

Then start the API server (runs uvicorn in the current process):

```python
import uvicorn
uvicorn.run("verlihub.api.app:app", host="127.0.0.1", port=9000)
```

Or drive it from asyncio for scripting / testing:

```python
import asyncio
from verlihub.api.app import create_app
from verlihub.models.database import DatabaseConfig, init_database, close_database

async def main():
    config = VerlihubConfig.from_dict({
        "database": {"type": "sqlite", "path": ":memory:"},
        "api": {"port": 9000},
    })
    config.apply_to_env()

    # initialise the database yourself
    await init_database(config=DatabaseConfig())

    app = create_app()          # FastAPI instance, ready for testing
    # ... use httpx.AsyncClient(app=app) for in-process requests ...

    await close_database()

asyncio.run(main())
```

If the C++ NMDC core is compiled (`-DBUILD_PYTHON_BINDINGS=ON`), you can also
start the hub:

```python
from verlihub.core import HubContext

ctx = HubContext.create("/etc/verlihub")
ctx.initialize()
ctx.start(port=411, listen_ip="0.0.0.0")  # NMDC hub now accepting connections
```

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

## REST API Endpoints

The API is served at `/api/v1/` and documented interactively at `/docs` (Swagger UI).
Authentication uses JWT tokens issued via `/api/v1/auth/login`.

| Endpoint | Method | Permission | Description |
|----------|--------|------------|-------------|
| `/api/v1/hub/status` | GET | Public | Hub status (running, users, share) |
| `/api/v1/hub/info` | GET | Public | Hub name, topic, MOTD, address |
| `/api/v1/hub/config` | GET | Admin (5) | Full hub configuration |
| `/api/v1/hub/config` | PUT | Admin (5) | Update hub config fields |
| `/api/v1/users/online` | GET | VIP (3) | Online users with enrichment |
| `/api/v1/users/online/{nick}` | GET | VIP (3) | Single user detail |
| `/api/v1/stats/users/detailed` | GET | VIP (3) | Users with GeoIP, clones, share |
| `/api/v1/stats/geo` | GET | VIP (3) | Geographic distribution |
| `/api/v1/stats/share` | GET | VIP (3) | Share statistics |
| `/dashboard/` | GET | Cookie | SPA dashboard |
| `/dashboard/embed` | GET | Public | Embeddable widget |

The online-user responses include extended fields: `client_version`, `mode`,
`slots`, `hubs_normal`/`hubs_registered`/`hubs_operator`, `status_flag`
(bitmask: 1=Normal, 2=Away, 4=Server, 8=Fireball, 16=TLS, 32=NAT),
`supports`, `login_time`, and GeoIP enrichment (`country_name`, `city`,
`hostname`, `is_clone`, etc.).

## Hub List Directory

Verlihub-py has built-in hublist support with two complementary features:

### 1. Registration Client — Register This Hub on External Hublists

Periodically POSTs hub info (name, address, users, share, etc.) to external
hublist servers using the standard NMDC hublist protocol.

```yaml
hub:
  hublist_servers:
    - hublist.te-home.net
    - hublist.pwiam.com
    - my-private-hublist.org
```

The registration client runs as a background task during the FastAPI lifespan
and sends form-encoded requests every 10 minutes (configurable via
`hublist.registration_interval`).

### 2. Hublist Server — Act as a Hub Directory

Turn this Verlihub-py instance into a hublist directory server that other hubs
can register on. Enabled via configuration:

```yaml
hublist:
  server_enabled: true
  registration_interval: 600   # client re-registration interval (seconds)
  stale_timeout: 1800          # prune hubs not pinged within 30 minutes
```

**API Endpoints** (under `/api/v1/hublist`):

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/v1/hublist/` | GET | Public | Download hublist (XML or JSON via `?fmt=json`) |
| `/api/v1/hublist/stats` | GET | Public | Hublist stats (total hubs, users, share) |
| `/api/v1/hublist/register` | POST | Public | Register / ping a hub (form-encoded or JSON) |
| `/api/v1/hublist/{id}` | DELETE | Admin | Remove a hub entry |

The registration endpoint accepts both NMDC-standard form fields (`Name`,
`Host`, `Description`, `Users`, `Share`, etc.) and JSON payloads. Same-address
registrations are upserted (updated, not duplicated).

### Dashboard Configuration

The Network tab in the dashboard settings (`/dashboard/config`) provides:
  * **Register on Hub Lists** — toggle + multi-line server list
  * **Run Built-in Hublist Server** — toggle to enable the directory

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

# Shared Code Architecture

Both the legacy `verlihub` binary and `verlihub-py` are built from the same
source tree and share the proven C++ networking and protocol code:

```
src/                          ← shared C++ source (libverlihub.so)
├── casyncsocketserver.*      ← async I/O event loop (poll/select)
├── casyncconn.*              ← non-blocking TCP connection handling
├── cconndc.*                 ← DC-specific connection state machine
├── cdcproto.*                ← full NMDC protocol parser (legacy)
├── cserverdc.*               ← legacy hub server (MySQL-dependent)
├── cmysql.*                  ← MySQL client wrapper
├── cuser.* / cusercollection.* ← user management, MyINFO storage
├── cpluginmanager.*          ← plugin loading (Lua, Python, Perl)
├── czlib.*                   ← zlib compression for ZPipe
├── cicuconvert.*             ← ICU charset conversion
├── cmaxminddb.*              ← GeoIP lookups
├── cpcre.*                   ← PCRE regex engine
├── clog.*                    ← logging framework
├── stringutils.*             ← string helpers
├── ...                       ← ~60 more legacy source files
│
├── core/                     ← new C++20 core library (verlihub_core_lib)
│   ├── hub_context.*         ← HubContext: thread-safe central state
│   ├── nmdc_hub_server.*     ← NMDC protocol server (auth via Python)
│   ├── nmdc_protocol.*       ← standalone NMDC protocol utilities
│   └── thread_safe_collections.* ← lock-free containers
│
└── swig/                     ← Python bindings (SWIG)
    └── verlihub_core.i       ← interface exposing HubContext to Python

plugins/                      ← shared plugin binaries
├── lua/                      ← liblua_pi.so — Lua scripting
├── python/                   ← libpython_pi.so — Python scripting
├── plugman/                  ← plug_pi.so — plugin manager
├── chatroom/ forbid/ floodprot/ iplog/ isp/ messenger/ replacer/ stats/
└── perl/                     ← libperl_pi.so — Perl scripting
```

## What each version uses

**Legacy `verlihub` binary** uses all of `src/*.cpp` compiled into
`libverlihub.so`, plus the `verlihub` executable (`verlihub.cpp`) which
bootstraps `cServerDC` → MySQL → starts the event loop. Plugins load from
disk at runtime.

**`verlihub-py`** uses:

| Layer | Source | Purpose |
|-------|--------|---------|
| **Socket I/O** | `casyncsocketserver.*`, `casyncconn.*` | The same battle-tested async event loop (poll/select) |
| **Connection handling** | `cconndc.*`, `cconnchoose.*`, `cconnpoll.*` | TCP connection state machine, shared with legacy |
| **Protocol** | `cdcproto.*` + `core/nmdc_protocol.*` | Legacy parser linked in; new standalone parser in core |
| **Compression** | `czlib.*` | ZPipe support, same code |
| **Encoding** | `cicuconvert.*` | ICU charset conversion, same code |
| **GeoIP** | `cmaxminddb.*` | MaxMindDB lookups, same code |
| **Plugin system** | `cpluginmanager.*`, `cpluginbase.*` | Same `.so` plugin ABI — Lua/Python plugins work on both |
| **Hub server** | `core/nmdc_hub_server.*` | **New:** inherits `cAsyncSocketServer`, no MySQL dependency |
| **Hub context** | `core/hub_context.*` | **New:** thread-safe state, replaces global singletons |
| **SWIG bridge** | `swig/verlihub_core.i` | Exports `HubContext` to Python via `_verlihub_core.so` |

The key difference: `NMDCHubServer` (`src/core/`) inherits from
`cAsyncSocketServer` (`src/`) to reuse the socket infrastructure, but
delegates all authentication and persistence to Python through the
`IHubEventCallback` interface. This removes the hard MySQL dependency while
keeping the proven networking code.

## Build targets

```
libverlihub.so          ← all src/*.cpp (legacy + shared code)
verlihub_core_lib.a     ← src/core/*.cpp (new C++20 library, links libverlihub.so)
_verlihub_core.so       ← SWIG wrapper (links both above)
verlihub                ← legacy binary (links libverlihub.so), not built for pip
test_nmdc_protocol      ← 142 C++ tests: protocol parsing, tags, search results, status flags
test_nmdc_hub_server    ← 29 C++ tests: hub server config, client struct defaults
test_hub_context        ← 32 C++ tests: lifecycle, config, events, signals, snapshots
test_geo_ip_lookup      ← 25 C++ tests: MaxMind .mmdb lookups
test_thread_safe_collections ← 31 C++ tests: thread-safe maps, counters, user collections
```

Python tests include 59 hublist-specific tests covering model CRUD,
XML/JSON serialization, stale pruning, registration client mocking,
endpoint integration (GET/POST/DELETE), config round-trip, and input
validation.

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
