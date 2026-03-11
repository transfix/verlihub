# LLM Chat & MCP Integration

## Overview

Verlihub includes optional LLM (Large Language Model) integration that provides:

1. **AI Chat Assistant** — A natural-language chatbot in the web dashboard that can query and manage the hub, available to admins and optionally regular users
2. **NMDC Bot Chat** — Hub users can chat with the `Hub-Security` bot via private messages or main chat, powered by the same LLM
3. **MCP Server** — A [Model Context Protocol](https://modelcontextprotocol.io/) server that exposes the hub to LLM-powered tools in IDEs like VS Code and Claude Desktop

Both features connect to any **OpenAI-compatible LLM backend** (Ollama, vLLM, llama.cpp, LiteLLM, OpenRouter, or any hosted API).

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Dashboard Browser                                              │
│  ┌──────────────────────────┐                                   │
│  │  AI Chat tab             │──── WebSocket /ws/llm-chat ──┐   │
│  └──────────────────────────┘                               │   │
└─────────────────────────────────────────────────────────────│───┘
                                                              │
┌─────────────────────────────────────────────────────────────│───┐
│  Verlihub Python Server                                     │   │
│                                                             ▼   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  verlihub.api.routes.llm  (FastAPI endpoints)            │   │
│  │  - /api/v1/llm/chat      POST (REST)                    │   │
│  │  - /api/v1/llm/status    GET                             │   │
│  │  - /ws/llm-chat          WebSocket (streaming)           │   │
│  │                                                          │   │
│  │  Tool orchestration loop:                                │   │
│  │  1. User message → LLM with tool definitions             │   │
│  │  2. LLM returns tool_calls → execute against hub         │   │
│  │  3. Feed results back → LLM produces final answer        │   │
│  └────────────────────────────┬─────────────────────────────┘   │
│                               │ direct Python calls             │
│  ┌────────────────────────────▼─────────────────────────────┐   │
│  │  Hub Context (verlihub.core.HubContext)                   │   │
│  │  + verlihub.api.routes.{users,bans,stats,hub,console}    │   │
│  │  Full hub operations: user lists, kicks, bans, config,   │   │
│  │  geo stats, share stats, console commands, etc.           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                               │                                 │
│               ┌───────────────▼──────────────────┐              │
│               │  OpenAI-compatible LLM API       │              │
│               │  (Ollama / vLLM / llama.cpp /    │              │
│               │   LiteLLM / OpenRouter / etc.)   │              │
│               └──────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  MCP Client (VS Code, Claude Desktop, etc.)                     │
│         │  MCP protocol (stdio or HTTP)                         │
│         ▼                                                       │
│  verlihub.client.mcp  (standalone process, uses REST client)    │
│         │  REST API calls via verlihub.client.api                │
│         ▼                                                       │
│  Verlihub REST API  /api/v1/*                                   │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  MCP Client (VS Code, Claude Desktop, etc.)                     │
│         │  MCP Streamable HTTP (POST /api/v1/mcp)               │
│         ▼                                                       │
│  In-process MCP endpoint (verlihub.api.routes.mcp)              │
│         │  JWT auth (same tokens as REST API)                   │
│         │  Direct hub context (no REST round-trip)              │
│         ▼                                                       │
│  Verlihub Hub Context (live hub)                                │
└─────────────────────────────────────────────────────────────────┘
```

## Configuration

Add to your `config.yml` (see also `config.example.yml`):

```yaml
# =============================================================================
# LLM Integration (AI Chat Assistant)
# =============================================================================
llm:
  enabled: false

  # OpenAI-compatible API endpoint
  base_url: "http://localhost:11434/v1"   # Ollama default
  model: "qwen2.5:7b"                    # Must support tool/function calling
  api_key: ""                             # Leave empty for most local servers

  # LLM parameters
  temperature: 0.3
  max_tokens: 2048
  max_tool_rounds: 8

  # Permission: minimum DC++ user class to access AI chat
  min_class: 3      # 3 = Operator
  # Permission: minimum class for admin/write tools
  admin_class: 5    # 5 = Admin
```

### Environment Variables

All LLM settings can also be set via environment variables:

| Variable | Config Key | Default |
|----------|-----------|---------|
| `VH_LLM_ENABLED` | `llm.enabled` | `false` |
| `VH_LLM_BASE_URL` | `llm.base_url` | `http://localhost:11434/v1` |
| `VH_LLM_MODEL` | `llm.model` | `qwen2.5:7b` |
| `VH_LLM_API_KEY` | `llm.api_key` | (empty) |
| `VH_LLM_MAX_TOOL_ROUNDS` | `llm.max_tool_rounds` | `8` |
| `VH_LLM_TEMPERATURE` | `llm.temperature` | `0.3` |
| `VH_LLM_MAX_TOKENS` | `llm.max_tokens` | `2048` |
| `VH_LLM_MIN_CLASS` | `llm.min_class` | `3` |
| `VH_LLM_ADMIN_CLASS` | `llm.admin_class` | `5` |

For the MCP server (separate process):

| Variable | CLI Flag | Default |
|----------|---------|---------|
| `VERLIHUB_HUB_URL` | `--hub-url` | `http://localhost:4112/api/v1` |
| `VERLIHUB_USERNAME` | `--username` | (required) |
| `VERLIHUB_PASSWORD` | `--password` | (required) |

## Setup Guide

### 1. Install an LLM Backend

**Ollama (recommended for self-hosting):**
```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model with tool-calling support
ollama pull qwen2.5:7b

# Ollama serves on http://localhost:11434 by default
```

**vLLM:**
```bash
pip install vllm
vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8080
# Set base_url: "http://localhost:8080/v1"
```

**llama.cpp:**
```bash
./llama-server -m model.gguf --port 8080
# Set base_url: "http://localhost:8080/v1"
```

**OpenRouter (hosted, no GPU needed):**
```yaml
llm:
  base_url: "https://openrouter.ai/api/v1"
  api_key: "sk-or-..."  # Your OpenRouter key
  model: "meta-llama/llama-3.1-8b-instruct"
```

### 2. Enable in Config

```yaml
llm:
  enabled: true
  base_url: "http://localhost:11434/v1"
  model: "qwen2.5:7b"
  min_class: 0      # Allow all logged-in users
  admin_class: 5    # Only admins get write tools
```

### 3. Access the AI Chat

Navigate to **Dashboard → AI Chat** in the web interface. The AI assistant can:

- **Read-only tools** (available to all permitted users):
  - Query online users, operators, bots
  - View hub statistics, geographic distribution, share stats
  - Check hub health and status
  - Look up individual user details

- **Admin tools** (available to users with class ≥ `admin_class`):
  - Kick users
  - Manage bans
  - Execute hub console commands
  - Modify hub configuration
  - Send broadcast messages

### Permission Model

The AI chat enforces the same permission model as the REST API:

| User Class | Access Level | Available Tools |
|-----------|-------------|-----------------|
| < `min_class` | No access | — |
| ≥ `min_class`, < `admin_class` | Read-only | Hub info, user lists, stats, geo |
| ≥ `admin_class` | Full admin | All above + kick, ban, config, console, broadcast |

The LLM's system prompt is adjusted per user to prevent information leakage (e.g., regular users don't see IP addresses in responses).

## NMDC Bot Chat (Hub-Security Bot)

When LLM is enabled and the hub runs in `both` mode (NMDC + API), users can
chat with the **Hub-Security** bot directly from their DC++ client — no web
browser needed.

### How It Works

Two interaction modes are supported:

| Mode | Trigger | Security Level |
|------|---------|---------------|
| **Private Message** | Send a PM to `Hub-Security` | Based on sender's user class |
| **Main Chat** | `Hub-Security: your question` | Lowest (no tools, conversational only) |

#### PM Security Levels

| User Class | Tools Available | System Prompt |
|-----------|----------------|---------------|
| ≥ `admin_class` (default 5) | All tools (kick, config, console, etc.) | Admin |
| ≥ `min_class` (default 3) | Read-only (user lists, stats, geo) | Operator |
| < `min_class` | None | Conversational only |

All users — including guests — can PM the bot and get a conversational
response. Users below `min_class` simply don't get access to hub tools.

#### Main Chat

Addressing `Hub-Security` in main chat (e.g. `Hub-Security: what is this hub about?`)
triggers a response at the **lowest** security level — no tools, no hub data
access. This is safe for public chat because the bot cannot leak any internal
information.

### Architecture

```
DC++ Client                        Verlihub
┌──────────┐                       ┌───────────────────────────────────┐
│ User PMs │── $To: Hub-Security ──│ C++ OnPrivateMessage callback    │
│ bot      │                       │         │                        │
└──────────┘                       │         ▼                        │
                                   │ Python BotChatHandler._on_pm()  │
                                   │         │                        │
                                   │         ▼ asyncio                │
                                   │ BotChatSession.chat(msg)        │
                                   │         │                        │
                                   │    ┌────▼────┐                   │
                                   │    │  Ollama  │ (tool calls)     │
                                   │    └────┬────┘                   │
                                   │         │                        │
                                   │         ▼                        │
                                   │ ctx.send_pm_as(bot, user, resp) │
                                   │         │                        │
                                   │         ▼                        │
                                   │ $To: User From: Hub-Security    │
                                   └───────────────────────────────────┘
```

### Configuration

No extra configuration is needed — the bot chat feature activates
automatically when `llm.enabled: true` and the hub runs in `both` mode.

The bot nickname is controlled by the standard `bots.security.nick` setting:

```yaml
bots:
  security:
    nick: "Hub-Security"
    description: "Hub security bot (LLM-powered)"

llm:
  enabled: true
  base_url: "http://localhost:11434/v1"
  model: "qwen2.5:7b"
  min_class: 3
  admin_class: 5
```

### Session Memory

Each user gets a persistent conversation session per mode (PM and main-chat
are separate). The session retains message history across multiple turns,
so users can have multi-turn conversations with context.

Sessions are held in memory and reset when the hub restarts.

### Dynamic Mood Engine

When `mood_enabled: true`, the bot's personality shifts dynamically based
on real-time hub activity. Two signals are tracked:

1. **Interaction rate** — messages the bot handles per hour (sliding window).
2. **User-count ratio** — current online users vs. a 24-hour rolling average.

These two axes combine into a 3×3 mood matrix:

|                   | Low Interaction | Normal | High Interaction |
|-------------------|-----------------|--------|------------------|
| **Low Users**     | lonely 😔       | melancholic 🌧️ | wistful 🥹 |
| **Normal Users**  | bored 😐        | neutral 🙂 | cheerful 😊 |
| **High Users**    | curious 🤔      | happy 😄 | ecstatic 🤩 |

Each mood injects a short personality modifier into the system prompt, so
the LLM naturally adopts the emotional tone without explicit instructions.
The **neutral** mood adds nothing — it's the default personality.

**Mood is global.** A single `BotMoodEngine` instance lives on
`BotChatHandler` and is shared by all sessions (PM and main-chat). When
the mood changes, *every* user's next message sees the updated prompt.

#### Configuration

All mood thresholds are configurable in `production.yml`:

```yaml
bots:
  behavior:
    mood_enabled: true
    mood_window: 3600            # interaction tracking window (seconds)
    mood_low_interaction: 2.0    # msgs/hr below this → low activity
    mood_high_interaction: 10.0  # msgs/hr above this → high activity
    mood_low_user_ratio: 0.5     # current/avg below this → few users
    mood_high_user_ratio: 1.5    # current/avg above this → many users
    mood_user_history: 86400     # rolling average window (seconds, 24h)
```

| Setting | Default | Description |
|---------|---------|-------------|
| `mood_enabled` | `false` | Enable/disable the mood engine |
| `mood_window` | `3600` | Sliding window (seconds) for counting interactions |
| `mood_low_interaction` | `2.0` | Messages/hour below this = "low activity" |
| `mood_high_interaction` | `10.0` | Messages/hour above this = "high activity" |
| `mood_low_user_ratio` | `0.5` | Current/average user ratio below this = "few users" |
| `mood_high_user_ratio` | `1.5` | Current/average user ratio above this = "many users" |
| `mood_user_history` | `86400` | How far back (seconds) to keep user-count samples |

### Web Access (Search, Fetch, RSS)

When `web_enabled: true`, the bot gains three LLM tool calls:

| Tool | Description |
|------|-------------|
| `web_search(query)` | Search via DuckDuckGo (instant answers + HTML lite scraping). No API key needed. |
| `fetch_webpage(url)` | Fetch a URL and extract readable plain text (HTML stripped). |
| `read_rss(url)` | Parse an RSS 2.0 or Atom feed and return recent headlines with summaries. |

The LLM decides when to use these tools based on the conversation. For
example, if a user asks "what's in the news?", the bot will call
`read_rss` on its configured feeds.

#### RSS Feeds

Configure RSS/Atom feed URLs and the bot can proactively check them:

```yaml
bots:
  behavior:
    web_enabled: true
    rss_feeds:
      - "https://torrentfreak.com/feed/"
      - "https://www.phoronix.com/rss.php"
```

When the bot generates a proactive message (see `proactive_interval`),
it may choose to pull a headline from one of these feeds and share it
in main chat. Users can also ask the bot to check feeds explicitly.

### Persistent Memory (Notes)

When `memory_enabled: true`, the bot can save and recall notes across
restarts using four LLM tool calls:

| Tool | Description |
|------|-------------|
| `save_note(topic, content)` | Create or update a note. The current mood is recorded automatically. |
| `recall_notes(query)` | Search notes by keyword (case-insensitive). Returns content, timestamps, and mood tags. |
| `list_notes()` | List all saved topics with relative timestamps and mood tags. |
| `delete_note(topic)` | Remove a note by topic. |

#### Database Storage

Notes are stored in the **same database** the hub uses (MySQL, PostgreSQL,
or SQLite — whichever is configured in `database:`). The `bot_notes` table
is created automatically alongside all other hub tables. There is **no
separate SQLite file** to manage.

The `BotNote` model has these fields:

| Column | Type | Description |
|--------|------|-------------|
| `id` | int | Auto-increment primary key |
| `topic` | varchar(255) | Note topic/title (indexed) |
| `content` | varchar(4096) | Note body |
| `mood` | varchar(64) | Bot's mood when the note was saved (e.g. "cheerful") |
| `created_at` | datetime (UTC) | When the note was first created |
| `updated_at` | datetime (UTC) | When the note was last updated |

#### How Memory Affects Prompts

At the start of every conversation turn, the system prompt is refreshed
with a compact summary of the bot's most recent notes (up to 10). This
gives the LLM awareness of what it has stored without overwhelming the
context window. The summary includes:

- Note topic and a truncated snippet of content
- Relative timestamp ("3h ago", "2d ago")
- Mood tag when the note was saved

The LLM can then use `recall_notes` to look up full details if needed.

#### Mood Association

Every note records the bot's mood at save time. When recalling or listing
notes, the mood tag is included — so the bot (and the LLM) can see how
it was feeling when it saved that information. This creates an emotional
history that makes the bot feel more self-aware.

### Time Awareness

The bot is aware of the current time. Every system prompt includes:

```
Current date and time (UTC): 2025-01-15 14:30 UTC
```

This is refreshed at the start of every conversation turn. Combined with
the relative timestamps on notes ("saved 3h ago"), the bot has a genuine
sense of time passing — it knows what day it is, can reason about
recency, and understands how long ago it saved particular information.

### Running Integration Tests

The bot chat integration tests require Docker with Ollama:

```bash
# Run bot chat tests
./docker_test_launcher.sh bot-chat

# Or directly via Docker Compose
docker compose -f docker/docker-compose.bot-chat-test.yml up \
    --build --abort-on-container-exit bot-tests

# Clean up
docker compose -f docker/docker-compose.bot-chat-test.yml down -v
```

The tests use `verlihub.client.nmdc.NMDCClient` to simulate users at
different permission levels (admin, operator, registered) and verify:

- PM to bot → LLM response received
- Different user classes get appropriate responses
- Main chat mention → bot responds in main chat
- Multi-turn conversation retains context
- Non-bot PMs are not intercepted

## MCP Server & Client

The `verlihub-mcp` CLI (`verlihub.client.mcp`) provides both a **server**
and a **client** for the Model Context Protocol. The server exposes the hub
as context for AI coding assistants; the client connects to a running MCP
server and lets you interact from the terminal.

```
verlihub-mcp serve   — start the MCP server (stdio or HTTP)
verlihub-mcp client  — query a running MCP server over HTTP
```

Two transport modes are supported for the server:

| Transport | Flag | Use Case |
|-----------|------|----------|
| **stdio** (default) | `--transport stdio` | AI editors (VS Code, Cursor, Claude Desktop) |
| **HTTP** | `--transport http` | Remote clients, web dashboards, multi-user setups |

### Installation

```bash
pip install 'verlihub[mcp]'
# or for everything:
pip install 'verlihub[ai]'
```

### Stdio Mode (AI Editors)

#### VS Code Integration

Create `.vscode/mcp.json`:

```json
{
  "servers": {
    "verlihub": {
      "type": "stdio",
      "command": "verlihub-mcp",
      "args": ["serve",
        "--hub-url", "http://localhost:4112/api/v1",
        "--username", "admin",
        "--password", "your_password"
      ]
    }
  }
}
```

Or using environment variables:

```json
{
  "servers": {
    "verlihub": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "verlihub.client.mcp", "serve"],
      "env": {
        "VERLIHUB_HUB_URL": "http://localhost:4112/api/v1",
        "VERLIHUB_USERNAME": "admin",
        "VERLIHUB_PASSWORD": "your_password"
      }
    }
  }
}
```

#### Claude Desktop Integration

Add to `~/.config/claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "verlihub": {
      "command": "verlihub-mcp",
      "args": ["serve",
        "--hub-url", "http://localhost:4112/api/v1",
        "--username", "admin",
        "--password", "your_password"
      ]
    }
  }
}
```

### HTTP Mode (Remote / Web Clients)

Start the MCP server over Streamable HTTP:

```bash
verlihub-mcp serve --transport http \
    --hub-url http://localhost:4112/api/v1 \
    --username admin --password secret \
    --host 0.0.0.0 --port 8080
```

The server listens on `http://<host>:<port>/mcp` using the
[Streamable HTTP](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http)
transport from the MCP specification.

#### HTTP-specific flags

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `8080` | Bind port |
| `--json-response` | off | Reply with JSON instead of SSE streams |

#### VS Code (HTTP)

```json
{
  "servers": {
    "verlihub": {
      "type": "http",
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

#### Any MCP HTTP Client

The endpoint is a standard MCP Streamable HTTP endpoint:

```
POST http://localhost:8080/mcp
GET  http://localhost:8080/mcp          (SSE stream)
DELETE http://localhost:8080/mcp        (session termination)
```

### In-Process MCP Endpoint (Recommended for HTTP)

Instead of running a separate `verlihub-mcp serve --transport http` process,
you can enable an **in-process** MCP endpoint that lives inside the main
FastAPI application at `/api/v1/mcp`.

**Advantages over the standalone HTTP server:**

- **JWT authentication** — uses the same tokens as the REST API; no separate credentials
- **Permission-gated tools** — read-only tools require `min_class`, admin tools require `admin_class`
- **No REST round-trip** — talks directly to the live hub context
- **Single process** — no extra service to manage

#### Enable in `config.yml`

```yaml
mcp:
  enabled: true
  min_class: 3     # Operator (read-only tools)
  admin_class: 5   # Admin (kick, ban, broadcast)
```

#### Obtain a JWT Token

```bash
# Login to get a token
TOKEN=$(curl -s -X POST http://localhost:4112/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=admin&password=secret' | jq -r .access_token)
```

#### VS Code / Copilot (HTTP)

Create `.vscode/mcp.json`:

```json
{
  "servers": {
    "verlihub": {
      "type": "http",
      "url": "http://localhost:4112/api/v1/mcp",
      "headers": {
        "Authorization": "Bearer ${input:verlihubToken}"
      }
    }
  },
  "inputs": [
    {
      "id": "verlihubToken",
      "type": "promptString",
      "description": "Verlihub JWT token (from /api/v1/auth/login)"
    }
  ]
}
```

#### Claude Desktop (HTTP)

Claude Desktop does not support custom headers natively. Use the
standalone stdio server (`verlihub-mcp serve`) for Claude Desktop instead.

#### curl / Any HTTP Client

```bash
# List tools
curl -X POST http://localhost:4112/api/v1/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# Call a tool
curl -X POST http://localhost:4112/api/v1/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_hub_info","arguments":{}}}'
```

#### Permission Model

| Tool Category | Minimum Class | Examples |
|--------------|---------------|----------|
| Read-only | `min_class` (3) | `get_hub_info`, `list_online_users`, `search_bans` |
| Admin / write | `admin_class` (5) | `kick_user`, `ban_user`, `send_broadcast` |

Admin-only tools are hidden from `tools/list` for users below `admin_class`.

### MCP Client CLI

Once an MCP server is running over HTTP, you can query it from the
terminal using the built-in client:

```bash
# List tools, resources, and prompts
verlihub-mcp client tools
verlihub-mcp client resources
verlihub-mcp client prompts

# Call a tool
verlihub-mcp client call get_hub_info
verlihub-mcp client call get_user_info '{"nick":"admin"}'
verlihub-mcp client call kick_user '{"nick":"spam","reason":"flooding"}'

# Read a resource
verlihub-mcp client read hub://info
verlihub-mcp client read hub://users

# Get a prompt
verlihub-mcp client prompt hub_report
verlihub-mcp client prompt user_lookup '{"nick":"admin"}'
```

By default the client connects to `http://localhost:8080/mcp`.  Use
`--url` or set `VERLIHUB_MCP_URL` to point elsewhere.

### MCP Resources

| URI | Description |
|-----|-------------|
| `hub://info` | Hub name, topic, version, user count, share |
| `hub://users` | Connected users with details |
| `hub://stats` | Comprehensive statistics |
| `hub://bans` | Active bans |

### MCP Tools

| Tool | Description |
|------|-------------|
| `get_hub_info` | Hub metadata (name, topic, version) |
| `list_online_users` | Detailed online user list |
| `get_user_info` | Look up a specific user |
| `get_hub_statistics` | Comprehensive hub statistics |
| `get_share_statistics` | File sharing stats |
| `get_geo_distribution` | User geography by country |
| `list_operators` | Online operators and admins |
| `list_bots` | Hub bots |
| `search_bans` | Search active bans |
| `get_registered_users` | Registered user list |
| `health_check` | Hub health check |
| `kick_user` | Kick a user (admin) |
| `send_broadcast` | Broadcast message (admin) |
| `send_message_to_user` | PM a user (admin) |
| `ban_user` | Ban a user (admin) |
| `register_user` | Register a new user (admin) |

### MCP Prompts

| Prompt | Description |
|--------|-------------|
| `hub_report` | Generate a comprehensive hub status report |
| `user_lookup` | Deep-dive on a specific user |
| `troubleshoot` | Diagnose potential hub issues |
