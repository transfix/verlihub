#!/usr/bin/env python3
"""
Verlihub Hook Dispatcher - Single-Interpreter Mode Support

This dispatcher enables multiple Python scripts to coexist in single-interpreter mode
by providing a centralized hook registration system.

In SUB-INTERPRETER MODE:
  - Each script runs in its own isolated Python interpreter
  - Scripts can have their own OnTimer, OnParsedMsgChat, etc. functions
  - No namespace collisions possible
  
In SINGLE-INTERPRETER MODE:
  - All scripts share one Python interpreter and global namespace
  - Multiple scripts defining OnTimer() will collide (last one wins)
  - THIS DISPATCHER solves the problem by:
    1. Providing a registry for hooks from multiple scripts
    2. Dispatching Verlihub events to all registered handlers
    3. Managing script lifecycle (load/unload)

USAGE:

1. Load this dispatcher FIRST:
   !pyload /path/to/verlihub_hook_dispatcher.py

2. Modify your scripts to use the dispatcher:
   
   # At the top of your script
   try:
       from verlihub_hook_dispatcher import register_script, unregister_script
       USING_DISPATCHER = True
   except ImportError:
       USING_DISPATCHER = False
   
   # Define your hooks as regular functions (any name)
   def my_on_timer(msec=0):
       # Your timer logic
       return 1
   
   def my_on_chat(nick, message):
       # Your chat handler
       return 1
   
   # Register with dispatcher at module load time
   if USING_DISPATCHER:
       SCRIPT_ID = register_script(
           script_name="MyScript",
           hooks={
               "OnTimer": my_on_timer,
               "OnParsedMsgChat": my_on_chat,
               # ... other hooks
           },
           cleanup=my_cleanup_function  # Optional
       )
   else:
       # In sub-interpreter mode, use global hooks
       OnTimer = my_on_timer
       OnParsedMsgChat = my_on_chat
   
   # Cleanup function
   def UnLoad():
       if USING_DISPATCHER:
           unregister_script(SCRIPT_ID)
       # Your cleanup code here

SUPPORTED HOOKS:
  - OnTimer
  - OnParsedMsgChat
  - OnParsedMsgPM
  - OnParsedMsgSearch
  - OnParsedMsgSR
  - OnParsedMsgMyINFO
  - OnParsedMsgValidateNick
  - OnParsedMsgConnectToMe
  - OnParsedMsgRevConnectToMe
  - OnParsedMsgSupports
  - OnUserLogin
  - OnUserLogout
  - OnUserDisconnected
  - OnNewConn
  - OnCloseConn
  - OnHubCommand
  - OnOperatorCommand
  - OnOperatorKicks
  - OnOperatorDrops
  - OnValidateTag
  - OnUserInList
  - OnUnknownMsg
  - OnFlood

Author: Verlihub Team
Version: 1.0.0
License: GPL
"""

import threading
import traceback
from typing import Dict, Callable, Any, Optional, Set
from collections import defaultdict

# =============================================================================
# Global Registry
# =============================================================================

# Script registry: script_id -> {name, hooks, cleanup, enabled}
_scripts = {}
_scripts_lock = threading.Lock()
_next_script_id = 1

# Hook registry: hook_name -> [(script_id, priority, handler), ...]
# Priority: lower numbers execute first (default=100)
_hooks = defaultdict(list)
_hooks_lock = threading.Lock()

# Statistics
_stats = {
    "total_scripts": 0,
    "active_scripts": 0,
    "total_calls": defaultdict(int),
    "failed_calls": defaultdict(int),
    "disabled_scripts": set()
}
_stats_lock = threading.Lock()

def name_and_version():
    """Script metadata for Verlihub"""
    return "HookDispatcher", "1.0.0"

# =============================================================================
# Public API - Script Registration
# =============================================================================

def register_script(
    script_name: str,
    hooks: Dict[str, Callable],
    cleanup: Optional[Callable] = None,
    priority: int = 100,
    auto_enable: bool = True
) -> int:
    """Register a script with the dispatcher
    
    Args:
        script_name: Unique name for the script
        hooks: Dictionary mapping hook names to handler functions
               Example: {"OnTimer": my_timer_func, "OnParsedMsgChat": my_chat_func}
        cleanup: Optional cleanup function to call when script unregisters
        priority: Execution priority (lower = earlier, default=100)
        auto_enable: Whether to enable the script immediately
    
    Returns:
        script_id: Unique ID for this script registration
    
    Example:
        SCRIPT_ID = register_script(
            script_name="ChatLogger",
            hooks={
                "OnTimer": my_timer_handler,
                "OnParsedMsgChat": my_chat_handler
            },
            cleanup=my_cleanup,
            priority=50
        )
    """
    global _next_script_id
    
    with _scripts_lock:
        script_id = _next_script_id
        _next_script_id += 1
        
        _scripts[script_id] = {
            "name": script_name,
            "hooks": hooks,
            "cleanup": cleanup,
            "priority": priority,
            "enabled": auto_enable,
            "registered_at": None  # Will be set when hooks are registered
        }
    
    # Register all hooks
    with _hooks_lock:
        for hook_name, handler in hooks.items():
            _hooks[hook_name].append((script_id, priority, handler))
            # Sort by priority (lower first)
            _hooks[hook_name].sort(key=lambda x: x[1])
    
    with _stats_lock:
        _stats["total_scripts"] += 1
        if auto_enable:
            _stats["active_scripts"] += 1
    
    print(f"[Dispatcher] Registered script '{script_name}' (ID={script_id}) with {len(hooks)} hooks at priority {priority}")
    return script_id

def unregister_script(script_id: int) -> bool:
    """Unregister a script and call its cleanup function
    
    Args:
        script_id: ID returned from register_script()
    
    Returns:
        True if script was unregistered, False if not found
    """
    with _scripts_lock:
        if script_id not in _scripts:
            print(f"[Dispatcher] Warning: Script ID {script_id} not found")
            return False
        
        script = _scripts[script_id]
        script_name = script["name"]
        cleanup = script["cleanup"]
        
        # Call cleanup function if provided
        if cleanup:
            try:
                print(f"[Dispatcher] Calling cleanup for '{script_name}'...")
                cleanup()
            except Exception as e:
                print(f"[Dispatcher] Error in cleanup for '{script_name}': {e}")
                traceback.print_exc()
        
        # Remove from scripts registry
        del _scripts[script_id]
    
    # Remove all hooks for this script
    with _hooks_lock:
        for hook_name in list(_hooks.keys()):
            _hooks[hook_name] = [(sid, pri, handler) for sid, pri, handler in _hooks[hook_name] if sid != script_id]
            if not _hooks[hook_name]:
                del _hooks[hook_name]
    
    with _stats_lock:
        _stats["active_scripts"] -= 1
        _stats["disabled_scripts"].discard(script_id)
    
    print(f"[Dispatcher] Unregistered script '{script_name}' (ID={script_id})")
    return True

def enable_script(script_id: int) -> bool:
    """Enable a previously disabled script"""
    with _scripts_lock:
        if script_id not in _scripts:
            return False
        _scripts[script_id]["enabled"] = True
    
    with _stats_lock:
        _stats["disabled_scripts"].discard(script_id)
        _stats["active_scripts"] += 1
    
    print(f"[Dispatcher] Enabled script ID {script_id}")
    return True

def disable_script(script_id: int) -> bool:
    """Disable a script without unregistering it"""
    with _scripts_lock:
        if script_id not in _scripts:
            return False
        _scripts[script_id]["enabled"] = False
    
    with _stats_lock:
        _stats["disabled_scripts"].add(script_id)
        _stats["active_scripts"] -= 1
    
    print(f"[Dispatcher] Disabled script ID {script_id}")
    return True

def get_script_info(script_id: int) -> Optional[Dict[str, Any]]:
    """Get information about a registered script"""
    with _scripts_lock:
        return _scripts.get(script_id)

def list_scripts() -> Dict[int, Dict[str, Any]]:
    """Get list of all registered scripts"""
    with _scripts_lock:
        return dict(_scripts)

def get_stats() -> Dict[str, Any]:
    """Get dispatcher statistics"""
    with _stats_lock:
        return {
            "total_scripts": _stats["total_scripts"],
            "active_scripts": _stats["active_scripts"],
            "disabled_scripts": len(_stats["disabled_scripts"]),
            "total_calls": dict(_stats["total_calls"]),
            "failed_calls": dict(_stats["failed_calls"]),
            "hooks": {name: len(handlers) for name, handlers in _hooks.items()}
        }

# =============================================================================
# Internal - Hook Dispatching
# =============================================================================

def _dispatch_hook(hook_name: str, *args, **kwargs) -> int:
    """Dispatch a hook call to all registered handlers
    
    Args:
        hook_name: Name of the hook (e.g., "OnTimer", "OnParsedMsgChat")
        *args, **kwargs: Arguments to pass to handlers
    
    Returns:
        Combined return value (for hooks that return int, follows Verlihub convention)
    """
    with _stats_lock:
        _stats["total_calls"][hook_name] += 1
    
    # Get handlers for this hook
    with _hooks_lock:
        handlers = list(_hooks.get(hook_name, []))
    
    if not handlers:
        return 1  # Default return value
    
    # Call each handler in priority order
    result = 1
    for script_id, priority, handler in handlers:
        # Check if script is enabled
        with _scripts_lock:
            if script_id not in _scripts or not _scripts[script_id]["enabled"]:
                continue
            script_name = _scripts[script_id]["name"]
        
        try:
            ret = handler(*args, **kwargs)
            
            # For hooks that return int (most Verlihub hooks):
            # - return 0 means "block/consume" the event
            # - return 1 means "allow/pass" the event
            # If any handler returns 0, we stop processing and return 0
            if isinstance(ret, int) and ret == 0:
                result = 0
                break  # Stop processing further handlers
            
        except Exception as e:
            with _stats_lock:
                _stats["failed_calls"][hook_name] += 1
            
            print(f"[Dispatcher] Error in {hook_name} handler for '{script_name}': {e}")
            traceback.print_exc()
            
            # Continue to next handler despite error
            continue
    
    return result

# =============================================================================
# Verlihub Event Hooks - Global Definitions
# =============================================================================

def OnTimer(msec=0):
    """Timer event - called periodically"""
    return _dispatch_hook("OnTimer", msec)

def OnParsedMsgChat(nick, message):
    """Chat message received"""
    return _dispatch_hook("OnParsedMsgChat", nick, message)

def OnParsedMsgPM(nick, message, other_nick):
    """Private message received"""
    return _dispatch_hook("OnParsedMsgPM", nick, message, other_nick)

def OnParsedMsgSearch(nick, search_string):
    """Search request received"""
    return _dispatch_hook("OnParsedMsgSearch", nick, search_string)

def OnParsedMsgSR(nick, result):
    """Search result received"""
    return _dispatch_hook("OnParsedMsgSR", nick, result)

def OnParsedMsgMyINFO(nick):
    """MyINFO message received"""
    return _dispatch_hook("OnParsedMsgMyINFO", nick)

def OnParsedMsgValidateNick(nick):
    """Nick validation request"""
    return _dispatch_hook("OnParsedMsgValidateNick", nick)

def OnParsedMsgConnectToMe(nick, ip, port):
    """ConnectToMe request"""
    return _dispatch_hook("OnParsedMsgConnectToMe", nick, ip, port)

def OnParsedMsgRevConnectToMe(nick, other_nick):
    """RevConnectToMe request"""
    return _dispatch_hook("OnParsedMsgRevConnectToMe", nick, other_nick)

def OnParsedMsgSupports(ip, msg, back):
    """Supports message received"""
    return _dispatch_hook("OnParsedMsgSupports", ip, msg, back)

def OnUserLogin(nick):
    """User logged in"""
    return _dispatch_hook("OnUserLogin", nick)

def OnUserLogout(nick):
    """User logged out"""
    return _dispatch_hook("OnUserLogout", nick)

def OnUserDisconnected(nick):
    """User disconnected"""
    return _dispatch_hook("OnUserDisconnected", nick)

def OnNewConn(ip):
    """New connection established"""
    return _dispatch_hook("OnNewConn", ip)

def OnCloseConn(ip):
    """Connection closed"""
    return _dispatch_hook("OnCloseConn", ip)

def OnHubCommand(nick, command, user_class, in_pm, prefix):
    """Hub command received"""
    return _dispatch_hook("OnHubCommand", nick, command, user_class, in_pm, prefix)

def OnOperatorCommand(nick, command, user_class, in_pm):
    """Operator command received"""
    return _dispatch_hook("OnOperatorCommand", nick, command, user_class, in_pm)

def OnOperatorKicks(op_nick, nick, reason):
    """Operator kicked a user"""
    return _dispatch_hook("OnOperatorKicks", op_nick, nick, reason)

def OnOperatorDrops(op_nick, nick, reason):
    """Operator dropped a user"""
    return _dispatch_hook("OnOperatorDrops", op_nick, nick, reason)

def OnValidateTag(nick, tag):
    """Validate user tag"""
    return _dispatch_hook("OnValidateTag", nick, tag)

def OnUserInList(nick):
    """User added to user list"""
    return _dispatch_hook("OnUserInList", nick)

def OnUnknownMsg(nick, message):
    """Unknown message type received"""
    return _dispatch_hook("OnUnknownMsg", nick, message)

def OnFlood(nick, message):
    """Flood detected"""
    return _dispatch_hook("OnFlood", nick, message)

# =============================================================================
# Admin Commands
# =============================================================================

def OnHubCommand(nick, command, user_class, in_pm, prefix):
    """Handle dispatcher admin commands"""
    # First dispatch to registered scripts
    result = _dispatch_hook("OnHubCommand", nick, command, user_class, in_pm, prefix)
    if result == 0:
        return 0  # Command was handled by a script
    
    # Handle dispatcher-specific commands
    parts = command.split()
    if not parts or parts[0] != "dispatcher":
        return 1  # Not our command
    
    # Check permissions
    if user_class < 10:  # Master only
        try:
            vh.pm("Permission denied. Master class required.", nick)
        except:
            pass
        return 0
    
    if len(parts) < 2:
        try:
            vh.pm("Usage: !dispatcher [list|stats|enable|disable|help]", nick)
        except:
            pass
        return 0
    
    subcmd = parts[1].lower()
    
    if subcmd == "list":
        scripts = list_scripts()
        try:
            vh.pm(f"Registered Scripts ({len(scripts)}):", nick)
            for sid, info in scripts.items():
                status = "✓" if info["enabled"] else "✗"
                vh.pm(f"  [{status}] ID={sid}: {info['name']} ({len(info['hooks'])} hooks, priority={info['priority']})", nick)
        except:
            pass
    
    elif subcmd == "stats":
        stats = get_stats()
        try:
            vh.pm("Dispatcher Statistics:", nick)
            vh.pm(f"  Total scripts: {stats['total_scripts']}", nick)
            vh.pm(f"  Active scripts: {stats['active_scripts']}", nick)
            vh.pm(f"  Disabled scripts: {stats['disabled_scripts']}", nick)
            vh.pm(f"  Hook calls:", nick)
            for hook, count in sorted(stats['total_calls'].items()):
                failed = stats['failed_calls'].get(hook, 0)
                vh.pm(f"    {hook}: {count} calls ({failed} failed)", nick)
        except:
            pass
    
    elif subcmd == "enable" and len(parts) > 2:
        try:
            script_id = int(parts[2])
            if enable_script(script_id):
                vh.pm(f"Script ID {script_id} enabled", nick)
            else:
                vh.pm(f"Script ID {script_id} not found", nick)
        except ValueError:
            vh.pm("Invalid script ID", nick)
        except:
            pass
    
    elif subcmd == "disable" and len(parts) > 2:
        try:
            script_id = int(parts[2])
            if disable_script(script_id):
                vh.pm(f"Script ID {script_id} disabled", nick)
            else:
                vh.pm(f"Script ID {script_id} not found", nick)
        except ValueError:
            vh.pm("Invalid script ID", nick)
        except:
            pass
    
    elif subcmd == "help":
        try:
            vh.pm("Dispatcher Commands:", nick)
            vh.pm("  !dispatcher list           - List all registered scripts", nick)
            vh.pm("  !dispatcher stats          - Show dispatcher statistics", nick)
            vh.pm("  !dispatcher enable <id>    - Enable a script", nick)
            vh.pm("  !dispatcher disable <id>   - Disable a script", nick)
            vh.pm("  !dispatcher help           - Show this help", nick)
        except:
            pass
    
    else:
        try:
            vh.pm(f"Unknown subcommand: {subcmd}", nick)
        except:
            pass
    
    return 0  # Command handled

def UnLoad():
    """Cleanup when dispatcher unloads"""
    print("[Dispatcher] Unloading - cleaning up all scripts...")
    
    # Unregister all scripts (will call their cleanup functions)
    with _scripts_lock:
        script_ids = list(_scripts.keys())
    
    for script_id in script_ids:
        unregister_script(script_id)
    
    print("[Dispatcher] Dispatcher unloaded")

# =============================================================================
# Initialization
# =============================================================================

print("=" * 80)
print("Verlihub Hook Dispatcher Loaded")
print("=" * 80)
print("This dispatcher enables multiple scripts to coexist in single-interpreter mode.")
print("")
print("Load this dispatcher FIRST, then load other scripts that use it.")
print("See script documentation for integration instructions.")
print("")
print("Commands: !dispatcher help")
print("=" * 80)
