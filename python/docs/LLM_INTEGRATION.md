# LLM Chat & MCP Integration

## Overview

Verlihub includes optional LLM (Large Language Model) integration that provides:

1. **AI Chat Assistant** — A natural-language chatbot in the web dashboard that can query and manage the hub, available to admins and optionally regular users
2. **MCP Server** — A [Model Context Protocol](https://modelcontextprotocol.io/) server that exposes the hub to LLM-powered tools in IDEs like VS Code and Claude Desktop

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
│         │  MCP protocol (stdio)                                 │
│         ▼                                                       │
│  verlihub.client.mcp  (standalone process)                      │
│         │  REST API calls via verlihub.client.api                │
│         ▼                                                       │
│  Verlihub REST API  /api/v1/*                                   │
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

## MCP Server

The MCP server (`verlihub.client.mcp`) exposes the hub as context for AI
coding assistants via the Model Context Protocol. It runs as a separate
process and connects to the hub through the REST API using
`verlihub.client.api.AsyncHubClient`.

Two transport modes are supported:

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
      "args": [
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
      "args": ["-m", "verlihub.client.mcp"],
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
      "args": [
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
verlihub-mcp --transport http \
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
