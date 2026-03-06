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

Add to your `config.yml`:

```yaml
# =============================================================================
# LLM Integration (AI Chat Assistant)
# =============================================================================
llm:
  # Enable the AI chat feature in the dashboard
  enabled: false

  # OpenAI-compatible API endpoint
  # Works with: Ollama, vLLM, llama.cpp, LiteLLM, OpenRouter, any OpenAI API
  base_url: "http://localhost:11434/v1"   # Ollama default

  # Model name (must support tool/function calling)
  model: "llama3.1"

  # API key (use "ollama" for local Ollama, real key for hosted services)
  api_key: "ollama"

  # Maximum tool-call round-trips per user message (prevents runaway loops)
  max_tool_rounds: 5

  # Temperature for LLM responses (lower = more deterministic)
  temperature: 0.3

  # Maximum tokens in LLM response
  max_tokens: 2048

  # Minimum user class to access AI chat
  # -1 = public (no login), 0 = any logged-in user, 3 = operators, 5 = admins
  min_class: 3

  # Minimum user class for admin-level AI tools (kick, ban, config, console)
  # Users below this class get read-only tools only
  admin_class: 5

# =============================================================================
# MCP Server
# =============================================================================
mcp:
  # Enable the MCP server entry point
  enabled: false
```

### Environment Variables

All LLM settings can also be set via environment variables:

| Variable | Config Key | Default |
|----------|-----------|---------|
| `VH_LLM_ENABLED` | `llm.enabled` | `false` |
| `VH_LLM_BASE_URL` | `llm.base_url` | `http://localhost:11434/v1` |
| `VH_LLM_MODEL` | `llm.model` | `llama3.1` |
| `VH_LLM_API_KEY` | `llm.api_key` | `ollama` |
| `VH_LLM_MAX_TOOL_ROUNDS` | `llm.max_tool_rounds` | `5` |
| `VH_LLM_MIN_CLASS` | `llm.min_class` | `3` |
| `VH_LLM_ADMIN_CLASS` | `llm.admin_class` | `5` |
| `VH_MCP_ENABLED` | `mcp.enabled` | `false` |

## Setup Guide

### 1. Install an LLM Backend

**Ollama (recommended for self-hosting):**
```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model with tool-calling support
ollama pull llama3.1

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
  model: "llama3.1"
  api_key: "ollama"
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

The MCP server exposes the hub as context for LLM tools in IDEs.

### VS Code Integration

Create `.vscode/mcp.json`:

```json
{
  "servers": {
    "verlihub": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "verlihub.client.mcp"],
      "env": {
        "VERLIHUB_API_URL": "http://localhost:8000/api/v1",
        "VERLIHUB_API_USER": "admin",
        "VERLIHUB_API_PASSWORD": "your_password"
      }
    }
  }
}
```

### Claude Desktop Integration

Add to `~/.config/claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "verlihub": {
      "command": "python",
      "args": ["-m", "verlihub.client.mcp"],
      "env": {
        "VERLIHUB_API_URL": "http://localhost:8000/api/v1",
        "VERLIHUB_API_USER": "admin",
        "VERLIHUB_API_PASSWORD": "your_password"
      }
    }
  }
}
```

### MCP Resources

| URI | Description |
|-----|-------------|
| `verlihub://hub/info` | Hub name, topic, version, user count, share |
| `verlihub://hub/status` | Full hub status with uptime |
| `verlihub://hub/users` | Connected user list |
| `verlihub://hub/stats` | Comprehensive statistics |
| `verlihub://hub/health` | Health check |

### MCP Tools

| Tool | Description | Permission |
|------|-------------|-----------|
| `get_hub_info` | Hub metadata | Any |
| `get_hub_status` | Hub status | Any |
| `list_users` | Online user list | Any |
| `get_user_info` | User details | Any |
| `list_operators` | Online operators | Any |
| `list_bots` | Hub bots | Any |
| `get_geo_distribution` | User geography | Any |
| `get_share_stats` | Share statistics | Any |
| `get_health` | Health check | Any |
| `kick_user` | Kick a user | Admin |
| `ban_user` | Ban a user | Admin |
| `send_broadcast` | Broadcast message | Admin |
| `execute_command` | Hub console command | Admin |
| `get_config` | Read hub config | Admin |
| `set_config` | Write hub config | Master |

### MCP Prompts

| Prompt | Description |
|--------|-------------|
| `hub_status_report` | Generate a comprehensive hub status report |
| `investigate_user` | Deep-dive on a specific user |
| `security_audit` | Review bans, suspicious users, share leeches |
