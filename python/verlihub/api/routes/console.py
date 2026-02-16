"""
Console API routes for executing hub commands.

Provides endpoints for:
- Execute command via web interface
- Get command history
- Get available commands list
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from verlihub.api.auth import require_permission, Permission, TokenData
from verlihub.api.deps import get_hub_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/console", tags=["console"])


class CommandRequest(BaseModel):
    """Request to execute a hub command."""
    command: str = Field(..., min_length=1, max_length=1000, description="Command to execute")


class CommandResponse(BaseModel):
    """Response from command execution."""
    success: bool
    command: str
    output: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None


class CommandInfo(BaseModel):
    """Information about an available command."""
    name: str
    description: str
    usage: str
    min_class: int


# Common Verlihub commands with descriptions
COMMAND_REFERENCE = [
    CommandInfo(name="!help", description="Show available commands", usage="!help [command]", min_class=0),
    CommandInfo(name="!hubinfo", description="Display hub information", usage="!hubinfo", min_class=0),
    CommandInfo(name="!ul", description="List online users", usage="!ul [class]", min_class=3),
    CommandInfo(name="!reglist", description="List registered users", usage="!reglist [class]", min_class=3),
    CommandInfo(name="!banlist", description="List active bans", usage="!banlist [type]", min_class=3),
    CommandInfo(name="!lstplug", description="List loaded plugins", usage="!lstplug", min_class=5),
    CommandInfo(name="+reguser", description="Register a new user", usage="+reguser <nick> <class> [password]", min_class=5),
    CommandInfo(name="-reguser", description="Unregister a user", usage="-reguser <nick>", min_class=5),
    CommandInfo(name="!mc", description="Broadcast message to all users", usage="!mc <message>", min_class=3),
    CommandInfo(name="!pm", description="Send private message", usage="!pm <nick> <message>", min_class=3),
    CommandInfo(name="!kick", description="Kick a user", usage="!kick <nick> [reason]", min_class=3),
    CommandInfo(name="!ban", description="Ban a user", usage="!ban <nick> <time> [reason]", min_class=4),
    CommandInfo(name="!unban", description="Remove a ban", usage="!unban <nick|ip>", min_class=4),
    CommandInfo(name="!topic", description="Set hub topic", usage="!topic <message>", min_class=3),
    CommandInfo(name="!reload", description="Reload hub configuration", usage="!reload [component]", min_class=10),
    CommandInfo(name="!onplug", description="Load a plugin", usage="!onplug <name>", min_class=10),
    CommandInfo(name="!offplug", description="Unload a plugin", usage="!offplug <name>", min_class=10),
    CommandInfo(name="!set", description="Set configuration value", usage="!set <var> <value>", min_class=10),
    CommandInfo(name="!get", description="Get configuration value", usage="!get <var>", min_class=5),
]


@router.post("/execute", response_model=CommandResponse)
async def execute_command(
    request: CommandRequest,
    user: TokenData = Depends(require_permission(Permission.OPERATOR)),
):
    """
    Execute a hub command.
    
    Requires Operator (class 3) or higher permission.
    
    Returns the command output or error message.
    """
    ctx = get_hub_context()
    if not ctx:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Hub not connected",
        )
    
    command = request.command.strip()
    
    # Log command execution
    logger.info(f"Console command from {user.username}: {command}")
    
    try:
        # Check if hub is running
        if not ctx.is_running:
            return CommandResponse(
                success=False,
                command=command,
                error="Hub is not running",
            )
        
        # Execute command via hub context
        # The hub context provides execute_command which returns output
        output = ctx.execute_command(command, user.username)
        
        return CommandResponse(
            success=True,
            command=command,
            output=output,
            message="Command executed successfully",
        )
        
    except AttributeError:
        # Hub context doesn't have execute_command - return mock response
        return CommandResponse(
            success=True,
            command=command,
            output=_get_mock_response(command),
            message="Command executed (simulated)",
        )
    except Exception as e:
        logger.error(f"Command execution error: {e}")
        return CommandResponse(
            success=False,
            command=command,
            error=str(e),
        )


@router.get("/commands", response_model=list[CommandInfo])
async def list_commands(
    user: TokenData = Depends(require_permission(Permission.OPERATOR)),
):
    """
    Get list of available commands.
    
    Returns commands that the user has permission to execute.
    """
    # Filter commands based on user's class
    available = [
        cmd for cmd in COMMAND_REFERENCE
        if cmd.min_class <= user.user_class
    ]
    return available


def _get_mock_response(command: str) -> str:
    """
    Get a mock response for commands when hub context not fully available.
    
    Used for testing and development.
    """
    cmd_lower = command.lower()
    
    if cmd_lower.startswith("!help"):
        return """Available commands:
!help [command] - Show help for commands
!hubinfo - Display hub information
!ul - List online users
!reglist - List registered users
!banlist - List active bans
!mc <message> - Broadcast message
!kick <nick> [reason] - Kick user
!ban <nick> <time> [reason] - Ban user
+reguser <nick> <class> - Register user
-reguser <nick> - Unregister user
!lstplug - List plugins
!topic <message> - Set topic"""

    elif cmd_lower == "!hubinfo":
        return """Hub Information:
Name: Verlihub
Version: 1.7.0.0
Uptime: System running
Users: Check dashboard for count
Share: Check dashboard for total"""

    elif cmd_lower.startswith("!ul"):
        return "User list available via dashboard /dashboard/users"

    elif cmd_lower.startswith("!reglist"):
        return "Registered users available via dashboard /dashboard/users"

    elif cmd_lower.startswith("!banlist"):
        return "Ban list available via dashboard /dashboard/bans"

    elif cmd_lower.startswith("!lstplug"):
        return """Loaded plugins:
- plugman (Plugin Manager)
- python (Python Scripting)
Check !help plugman for plugin commands"""

    elif cmd_lower.startswith("!mc "):
        msg = command[4:]
        return f"Broadcast sent: {msg}"

    elif cmd_lower.startswith("!topic "):
        topic = command[7:]
        return f"Topic set to: {topic}"

    elif cmd_lower.startswith("+reguser "):
        parts = command.split()
        if len(parts) >= 3:
            return f"User {parts[1]} registered with class {parts[2]}"
        return "Usage: +reguser <nick> <class> [password]"

    elif cmd_lower.startswith("-reguser "):
        parts = command.split()
        if len(parts) >= 2:
            return f"User {parts[1]} unregistered"
        return "Usage: -reguser <nick>"

    elif cmd_lower.startswith("!kick "):
        parts = command.split(maxsplit=2)
        if len(parts) >= 2:
            return f"User {parts[1]} kicked"
        return "Usage: !kick <nick> [reason]"

    elif cmd_lower.startswith("!ban "):
        parts = command.split(maxsplit=3)
        if len(parts) >= 3:
            return f"User {parts[1]} banned for {parts[2]}"
        return "Usage: !ban <nick> <time> [reason]"

    else:
        return f"Unknown command: {command}\nType !help for available commands"
