#!/usr/bin/env python3
"""
Verlihub CLI - Command-line interface for managing Verlihub.

Usage:
    verlihub-cli [OPTIONS] COMMAND [ARGS]
    
Commands:
    status      Show hub status
    users       List online users
    kick        Kick a user
    ban         Ban a user
    broadcast   Send a broadcast message
    command     Execute a hub command
    login       Login and get authentication token

Examples:
    verlihub-cli status
    verlihub-cli users --format table
    verlihub-cli kick baduser --reason "Spamming"
    verlihub-cli broadcast "Hub maintenance in 5 minutes"
    verlihub-cli command "!help"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import httpx
except ImportError:
    print("Error: httpx is required. Install with: pip install httpx", file=sys.stderr)
    sys.exit(1)


# Configuration
DEFAULT_API_URL = os.getenv("VH_API_URL", "http://localhost:8000")
CONFIG_FILE = Path.home() / ".verlihub-cli.json"


def load_config() -> dict:
    """Load CLI configuration."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(config: dict) -> None:
    """Save CLI configuration with secure file permissions."""
    import stat
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    # Set file permissions to owner-only (600)
    CONFIG_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)


def get_client(args: argparse.Namespace) -> httpx.Client:
    """Create HTTP client with authentication."""
    config = load_config()
    token = args.token or config.get("token")
    
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    return httpx.Client(
        base_url=args.api_url or config.get("api_url") or DEFAULT_API_URL,
        headers=headers,
        timeout=30.0,
    )


def format_bytes(size: int) -> str:
    """Format bytes to human-readable."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def format_uptime(seconds: int) -> str:
    """Format seconds to uptime string."""
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    
    return " ".join(parts)


# Commands

def cmd_login(args: argparse.Namespace) -> int:
    """Login and save authentication token."""
    with get_client(args) as client:
        try:
            response = client.post("/api/v1/auth/login", json={
                "username": args.username,
                "password": args.password,
            })
            
            if response.status_code == 200:
                data = response.json()
                token = data.get("access_token")
                
                # Save to config
                config = load_config()
                config["token"] = token
                config["api_url"] = args.api_url or config.get("api_url") or DEFAULT_API_URL
                save_config(config)
                
                print(f"✓ Logged in as {args.username}")
                print(f"  Token saved to {CONFIG_FILE}")
                return 0
            else:
                print(f"✗ Login failed: {response.text}", file=sys.stderr)
                return 1
        except httpx.RequestError as e:
            print(f"✗ Connection error: {e}", file=sys.stderr)
            return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show hub status."""
    with get_client(args) as client:
        try:
            response = client.get("/api/v1/hub/stats")
            
            if response.status_code == 200:
                data = response.json()
                
                if args.format == "json":
                    print(json.dumps(data, indent=2))
                else:
                    print("Hub Status")
                    print("=" * 40)
                    print(f"  Status:     {'Online' if data.get('running') else 'Offline'}")
                    print(f"  Hub Name:   {data.get('hub_name', 'N/A')}")
                    print(f"  Users:      {data.get('user_count', 0)}")
                    print(f"  Share:      {format_bytes(data.get('share_total', 0))}")
                    if data.get('uptime'):
                        print(f"  Uptime:     {format_uptime(data['uptime'])}")
                return 0
            elif response.status_code == 401:
                print("✗ Authentication required. Run: verlihub-cli login", file=sys.stderr)
                return 1
            else:
                print(f"✗ Error: {response.text}", file=sys.stderr)
                return 1
        except httpx.RequestError as e:
            print(f"✗ Connection error: {e}", file=sys.stderr)
            return 1


def cmd_users(args: argparse.Namespace) -> int:
    """List online users."""
    with get_client(args) as client:
        try:
            response = client.get("/api/v1/users/online")
            
            if response.status_code == 200:
                users = response.json()
                
                if args.format == "json":
                    print(json.dumps(users, indent=2))
                else:
                    if not users:
                        print("No users online")
                        return 0
                    
                    print(f"{'Nick':<20} {'Class':<8} {'Share':<12} {'IP':<15}")
                    print("-" * 60)
                    for user in users:
                        print(f"{user.get('nick', 'N/A'):<20} "
                              f"{user.get('class', 0):<8} "
                              f"{format_bytes(user.get('share', 0)):<12} "
                              f"{user.get('ip', 'N/A'):<15}")
                    print(f"\nTotal: {len(users)} users")
                return 0
            elif response.status_code == 401:
                print("✗ Authentication required. Run: verlihub-cli login", file=sys.stderr)
                return 1
            else:
                print(f"✗ Error: {response.text}", file=sys.stderr)
                return 1
        except httpx.RequestError as e:
            print(f"✗ Connection error: {e}", file=sys.stderr)
            return 1


def cmd_kick(args: argparse.Namespace) -> int:
    """Kick a user."""
    with get_client(args) as client:
        try:
            response = client.post(f"/api/v1/users/{args.nick}/kick", json={
                "reason": args.reason or "",
            })
            
            if response.status_code == 200:
                print(f"✓ Kicked user: {args.nick}")
                if args.reason:
                    print(f"  Reason: {args.reason}")
                return 0
            elif response.status_code == 404:
                print(f"✗ User not found: {args.nick}", file=sys.stderr)
                return 1
            elif response.status_code == 401:
                print("✗ Authentication required. Run: verlihub-cli login", file=sys.stderr)
                return 1
            else:
                print(f"✗ Error: {response.text}", file=sys.stderr)
                return 1
        except httpx.RequestError as e:
            print(f"✗ Connection error: {e}", file=sys.stderr)
            return 1


def cmd_ban(args: argparse.Namespace) -> int:
    """Ban a user."""
    with get_client(args) as client:
        try:
            response = client.post("/api/v1/bans/", json={
                "nick": args.nick,
                "ip": args.ip or "",
                "reason": args.reason or "",
                "duration": args.duration or "1d",
            })
            
            if response.status_code in (200, 201):
                print(f"✓ Banned: {args.nick or args.ip}")
                if args.reason:
                    print(f"  Reason: {args.reason}")
                if args.duration:
                    print(f"  Duration: {args.duration}")
                return 0
            elif response.status_code == 401:
                print("✗ Authentication required. Run: verlihub-cli login", file=sys.stderr)
                return 1
            else:
                print(f"✗ Error: {response.text}", file=sys.stderr)
                return 1
        except httpx.RequestError as e:
            print(f"✗ Connection error: {e}", file=sys.stderr)
            return 1


def cmd_broadcast(args: argparse.Namespace) -> int:
    """Send a broadcast message."""
    with get_client(args) as client:
        try:
            response = client.post("/api/v1/hub/broadcast", json={
                "message": args.message,
            })
            
            if response.status_code == 200:
                print(f"✓ Broadcast sent: {args.message}")
                return 0
            elif response.status_code == 401:
                print("✗ Authentication required. Run: verlihub-cli login", file=sys.stderr)
                return 1
            else:
                print(f"✗ Error: {response.text}", file=sys.stderr)
                return 1
        except httpx.RequestError as e:
            print(f"✗ Connection error: {e}", file=sys.stderr)
            return 1


def cmd_command(args: argparse.Namespace) -> int:
    """Execute a hub command."""
    with get_client(args) as client:
        try:
            response = client.post("/api/v1/console/execute", json={
                "command": args.hub_command,
            })
            
            if response.status_code == 200:
                data = response.json()
                if data.get("output"):
                    print(data["output"])
                if data.get("message") and args.verbose:
                    print(f"\n✓ {data['message']}")
                return 0 if data.get("success") else 1
            elif response.status_code == 401:
                print("✗ Authentication required. Run: verlihub-cli login", file=sys.stderr)
                return 1
            else:
                print(f"✗ Error: {response.text}", file=sys.stderr)
                return 1
        except httpx.RequestError as e:
            print(f"✗ Connection error: {e}", file=sys.stderr)
            return 1


def cmd_config(args: argparse.Namespace) -> int:
    """Show/set CLI configuration."""
    config = load_config()
    
    if args.show:
        print(json.dumps(config, indent=2))
        return 0
    
    if args.set_url:
        config["api_url"] = args.set_url
        save_config(config)
        print(f"✓ API URL set to: {args.set_url}")
    
    if args.clear:
        CONFIG_FILE.unlink(missing_ok=True)
        print("✓ Configuration cleared")
    
    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Verlihub command-line interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    
    # Global options
    parser.add_argument("--api-url", "-u", help="API base URL")
    parser.add_argument("--token", "-t", help="Authentication token")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--format", "-f", choices=["text", "json"], default="text",
                        help="Output format")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # login command
    login_parser = subparsers.add_parser("login", help="Login to the hub API")
    login_parser.add_argument("username", help="Username")
    login_parser.add_argument("password", help="Password")
    
    # status command
    subparsers.add_parser("status", help="Show hub status")
    
    # users command
    subparsers.add_parser("users", help="List online users")
    
    # kick command
    kick_parser = subparsers.add_parser("kick", help="Kick a user")
    kick_parser.add_argument("nick", help="Nick to kick")
    kick_parser.add_argument("--reason", "-r", help="Kick reason")
    
    # ban command
    ban_parser = subparsers.add_parser("ban", help="Ban a user")
    ban_parser.add_argument("--nick", "-n", help="Nick to ban")
    ban_parser.add_argument("--ip", "-i", help="IP to ban")
    ban_parser.add_argument("--reason", "-r", help="Ban reason")
    ban_parser.add_argument("--duration", "-d", default="1d", help="Ban duration (e.g., 1h, 1d, 1w)")
    
    # broadcast command
    broadcast_parser = subparsers.add_parser("broadcast", help="Send broadcast message")
    broadcast_parser.add_argument("message", help="Message to broadcast")
    
    # command (execute raw command)
    command_parser = subparsers.add_parser("command", aliases=["cmd", "exec"],
                                           help="Execute a hub command")
    command_parser.add_argument("hub_command", help="Command to execute")
    
    # config command
    config_parser = subparsers.add_parser("config", help="CLI configuration")
    config_parser.add_argument("--show", action="store_true", help="Show current config")
    config_parser.add_argument("--set-url", help="Set API URL")
    config_parser.add_argument("--clear", action="store_true", help="Clear saved config")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 0
    
    # Dispatch commands
    commands = {
        "login": cmd_login,
        "status": cmd_status,
        "users": cmd_users,
        "kick": cmd_kick,
        "ban": cmd_ban,
        "broadcast": cmd_broadcast,
        "command": cmd_command,
        "cmd": cmd_command,
        "exec": cmd_command,
        "config": cmd_config,
    }
    
    cmd_func = commands.get(args.command)
    if cmd_func:
        return cmd_func(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
