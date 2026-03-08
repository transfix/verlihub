# Verlihub Docker Guide

This guide explains how to configure and run Verlihub using Docker for both development and production environments.

## Table of Contents

- [Quick Start](#quick-start)
- [Development Environment](#development-environment)
- [Production Environment](#production-environment)
- [Configuration Reference](#configuration-reference)
- [TLS/SSL Configuration](#tlsssl-configuration)
- [User Management](#user-management)
- [Matterbridge Integration](#matterbridge-integration)
- [FastAPI Web Interface](#fastapi-web-interface)
- [LLM Chat & MCP Integration](#llm-chat--mcp-integration)
- [Troubleshooting](#troubleshooting)

## Quick Start

### Development (Testing)

```bash
# Run integration tests
sg docker ./run_integration_tests.sh

# Or run with docker-compose directly
sg docker -c "docker compose up --build"
```

### Production

```bash
# 1. Copy and customize the configuration
cp production.example.yml production.yml
# Edit production.yml with your settings

# 2. Start the hub
sg docker -c "./run_production.sh --config production.yml"

# 3. View logs
sg docker -c "./run_production.sh --logs"

# 4. Stop the hub
sg docker -c "./run_production.sh --stop"
```

## Development Environment

The development setup uses `docker-compose.yml` for quick testing and development.

### Files

- `docker-compose.yml` - Development compose configuration
- `docker/Dockerfile` - Container build configuration
- `docker/entrypoint.sh` - Container initialization script
- `run_integration_tests.sh` - Automated test runner

### Running Development Environment

```bash
# Build and start
sg docker -c "docker compose up --build"

# Run in background
sg docker -c "docker compose up -d --build"

# View logs
sg docker -c "docker compose logs -f verlihub"

# Stop and remove
sg docker -c "docker compose down"
```

### Environment Variables

The development environment supports these variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MYSQL_HOST` | mysql | MySQL server hostname |
| `MYSQL_USER` | verlihub | Database username |
| `MYSQL_PASS` | verlihub | Database password |
| `MYSQL_DB` | verlihub | Database name |
| `HUB_NAME` | Test Hub | Hub display name |
| `HUB_PORT` | 4111 | Hub listening port |
| `ADMIN_NICK` | admin | Administrator nickname |
| `ADMIN_PASS` | test123 | Administrator password |
| `PYTHON_MODE` | single | Python interpreter mode |

## Production Environment

The production setup uses a YAML configuration file for comprehensive hub configuration.

### Files

- `production.example.yml` - Example configuration (copy to `production.yml`)
- `run_production.sh` - Production launcher script
- `docker/run_commands.py` - NMDC command runner for post-startup configuration
- `docker/register_users.py` - Multi-user registration utility

### Production Script Commands

```bash
# Start production instance
./run_production.sh [--config FILE]

# Stop production instance
./run_production.sh --stop

# Restart (applies config changes)
./run_production.sh --restart

# View logs
./run_production.sh --logs

# Show status
./run_production.sh --status

# Force rebuild images
./run_production.sh --rebuild

# Skip startup commands
./run_production.sh --skip-commands

# Enable debug output
./run_production.sh --debug
```

### Persistent Data

Production mode uses Docker volumes for persistent data:

- **Config Volume** (`verlihub-production-config`): Hub configuration files, MOTD, TLS certificates
- **MySQL Volume** (`verlihub-production-mysql`): Database files

Data persists across container restarts. Configuration changes are applied idempotently on restart without data loss.

## Configuration Reference

### Database Configuration

```yaml
database:
  host: mysql           # MySQL hostname (use 'mysql' for Docker internal)
  user: verlihub        # Database username
  password: verlihub    # Database password (change in production!)
  name: verlihub        # Database name
  port: 3306            # MySQL port
```

### Hub Configuration

```yaml
hub:
  # Basic identity
  name: "My DC++ Hub"           # Hub name shown to clients
  description: "Welcome!"       # Hub description
  host: "nmdcs://hub.example.com:411"  # Full hub address for hublist
  owner: "YourName"             # Hub owner name
  topic: "Hub topic/tagline"    # Shown in some clients
  category: "General"           # Hub category for hublist
  
  # Branding URLs
  icon_url: "https://example.com/favicon.ico"
  logo_url: "https://example.com/logo.png"
  
  # Character encoding (default: CP1252)
  encoding: "CP1252"
  
  # Network settings
  port: 4111                    # Listening port
  listen_host: "0.0.0.0"        # Listen address (0.0.0.0 for all)
  extra_ports: "411 5555"       # Additional ports (space-separated)
  
  # MOTD file
  motd_file: "motd.txt"         # Path to Message of the Day file
```

### Hub Bots

```yaml
bots:
  security:
    nick: "Hub-Security"
    description: "Hub security system"
  opchat:
    nick: "OpChat"
    description: "Operator chat"
    min_class: 3                # Minimum class to access (3 = operators)
```

### Connection Limits

```yaml
limits:
  max_users: 6000               # Maximum total users
  max_users_per_ip: 0           # Max from single IP (0 = unlimited)
  max_passive_users: -1         # Max passive users (-1 = unlimited)
  
  # Optional: limits by user class
  max_users_by_class:
    guest: 6000
    registered: 1000
    vip: 1000
    operator: 1000
```

### Share Requirements

```yaml
share:
  min_share: 0                  # Minimum share in bytes (0 = none)
  max_share: 0                  # Maximum share (0 = no limit)
  passive_multiplier: 1.0       # Multiplier for passive users
  
  # Optional: per-class minimums
  min_share_by_class:
    registered: 1073741824      # 1 GB
    vip: 0
    operator: 0
```

### Nick Requirements

```yaml
nick:
  min_length: 1
  max_length: 64
  allowed_chars: ""             # Regex, empty = all allowed
  prefix: ""                    # Required prefix for all nicks
  autoreg_prefix: ""            # Prefix for auto-registered users
```

### Chat Settings

```yaml
chat:
  max_message_length: 256
  max_pm_length: 1024
  max_lines_per_message: 5
  min_class: 0                  # Min class to chat (0 = guests)
  disable_me: false
  default_enabled: true
```

### Search Settings

```yaml
search:
  min_chars: 4                  # Minimum search query length
  interval:
    guest: 32                   # Seconds between searches
    registered: 16
    vip: 8
    operator: 1
    passive: 48
```

### Security Settings

```yaml
security:
  min_password_length: 6
  password_encryption: 2        # 0=plain, 1=encrypt, 2=MD5
  
  clone_detection:
    enabled: false
    count: 0                    # Clones before action (0 = disabled)
    report: true
    ban_time: 1800              # Seconds
  
  flood_protection:
    action: 3                   # 0=ignore, 1=warn, 2=drop, 3=ban
    ban_time: 1800
    report: true
  
  hide_kicks: true
  kick_ban_time: 300            # Temp ban after kick (seconds)
```

### Registration Settings

```yaml
registration:
  auto_register_class: 0        # 0 = disabled
  min_class_to_register: 4      # Min class that can register others
  request_password: true        # Ask password for registered nicks
  allow_password_change: true
  disable_regme: false
```

### Hublist Registration

Register this hub on external hublist servers and optionally host a
built-in hublist directory for other hubs:

```yaml
# External hublist servers to register on (in hub section)
hub:
  hublist_servers:
    - hublist.te-home.net
    - hublist.pwiam.com

# Built-in hublist server (optional)
hublist:
  server_enabled: true         # serve directory at /api/v1/hublist
  registration_interval: 600   # client re-registration interval (seconds)
  stale_timeout: 1800          # prune hubs not pinged within 30 min
```

### Permissions

```yaml
permissions:
  oplist_class: 3               # Min class to see op list
  user_ip_class: 3              # Min class to see user IPs
  ban_bypass_class: 10          # Min class to bypass bans
  topic_mod_class: 4            # Min class to modify topic
  plugin_mod_class: 5           # Min class to modify plugins
  broadcast_class: 4            # Min class to broadcast
  redirect_class: 4             # Min class to redirect users
  
  class_differences:
    kick: 0                     # Same class can kick each other
    register: 2                 # 2 class levels higher to register
    pm: 10                      # Higher class can PM anyone
    download: 10                # Higher class can download from anyone
```

### Advanced Settings

```yaml
advanced:
  dns_lookup: false
  extended_welcome: true
  zlib_enabled: false           # Compression (disable for compatibility)
  log_level: 0                  # 0-5, higher = more verbose
  extjson_enabled: false
  allow_same_user: true         # Same nick from multiple connections
  filter_lan_requests: false
```

### Python Plugin Mode

```yaml
python_mode: single  # or 'multi'
```

| Mode | Description |
|------|-------------|
| `single` | Single interpreter with dispatcher. Supports FastAPI, threading, and shared state between scripts. |
| `multi` | Sub-interpreters with script isolation. Better security but no FastAPI support. |

### Docker Settings

```yaml
docker:
  config_volume: verlihub-production-config
  mysql_volume: verlihub-production-mysql
  network: verlihub-production-net
  container_prefix: vh-prod
  restart_policy: unless-stopped  # no, always, on-failure, unless-stopped
```

## TLS/SSL Configuration

Enable encrypted NMDCS connections for clients.

> **Note**: Requires Verlihub built with `USE_FEARTLS_PROXY=ON` or `USE_TLS_PROXY=ON`

### Basic TLS (Self-Signed Certificate)

```yaml
tls:
  enabled: true
  internal_port: 411      # Internal proxy port
  only_mode: false        # Allow both TLS and non-TLS clients
  min_version: 2          # TLS 1.2 minimum
  cert_org: "My Hub"
  cert_email: "admin@example.com"
```

Verlihub will automatically generate a self-signed certificate.

### TLS with Custom Certificate

```yaml
tls:
  enabled: true
  internal_port: 411
  cert_file: "/path/to/certificate.pem"
  key_file: "/path/to/private.key"
```

### TLS with Let's Encrypt

```yaml
tls:
  enabled: true
  internal_port: 411
  cert_file: "/etc/letsencrypt/live/hub.example.com/fullchain.pem"
  key_file: "/etc/letsencrypt/live/hub.example.com/privkey.pem"
```

### TLS-Only Mode

Force all clients to use encrypted connections:

```yaml
tls:
  enabled: true
  only_mode: true   # Reject non-TLS connections
```

### TLS Version Settings

| Value | TLS Version |
|-------|-------------|
| 0 | TLS 1.0 |
| 1 | TLS 1.1 |
| 2 | TLS 1.2 (default, recommended) |
| 3 | TLS 1.3 |

### Client Connection

With TLS enabled, clients can connect using:
- **Encrypted**: `nmdcs://hostname:port`
- **Unencrypted**: `dc://hostname:port` (unless `only_mode: true`)

## User Management

### User Classes

| Class | Level | Description |
|-------|-------|-------------|
| Masters | 10 | Full hub control, can manage all users |
| Admins | 5 | User management, kicks, bans |
| Operators | 3 | Basic moderation capabilities |
| VIPs | 2 | Extra privileges (e.g., bypass limits) |
| Registered | 1 | Registered users |

### Configuration

```yaml
users:
  masters:
    - nick: admin
      password: secure_password_here
      note: "Primary administrator"
    - nick: admin2
      password: another_password
      
  admins:
    - nick: moderator1
      password: mod_password
      note: "Trusted moderator"
      
  operators:
    - nick: op1
      password: op_password
      
  vips:
    - nick: vipuser
      password: vip_password
      
  registered:
    - nick: regular1
      password: user_password
```

### User Registration Behavior

- Users are registered/updated on every hub start
- Uses `INSERT ... ON DUPLICATE KEY UPDATE` for idempotency
- Existing users are updated, not duplicated
- Removing a user from config does NOT delete them from the database

## Matterbridge Integration

Bridge hub chat with Discord, Slack, IRC, Telegram, Matrix, and more.

### Prerequisites

1. Running [Matterbridge](https://github.com/42wim/matterbridge) instance
2. Matterbridge API enabled in `matterbridge.toml`

### Configuration

```yaml
matterbridge:
  enabled: true
  api_url: "http://matterbridge:4242"
  api_token: "your-api-token"        # Optional
  gateway: "verlihub"                 # Must match matterbridge.toml
  channel: "#general"
  bot_nick: "[Bridge]"
  min_class_to_send: 0               # 0=all, 1=registered, 3=operators
  ignore_nicks: []                    # Nicks to ignore
```

### Matterbridge Configuration Example

In `matterbridge.toml`:

```toml
[api]
BindAddress = "0.0.0.0:4242"
Token = "your-api-token"

[api.verlihub]
Token = "your-api-token"

[[gateway]]
name = "verlihub"
enable = true

[[gateway.inout]]
account = "api.verlihub"
channel = "api"

[[gateway.inout]]
account = "discord.myserver"
channel = "#general"
```

## FastAPI Web Interface

The hub includes a built-in FastAPI server for REST API and web interface.

> **Note**: Only available with `python_mode: single`

### Configuration

```yaml
api:
  enabled: true
  port: 30000
  cors_origins:
    - "https://your-domain.com"
    - "http://localhost:3000"
```

### Startup Commands

The API server must be started via hub command:

```yaml
startup_commands:
  - "!onplug python"          # Load Python plugin
  - "!api start 30000"        # Start API on port 30000
```

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | API status and available endpoints |
| `GET /app` | Web application interface |
| `GET /health` | Health check |
| `GET /users` | List connected users |
| `GET /stats` | Hub statistics |

### Accessing the Web Interface

```
http://your-hub-host:30000/app
```

## Startup Commands

Execute hub commands automatically after startup:

```yaml
startup_commands:
  - "!onplug python"           # Load Python plugin
  - "!api start 30000"         # Start FastAPI server
  - "!set hub_security 1"      # Configure hub setting
  - "!topic Welcome to the hub!"

plugin_commands:
  - "+py load my_script"       # Load custom Python script
  - "!bridge start"            # Start matterbridge (if configured)
```

Commands are executed as the first master user via NMDC protocol.

## LLM Chat & MCP Integration

Verlihub-py can integrate with any OpenAI-compatible LLM endpoint to provide
AI-powered chat through the Hub-Security bot and an MCP (Model Context Protocol)
server for external AI clients.

When enabled:

- Users can **PM Hub-Security** for tool-assisted AI chat (tools vary by user class)
- Users can **mention Hub-Security in main chat** for conversational replies
- The **MCP endpoint** at `/api/v1/mcp` exposes hub tools to external AI clients

### Example: Remote vLLM Endpoint (no GPU required)

Use a remote OpenAI-compatible server such as vLLM. No Ollama sidecar is
started — the hub calls the remote API directly.

**production.yml:**

```yaml
edition: py

database:
  type: postgresql
  host: postgres
  user: verlihub
  password: verlihub
  name: verlihub
  port: 5432

hub:
  name: "My Local Hub"
  description: "Local dev hub with LLM + dashboard"
  port: 4111

users:
  masters:
    - nick: admin
      password: changeme

api:
  enabled: true
  port: 30000

llm:
  enabled: true
  base_url: "https://vllm-qwen35-35b.tinyhost.xyz/v1"
  model: "cyankiwi/Qwen3.5-35B-A3B-AWQ-4bit"
  api_key: "none"
  temperature: 0.3
  max_tokens: 2048
  max_tool_rounds: 8
  min_class: 3
  admin_class: 5

mcp:
  enabled: true
  min_class: 3
  admin_class: 5

lua:
  enabled: true
  github_scripts:
    - repo: "Verlihub/ledokol"
      files: ["ledokol.lua"]
  autoload: ["ledokol.lua"]

python:
  enabled: true
python_mode: single

startup_commands:
  - "!onplug python"
  - "!onplug lua"
  - "!api start 30000"
```

**Launch:**

```bash
sg docker -c "./run_production.sh --config production.yml --edition py"
```

### Example: Local Ollama Sidecar (CPU, no GPU)

When `llm.base_url` points to the default Ollama sidecar
(`http://vh-prod-ollama:11434/v1`), `run_production.sh` automatically starts an
Ollama container and pulls the configured model on first boot.

**production.yml** — only the `llm:` section differs:

```yaml
edition: py

database:
  type: postgresql
  host: postgres
  user: verlihub
  password: verlihub
  name: verlihub
  port: 5432

hub:
  name: "My Local Hub"
  port: 4111

users:
  masters:
    - nick: admin
      password: changeme

api:
  enabled: true
  port: 30000

llm:
  enabled: true
  # Default Ollama sidecar URL — run_production.sh starts Ollama for you
  base_url: "http://vh-prod-ollama:11434/v1"
  model: "qwen2.5:0.5b"         # ~400 MB, runs on CPU
  # model: "qwen2.5:3b"          # ~2 GB,  better quality
  # model: "qwen2.5:7b"          # ~4.5 GB, best quality (GPU recommended)
  api_key: "ollama"              # Ollama ignores this but the client requires it
  temperature: 0.3
  max_tokens: 2048
  min_class: 3
  admin_class: 5
  # Expose Ollama API on the host (optional, 0 = don't expose)
  # ollama_port: 11434

mcp:
  enabled: true
  min_class: 3
  admin_class: 5

lua:
  enabled: true
  github_scripts:
    - repo: "Verlihub/ledokol"
      files: ["ledokol.lua"]
  autoload: ["ledokol.lua"]

python:
  enabled: true
python_mode: single

startup_commands:
  - "!onplug python"
  - "!onplug lua"
  - "!api start 30000"
```

**Launch** (same command — the script detects the Ollama URL and starts the sidecar):

```bash
sg docker -c "./run_production.sh --config production.yml --edition py"
```

### What You Get

| Component | Access |
|-----------|--------|
| DC++ hub (NMDC) | `dchub://localhost:4111` |
| Dashboard / REST API | `http://localhost:30000` |
| LLM chat | PM **Hub-Security** or mention it in main chat (class 3+) |
| MCP endpoint | `http://localhost:30000/api/v1/mcp` |
| PostgreSQL | Internal on `postgres:5432` |

### Lifecycle Commands

```bash
sg docker -c "./run_production.sh --logs"       # tail logs
sg docker -c "./run_production.sh --status"     # container status
sg docker -c "./run_production.sh --stop"       # shut down
sg docker -c "./run_production.sh --restart"    # restart
```

### LLM Configuration Reference

| Key | Default | Description |
|-----|---------|-------------|
| `llm.enabled` | `false` | Enable LLM chat integration |
| `llm.base_url` | `http://vh-prod-ollama:11434/v1` | OpenAI-compatible API base URL |
| `llm.model` | `qwen2.5:7b` | Model name to use |
| `llm.api_key` | `ollama` | API key (Ollama ignores this) |
| `llm.temperature` | `0.3` | Generation temperature |
| `llm.max_tokens` | `2048` | Maximum tokens per response |
| `llm.max_tool_rounds` | `8` | Max tool-calling iterations per request |
| `llm.min_class` | `3` (operator) | Minimum user class to access LLM chat |
| `llm.admin_class` | `5` (master) | Minimum class for admin-level LLM tools |
| `llm.ollama_port` | `0` | Expose Ollama API on this host port |
| `mcp.enabled` | `false` | Enable MCP server endpoint |
| `mcp.min_class` | `3` | Minimum class for MCP access |
| `mcp.admin_class` | `5` | Minimum class for admin MCP tools |

### Tips

- **No GPU?** Use `qwen2.5:0.5b` (~400 MB) with the Ollama sidecar, or point
  `base_url` at a remote endpoint.
- **Remote endpoint:** Set `base_url` to any OpenAI-compatible URL
  (vLLM, Together, OpenRouter, etc.) and `api_key` to the provider's key.
  No Ollama container is started.
- **Disable MCP:** Set `mcp.enabled: false` if you only want bot chat.
- **Class permissions:** `min_class: 0` lets guests use LLM chat;
  `min_class: 3` restricts it to operators and above.

## Troubleshooting

### View Logs

```bash
# Production logs
./run_production.sh --logs

# Development logs
sg docker -c "docker compose logs -f"

# Specific container
docker logs vh-prod-hub -f
docker logs vh-prod-mysql -f
```

### Check Status

```bash
./run_production.sh --status
```

### Common Issues

#### Hub not starting

1. Check MySQL is running: `docker ps | grep mysql`
2. Check logs: `./run_production.sh --logs`
3. Verify database credentials in config

#### TLS not working

1. Verify Verlihub was built with TLS support
2. Check certificate file paths
3. Test with: `openssl s_client -connect hostname:port`

#### Users not registering

1. Check MySQL connectivity
2. Verify user config syntax in YAML
3. Run with `--debug` for verbose output

#### API not accessible

1. Ensure `python_mode: single`
2. Check `!api start` is in startup_commands
3. Verify `!onplug python` runs first
4. Check firewall/port mapping

### Reset Everything

```bash
# Stop and remove containers
./run_production.sh --stop

# Remove volumes (WARNING: deletes all data)
docker volume rm verlihub-production-config verlihub-production-mysql

# Rebuild from scratch
./run_production.sh --rebuild
```

### Database Access

```bash
# Connect to MySQL
docker exec -it vh-prod-mysql mysql -uverlihub -pverlihub verlihub

# Check users
SELECT nick, class FROM reglist;

# Check settings
SELECT * FROM SetupList WHERE file='config';
```

## Example Production Configuration

Complete example for a production hub:

```yaml
database:
  host: mysql
  user: verlihub
  password: "super_secure_password_change_me"
  name: verlihub

hub:
  name: "My Awesome DC++ Hub"
  description: "The best hub on the network!"
  host: "nmdcs://hub.example.com:411"
  owner: "HubOwner"
  topic: "https://example.com"
  category: "General"
  icon_url: "https://example.com/favicon.ico"
  port: 4111
  motd_file: "motd.txt"

bots:
  security:
    nick: "Security"
    description: "Hub security system"
  opchat:
    nick: "OpChat"
    min_class: 3

limits:
  max_users: 5000
  max_users_per_ip: 3

share:
  min_share: 1073741824         # 1 GB minimum
  
nick:
  min_length: 2
  max_length: 32

chat:
  max_message_length: 512
  min_class: 0

search:
  min_chars: 3
  interval:
    guest: 30
    registered: 15
    vip: 10
    operator: 1

security:
  min_password_length: 8
  clone_detection:
    count: 3
    ban_time: 3600
  hide_kicks: true

registration:
  min_class_to_register: 5
  request_password: true

hublist:
  server_enabled: true
  registration_interval: 600
  stale_timeout: 1800

permissions:
  oplist_class: 3
  broadcast_class: 4

tls:
  enabled: true
  only_mode: false
  min_version: 2
  cert_file: "/certs/hub.crt"
  key_file: "/certs/hub.key"

users:
  masters:
    - nick: HubOwner
      password: "very_secure_master_password"
  admins:
    - nick: Moderator1
      password: "admin_password_1"
    - nick: Moderator2  
      password: "admin_password_2"
  operators:
    - nick: Helper1
      password: "op_password"

python_mode: single

api:
  enabled: true
  port: 30000
  cors_origins:
    - "https://hub.example.com"

matterbridge:
  enabled: true
  api_url: "http://matterbridge:4242"
  gateway: "verlihub"
  channel: "#hub-chat"

startup_commands:
  - "!onplug python"
  - "!api start 30000"

docker:
  config_volume: myhub-config
  mysql_volume: myhub-mysql
  container_prefix: myhub
  restart_policy: unless-stopped
```

## Security Recommendations

1. **Change default passwords** - Never use example passwords in production
2. **Enable TLS** - Use encrypted connections when possible
3. **Use TLS 1.2+** - Set `min_version: 2` or higher
4. **Limit CORS origins** - Only allow specific domains
5. **Use proper certificates** - Consider Let's Encrypt for production
6. **Regular backups** - Back up MySQL volume regularly
7. **Keep updated** - Pull latest images and rebuild periodically
