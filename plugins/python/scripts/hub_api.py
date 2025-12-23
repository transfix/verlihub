#!/usr/bin/env python3
"""
Verlihub FastAPI HTTP REST API Script

Provides HTTP endpoints for querying hub information:
- Hub statistics (name, users online, total share)
- User list with details
- User information by nickname
- Geographic distribution
- Share statistics

Admin commands:
  !api start [port]  - Start the API server (default port: 8000)
  !api stop          - Stop the API server
  !api status        - Check API server status
  !api help          - Show help

Requirements:
  pip install fastapi uvicorn

IMPORTANT: This script requires Verlihub to be compiled with single-interpreter mode:
  cmake -DPYTHON_USE_SINGLE_INTERPRETER=ON ..
  
FastAPI/Pydantic use C extensions (PyO3/Rust) that don't support Python subinterpreters.
The default subinterpreter mode will fail to load FastAPI with errors like:
  - "PyO3 modules do not yet support subinterpreters"
  - "Interpreter change detected - this module can only be loaded into one interpreter"

Author: Verlihub Team
Version: 1.0.0
"""

import vh
import sys
import os
import asyncio
threading
import time
import traceback
from typing import Optional, List, Dict, Any
from datetime import datetime

# Try to import dispatcher for single-interpreter mode
try:
    from dispatcher import register_script, unregister_script
    USING_DISPATCHER = True
except ImportError:
    USING_DISPATCHER = False

SCRIPT_ID = None

# Try to find and add venv site-packages to path
script_dir = os.path.dirname(os.path.abspath(__file__))

# Look for venv in multiple locations
venv_locations = [
    # Script's own directory
    os.path.join(script_dir, 'venv'),
    # Parent directory (for installed scripts)
    os.path.join(os.path.dirname(script_dir), 'venv'),
    # Build directory (for tests)
    os.path.join(script_dir, '..', '..', 'venv'),
]

# Also check environment variable
if 'VERLIHUB_PYTHON_VENV' in os.environ:
    venv_locations.insert(0, os.environ['VERLIHUB_PYTHON_VENV'])

# Try each location and find site-packages
venv_found = False
for venv_base in venv_locations:
    if not os.path.exists(venv_base):
        continue
    
    # Try different Python version patterns
    lib_dir = os.path.join(venv_base, 'lib')
    if os.path.exists(lib_dir):
        for item in os.listdir(lib_dir):
            if item.startswith('python'):
                site_packages = os.path.join(lib_dir, item, 'site-packages')
                if os.path.exists(site_packages):
                    print(f"[Hub API] Found venv at: {venv_base}")
                    print(f"[Hub API] Adding to path: {site_packages}")
                    sys.path.insert(0, site_packages)
                    venv_found = True
                    break
    if venv_found:
        break

if not venv_found:
    print("[Hub API] No venv found, using system Python packages")

# Debug: Print current sys.path
print(f"[Hub API] Current sys.path: {sys.path[:3]}...")  # First 3 entries

try:
    print("[Hub API] Attempting to import FastAPI...")
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import JSONResponse
    print("[Hub API] FastAPI imported successfully!")
    print("[Hub API] Attempting to import uvicorn...")
    import uvicorn
    print("[Hub API] uvicorn imported successfully!")
    FASTAPI_AVAILABLE = True
    print("[Hub API] ✓ All dependencies loaded")
except ImportError as e:
    FASTAPI_AVAILABLE = False
    print(f"[Hub API] ✗ ImportError: {e}")
    print(f"[Hub API] sys.path when import failed: {sys.path[:5]}")
    import traceback
    traceback.print_exc()

# Global state
api_server = None
api_thread = None
api_port = 8000
server_running = False

# Thread-safe cache for hub data (updated by OnTimer in main thread)
data_cache = {
    "hub_info": {},
    "users": [],
    "geo_stats": {},
    "share_stats": {},
    "hub_encoding": "cp1251",  # Default to CP1251 (common for DC++ hubs)
    "last_update": 0
}
data_cache_lock = threading.Lock()
CACHE_TTL = 1.0  # Cache time-to-live in seconds

# Initialize FastAPI app
if FASTAPI_AVAILABLE:
    app = FastAPI(
        title="Verlihub API",
        description="REST API for Verlihub DC++ Hub",
        version="1.0.0"
    )

def name_and_version():
    """Script metadata"""
    return "HubAPI", "1.0.0"

# =============================================================================
# Helper Functions
# =============================================================================

def safe_decode(text: str, encoding: str = "cp1251") -> str:
    """Safely decode text from hub encoding to UTF-8 for JSON/web display
    
    Args:
        text: String in hub encoding (may already be Python str if ASCII-compatible)
        encoding: Hub encoding (cp1251, iso-8859-1, etc.)
    
    Returns:
        UTF-8 string safe for JSON, with problematic chars replaced
    """
    if not text:
        return text
    
    try:
        # If text is already a proper Unicode string (all chars < 128), return as-is
        if all(ord(c) < 128 for c in text):
            return text
        
        # Try to encode back to bytes using latin-1 (which preserves byte values)
        # then decode using the actual hub encoding
        try:
            # Python strings from C++ are decoded as latin-1 by default when they
            # contain bytes > 127. We need to reverse that and use the real encoding.
            byte_data = text.encode('latin-1', errors='replace')
            return byte_data.decode(encoding, errors='replace')
        except (UnicodeDecodeError, UnicodeEncodeError, LookupError):
            # If that fails, just replace problematic characters
            return text.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
    except Exception as e:
        print(f"[Hub API] Encoding error for text '{text[:50]}...': {e}")
        # Last resort: keep what we have
        return text

def update_data_cache():
    """Update cached data from vh module (called from main thread only)"""
    try:
        # Get hub encoding first
        hub_encoding = vh.GetConfig("config", "hub_encoding", "cp1251")
        if not hub_encoding or hub_encoding.lower() == "utf-8":
            hub_encoding = "utf-8"  # No conversion needed
        
        # Gather all data
        hub_info = _get_hub_info_unsafe()
        users = _get_all_users_unsafe(hub_encoding)
        geo_stats = _get_geographic_stats_unsafe()
        share_stats = _get_share_stats_unsafe(users)
        
        # Update cache atomically
        with data_cache_lock:
            data_cache["hub_info"] = hub_info
            data_cache["users"] = users
            data_cache["geo_stats"] = geo_stats
            data_cache["share_stats"] = share_stats
            data_cache["hub_encoding"] = hub_encoding
            data_cache["last_update"] = time.time()
    except Exception as e:
        print(f"Error updating data cache: {e}")

def get_cached_data(key: str) -> Any:
    """Get cached data (thread-safe)"""
    with data_cache_lock:
        return data_cache.get(key)

def _get_hub_info_unsafe() -> Dict[str, Any]:
    """Get basic hub information (UNSAFE - call only from main thread)"""
    try:
        hub_name = vh.GetConfig("config", "hub_name") or "Verlihub"
        hub_desc = vh.GetConfig("config", "hub_desc") or "DC++ Hub"
        topic = vh.Topic() or ""
        max_users = vh.GetConfig("config", "max_users") or "0"
        
        return {
            "name": hub_name,
            "description": hub_desc,
            "topic": topic,
            "max_users": int(max_users) if max_users.isdigit() else 0,
            "version": vh.name_and_version()
        }
    except Exception as e:
        return {
            "name": "Verlihub",
            "description": "DC++ Hub",
            "topic": "",
            "max_users": 0,
            "version": "Unknown",
            "error": str(e)
        }

def _get_user_info_unsafe(nick: str) -> Optional[Dict[str, Any]]:
    """Get detailed information about a user (UNSAFE - call only from main thread)
    
    Args:
        nick: User nickname in hub encoding (exactly as returned by GetNickList)
    """
    try:
        # Get hub encoding for proper display conversion
        hub_encoding = data_cache.get("hub_encoding", "cp1251")
        
        user_class = vh.GetUserClass(nick)
        if user_class < 0:  # User not found
            print(f"[Hub API] WARNING: GetUserClass returned -1 for nick: {nick!r}")
            return None
        
        ip = vh.GetUserIP(nick)
        host = vh.GetUserHost(nick)
        cc = vh.GetUserCC(nick)
        hub_url = vh.GetUserHubURL(nick) or ""
        ext_json = vh.GetUserExtJSON(nick) or ""
        
        # Get comprehensive geographic info
        country = ""
        city = ""
        region = ""
        region_code = ""
        timezone = ""
        continent = ""
        continent_code = ""
        postal_code = ""
        asn = ""
        
        if ip:
            try:
                country = vh.GetIPCN(ip) or ""
                city_raw = vh.GetIPCity(ip, "")
                city = city_raw if (city_raw and city_raw not in ("--", "")) else ""
                asn_raw = vh.GetIPASN(ip, "")
                asn = asn_raw if (asn_raw and asn_raw not in ("--", "")) else ""
                
                # GetGeoIP returns a dict with all geographic details
                geo_data = vh.GetGeoIP(ip, "")
                print(f"[Hub API] DEBUG geo_data for {ip}: type={type(geo_data)}, value={geo_data!r}")
                if geo_data and isinstance(geo_data, dict):
                    region = geo_data.get("region", "") or ""
                    region_code = geo_data.get("region_code", "") or ""
                    timezone = geo_data.get("time_zone", "") or ""  # Note: key is "time_zone" not "timezone"
                    continent = geo_data.get("continent", "") or ""
                    continent_code = geo_data.get("continent_code", "") or ""
                    postal_code = geo_data.get("postal_code", "") or ""
                    # Override with GeoIP values if available
                    if not country and geo_data.get("country"):
                        country = geo_data.get("country")
                    if not city and geo_data.get("city"):
                        city = geo_data.get("city")
                else:
                    print(f"[Hub API] WARNING: GetGeoIP returned non-dict for {ip}: {geo_data!r}")
            except Exception as e:
                print(f"[Hub API] Error getting geo info for {ip}: {e}")
        
        # Parse MyINFO for additional details
        myinfo = vh.GetMyINFO(nick)
        share = 0
        desc = ""
        tag = ""
        email = ""
        
        if myinfo and isinstance(myinfo, tuple) and len(myinfo) >= 6:
            # Tuple format: (nick, desc, tag, speed, email, sharesize)
            _, desc, tag, speed, email, size_str = myinfo[:6]
            
            # Convert desc, tag, email from hub encoding to UTF-8 for display
            desc = safe_decode(desc, hub_encoding)
            tag = safe_decode(tag, hub_encoding)
            email = safe_decode(email, hub_encoding)
            
            try:
                # Share size is in bytes as a string
                share = int(size_str) if size_str else 0
            except (ValueError, TypeError):
                share = 0
        
        # Convert nick for display (but keep original for lookups)
        nick_display = safe_decode(nick, hub_encoding)
        
        return {
            "nick": nick_display,  # Converted for display
            "class": user_class,
            "class_name": get_class_name(user_class),
            "ip": ip,
            "host": host,
            "country_code": cc,
            "country": country,
            "city": city,
            "region": region,
            "region_code": region_code,
            "timezone": timezone,
            "continent": continent,
            "continent_code": continent_code,
            "postal_code": postal_code,
            "asn": asn,
            "hub_url": hub_url,
            "ext_json": ext_json,
            "description": desc,
            "tag": tag,
            "email": email,
            "share": share,
            "share_formatted": format_bytes(share)
        }
    except Exception as e:
        print(f"Error getting user info for {nick}: {e}")
        return None

def get_class_name(user_class: int) -> str:
    """Convert user class number to name"""
    classes = {
        -1: "Disconnected",
        0: "Guest",
        1: "Regular",
        2: "VIP", 
        3: "Operator",
        4: "Cheef",
        5: "Admin",
        10: "Master"
    }
    return classes.get(user_class, f"Class{user_class}")

def format_bytes(size: int) -> str:
    """Format bytes into human-readable string"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} EB"

def _get_all_users_unsafe(hub_encoding: str = "cp1251") -> List[Dict[str, Any]]:
    """Get list of all users with their information (UNSAFE - call only from main thread)
    
    Args:
        hub_encoding: The hub's character encoding for proper display conversion
    """
    users = []
    nick_list = vh.GetNickList()
    
    if not isinstance(nick_list, list):
        return users
    
    for nick in nick_list:
        # nick is in hub encoding - use it as-is for lookups
        user_info = _get_user_info_unsafe(nick)
        if user_info:
            users.append(user_info)
    
    return users

def _get_geographic_stats_unsafe() -> Dict[str, int]:
    """Get user distribution by country (UNSAFE - call only from main thread)"""
    stats = {}
    nick_list = vh.GetNickList()
    
    for nick in nick_list:
        try:
            cc = vh.GetUserCC(nick)
            if cc and cc != "--":
                stats[cc] = stats.get(cc, 0) + 1
        except:
            pass
    
    return stats

def _get_share_stats_unsafe() -> Dict[str, Any]:
    """Get share size statistics (UNSAFE - call only from main thread)"""
    total_share = 0
    users = _get_all_users_unsafe()
    
    for user in users:
        total_share += user.get("share", 0)
    
    return {
        "total": total_share,
        "total_formatted": format_bytes(total_share),
        "average": total_share // len(users) if users else 0,
        "average_formatted": format_bytes(total_share // len(users)) if users else "0 B"
    }

# =============================================================================
# FastAPI Routes
# =============================================================================

if FASTAPI_AVAILABLE:
    @app.get("/")
    async def root():
        """API root endpoint"""
        return {
            "service": "Verlihub REST API",
            "version": "1.0.0",
            "endpoints": {
                "hub_info": "/api/hub",
                "statistics": "/api/stats",
                "users": "/api/users",
                "user_detail": "/api/user/{nick}",
                "geography": "/api/geo",
                "share": "/api/share"
            }
        }

    @app.get("/api/hub")
    async def hub_info():
        """Get hub information"""
        return get_cached_data("hub_info") or {}

    @app.get("/api/stats")
    async def statistics():
        """Get hub statistics"""
        try:
            hub_info = get_cached_data("hub_info") or {}
            users = get_cached_data("users") or []
            share_stats = get_cached_data("share_stats") or {}
            last_update = get_cached_data("last_update") or 0
            
            return {
                "timestamp": datetime.utcnow().isoformat(),
                "users_online": len(users),
                "max_users": hub_info.get("max_users", 0),
                "total_share": share_stats.get("total_formatted", "0 B"),
                "hub_name": hub_info.get("name", "Unknown"),
                "cache_age": time.time() - last_update
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/users")
    async def users(limit: Optional[int] = None, offset: int = 0):
        """Get list of online users"""
        try:
            all_users = get_cached_data("users") or []
            
            # Apply pagination
            if limit:
                paginated = all_users[offset:offset + limit]
            elif offset:
                paginated = all_users[offset:]
            else:
                paginated = all_users
            
            return {
                "count": len(paginated),
                "total": len(all_users),
                "users": paginated
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/user/{nick}")
    async def user_detail(nick: str):
        """Get detailed information about a specific user"""
        all_users = get_cached_data("users") or []
        
        # Find user in cache
        user_info = next((u for u in all_users if u.get("nick") == nick), None)
        
        if not user_info:
            raise HTTPException(status_code=404, detail=f"User '{nick}' not found")
        
        return user_info

    @app.get("/api/geo")
    async def geography():
        """Get geographic distribution of users"""
        try:
            stats = get_cached_data("geo_stats") or {}
            
            # Sort by count descending
            sorted_stats = sorted(stats.items(), key=lambda x: x[1], reverse=True)
            
            return {
                "total_countries": len(stats),
                "distribution": [
                    {"country_code": cc, "users": count}
                    for cc, count in sorted_stats
                ]
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/share")
    async def share_statistics():
        """Get share size statistics"""
        try:
            return get_cached_data("share_stats") or {}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/health")
    async def health():
        """Health check endpoint"""
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat()
        }

# =============================================================================
# Server Management
# =============================================================================

def run_server(port: int):
    """Run the FastAPI server in a separate thread"""
    global server_running
    
    try:
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=port,
            log_level="info",
            access_log=True
        )
        server = uvicorn.Server(config)
        
        # Run in the current thread (which is already a separate thread)
        asyncio.run(server.serve())
    except Exception as e:
        print(f"API server error: {e}")
    finally:
        server_running = False

def start_api_server(port: int = 8000) -> bool:
    """Start the API server"""
    global api_thread, api_port, server_running
    
    if not FASTAPI_AVAILABLE:
        return False
    
    if api_thread and api_thread.is_alive():
        return False  # Already running
    
    # Initialize cache before starting server
    update_data_cache()
    
    api_port = port
    api_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    api_thread.start()
    
    return True

def stop_api_server() -> bool:
    """Stop the API server"""
    global server_running
    
    if not server_running:
        return False
    
    # Signal to stop (uvicorn doesn't have easy way to stop from another thread)
    # The daemon thread will exit when script unloads
    server_running = False
    return True

def is_api_running() -> bool:
    """Check if API server is running"""
    return server_running

# =============================================================================
# Verlihub Event Hooks
# =============================================================================

def hub_api_supports_handler(ip, msg, back):
    """Called when user sends $Supports message
    
    Args:
        ip: User IP address
        msg: The full $Supports message string
        back: Response string (unused)
    
    Returns:
        1 to allow the message to be processed normally
    """
    try:
        # Extract nick from connection by IP (use GetNickList to find user)
        nick_list = vh.GetNickList()
        user_nick = None
        
        # Find the user with this IP
        for nick in nick_list:
            if vh.GetUserIP(nick) == ip:
                user_nick = nick
                break
        
        if user_nick:
            # Parse support flags from message
            # $Supports format: "$Supports FLAG1 FLAG2 FLAG3 ..."
            if msg.startswith("$Supports "):
                flags_str = msg[10:]  # Skip "$Supports "
                flags = [f.strip() for f in flags_str.split() if f.strip()]
                
                # Store in cache
                with support_flags_lock:
                    support_flags_cache[user_nick] = flags
                
                print(f"[Hub API] Captured {len(flags)} support flags for {user_nick}: {flags}")
    except Exception as e:
        print(f"[Hub API] Error in OnParsedMsgSupports: {e}")
        import traceback
        traceback.print_exc()
    
    return 1  # Allow message to be processed

def hub_api_timer_handler(msec):
    """Update data cache periodically (runs in main thread)"""
    # Only update if API server is running
    if not server_running:
        return 1
    
    # Throttle updates - only update every CACHE_UPDATE_INTERVAL seconds
    current_time = time.time()
    if current_time - last_cache_update < CACHE_UPDATE_INTERVAL:
        return 1
    
    last_cache_update = current_time
    update_data_cache()
    return 1

def hub_api_login_handler(nick):
    """Update cache when user logs in (runs in main thread)
    
    Also proactively schedules network diagnostics for the user's IP
    """
    if server_running:
        update_data_cache()
    return 1

def hub_api_logout_handler(nick):
    """Update cache when user logs out (runs in main thread)"""
    if server_running:
        update_data_cache()
    return 1

def hub_api_command_handler(nick, command, user_class, in_pm, prefix):
    """Handle hub commands
    
    IMPORTANT: Return value logic (Python -> C++ -> Verlihub core):
    - return 1 → C++ returns true → Command is ALLOWED (passes through)
    - return 0 → C++ returns false → Command is BLOCKED (consumed/handled)
    
    So: return 1 for commands we DON'T handle, return 0 for commands we DO handle
    """
    parts = command.split()
    
    if not parts or parts[0] != "api":
        return 1  # Not our command, allow it (true in C++)
    
    # Check permissions (operators only)
    if user_class < 3:
        return 0  # Block this command (false in C++)
    
    # Helper function to send messages (handles the correct vh.pm/vh.usermc signature)
    def send_message(msg):
        if in_pm:
            vh.pm(msg, nick)  # pm(message, destination_nick, [from_nick], [bot_nick])
        else:
            vh.usermc(msg, nick)  # usermc(message, destination_nick, [bot_nick])
    
    if len(parts) < 2:
        write(nick, "Usage: !api [start|stop|status|help] [port]")
        return 0
    
    subcmd = parts[1].lower()
    
    if subcmd == "start":
        if not FASTAPI_AVAILABLE:
            write(nick, "ERROR: FastAPI not installed. Run: pip install fastapi uvicorn")
            return 0
        
        port = 8000
        if len(parts) > 2:
            try:
                port = int(parts[2])
                if port < 1024 or port > 65535:
                    write(nick, "ERROR: Port must be between 1024 and 65535")
                    return 0
            except ValueError:
                write(nick, "ERROR: Invalid port number")
                return 0
        
        if is_api_running():
            write(nick, f"API server already running on port {api_port}")
        else:
            if start_api_server(port):
                write(nick, f"API server starting on http://0.0.0.0:{port}")
                write(nick, f"Documentation: http://localhost:{port}/docs")
            else:
                write(nick, "ERROR: Failed to start API server")
    
    elif subcmd == "stop":
        if is_api_running():
            stop_api_server()
            write(nick, "API server stopping...")
        else:
            write(nick, "API server is not running")
    
    elif subcmd == "status":
        if is_api_running():
            write(nick, f"API server is RUNNING on port {api_port}")
            write(nick, f"Endpoints: http://localhost:{api_port}/")
            write(nick, f"Docs: http://localhost:{api_port}/docs")
        else:
            write(nick, "API server is STOPPED")
    
    elif subcmd == "help":
        help_text = """
Hub API Commands:
  !api start [port]  - Start API server (default: 8000)
  !api stop          - Stop API server
  !api status        - Check server status
  !api help          - Show this help

API Endpoints:
  GET /              - API overview
  GET /api/hub       - Hub information
  GET /api/stats     - Hub statistics
  GET /api/users     - List all users
  GET /api/user/{nick} - User details
  GET /api/geo       - Geographic distribution
  GET /api/share     - Share statistics
  GET /health        - Health check
  GET /docs          - Interactive API docs

Requirements:
  pip install fastapi uvicorn
"""
        for line in help_text.strip().split('\n'):
            write(nick, line)
    
    else:
        write(nick, f"Unknown subcommand: {subcmd}")
        write(nick, "Use: !api help")
    
    return 0  # Command handled

def hub_api_cleanup():
    """Cleanup when script unloads"""
    global server_running
    
    if is_api_running():
        print("Stopping API server...")
        stop_api_server()
        server_running = False
    
    print("Hub API script unloaded")

# =============================================================================
# Hook Registration
# =============================================================================

HOOKS = {
    'OnTimer': hub_api_timer_handler,
    'OnParsedMsgSupports': hub_api_supports_handler,
    'OnUserLogin': hub_api_login_handler,
    'OnUserLogout': hub_api_logout_handler,
    'OnHubCommand': hub_api_command_handler
}

if USING_DISPATCHER:
    SCRIPT_ID = register_script(
        script_name="HubAPI",
        hooks=HOOKS,
        cleanup=hub_api_cleanup,
        priority=100
    )
    print(f"[Hub API] Registered with dispatcher, ID={SCRIPT_ID}")
else:
    # Sub-interpreter mode: assign hooks globally
    OnTimer = hub_api_timer_handler
    OnParsedMsgSupports = hub_api_supports_handler
    OnUserLogin = hub_api_login_handler
    OnUserLogout = hub_api_logout_handler
    OnHubCommand = hub_api_command_handler

def UnLoad():
    """Cleanup on script unload"""
    if USING_DISPATCHER and SCRIPT_ID is not None:
        unregister_script(SCRIPT_ID)
    hub_api_cleanup()

# =============================================================================
# Initialization
# =============================================================================

if FASTAPI_AVAILABLE:
    print("Hub API script loaded successfully")
    print("Use !api help to see available commands")
else:
    print("Hub API script loaded with LIMITED functionality")
    print("Install dependencies: pip install fastapi uvicorn")
