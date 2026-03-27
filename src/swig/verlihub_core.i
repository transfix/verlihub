/*
	Copyright (C) 2003-2005 Daniel Muller, dan at verliba dot cz
	Copyright (C) 2006-2026 Verlihub Team, info at verlihub dot net

	Verlihub is free software; You can redistribute it
	and modify it under the terms of the GNU General
	Public License as published by the Free Software
	Foundation, either version 3 of the license, or at
	your option any later version.

	Verlihub is distributed in the hope that it will be
	useful, but without any warranty, without even the
	implied warranty of merchantability or fitness for
	a particular purpose. See the GNU General Public
	License for more details.

	Please see http://www.gnu.org/licenses/ for a copy
	of the GNU General Public License.
*/

/*
 * SWIG interface file for verlihub_core Python module.
 * 
 * This module exposes the HubContext and related types to Python,
 * allowing the Python application to control the C++ hub core.
 */

%module(directors="1", threads="1") verlihub_core

// Enable exception handling
%include <std_string.i>
%include <std_vector.i>
%include <exception.i>
%include <stdint.i>

// Enable thread support - release GIL during C++ calls
%thread;

// Exception handling - convert C++ exceptions to Python exceptions
%exception {
    try {
        $action
    } catch (const std::exception& e) {
        SWIG_exception(SWIG_RuntimeError, e.what());
    } catch (...) {
        SWIG_exception(SWIG_UnknownError, "Unknown C++ exception");
    }
}

%{
// Include headers needed for compilation
#include "core/hub_context.h"
#include "core/nmdc_protocol.h"
#include "core/thread_safe_collections.h"

using namespace nVerliHub;
%}

// ============================================================================
// std::string_view typemaps (C++17 feature, not natively supported by SWIG)
// ============================================================================

// Tell SWIG about string_view
%include <std_string.i>

// In block - tell SWIG what string_view is
%{
#include <string_view>
%}

// Input typemap: Python str -> std::string_view
%typemap(in) std::string_view (std::string temp) {
    if (PyUnicode_Check($input)) {
        const char* utf8 = PyUnicode_AsUTF8($input);
        if (!utf8) SWIG_fail;
        temp = utf8;
        $1 = temp;
    } else if (PyBytes_Check($input)) {
        temp = std::string(PyBytes_AsString($input), PyBytes_Size($input));
        $1 = temp;
    } else {
        SWIG_exception(SWIG_TypeError, "Expected string or bytes");
    }
}

// Typecheck for overloading
%typemap(typecheck, precedence=SWIG_TYPECHECK_STRING) std::string_view {
    $1 = PyUnicode_Check($input) || PyBytes_Check($input);
}

// Output typemap: std::string_view -> Python str
%typemap(out) std::string_view {
    $result = PyUnicode_FromStringAndSize($1.data(), $1.size());
}

// Director in typemap: Convert Python str to string_view for callbacks
%typemap(directorin) std::string_view {
    $input = PyUnicode_FromStringAndSize($1.data(), $1.size());
}

// Director out typemap: Convert Python str back to string_view
%typemap(directorout) std::string_view (std::string temp) {
    if (PyUnicode_Check($input)) {
        const char* utf8 = PyUnicode_AsUTF8($input);
        if (utf8) {
            temp = utf8;
            $result = temp;
        }
    }
}

// ============================================================================
// Standard library template instantiations
// ============================================================================

namespace std {
    %template(StringVector) vector<string>;
}

// ============================================================================
// PluginInfo struct for Python
// ============================================================================

%feature("docstring") nVerliHub::PluginInfo "
Information about a loaded plugin.

Attributes:
    name: Plugin name (e.g., 'lua', 'python')
    path: Path to the plugin shared library
    version: Plugin version string
    loaded: Whether the plugin is currently loaded
";

// Template for vector of PluginInfo
%template(PluginInfoVector) std::vector<nVerliHub::PluginInfo>;

// Template for vector of UserInfoSnapshot
%template(UserInfoSnapshotVector) std::vector<nVerliHub::UserInfoSnapshot>;

// ============================================================================
// HubConfig - Python can read/write hub configuration
// ============================================================================

// Enable struct-like access in Python
%feature("python:slot", "tp_str", functype="reprfunc") nVerliHub::HubConfig::__str__;

%extend nVerliHub::HubConfig {
    std::string __str__() {
        return "HubConfig(hub_name='" + $self->hub_name + 
               "', listen_port=" + std::to_string($self->listen_port) + ")";
    }
}

// ============================================================================
// IHubEventCallback - Director class for Python callbacks
// ============================================================================

/*
 * Enable directors for IHubEventCallback.
 * 
 * This allows Python classes to inherit from IHubEventCallback and
 * override methods that will be called from C++.
 * 
 * Example Python usage:
 * 
 *   class MyCallback(verlihub_core.IHubEventCallback):
 *       def OnUserConnect(self, nick, ip):
 *           print(f"User connected: {nick} from {ip}")
 *           return True  # Allow connection
 *       
 *       def OnValidateNick(self, nick, ip):
 *           # Check database for registered user
 *           user = db.find_user(nick)
 *           if user:
 *               return user.user_class  # Registered, needs password
 *           return 0  # Allow as guest
 *       
 *       def OnCheckPassword(self, nick, password):
 *           user = db.find_user(nick)
 *           if user and user.verify_password(password):
 *               return user.user_class
 *           return -1  # Wrong password
 *       
 *       def OnChatMessage(self, nick, message):
 *           if "spam" in message.lower():
 *               return False  # Block message
 *           return True
 *       
 *       def OnHubStarted(self):
 *           print("Hub is now running!")
 */
%feature("director") nVerliHub::IHubEventCallback;

// Note: string_view typemaps are defined earlier in this file

%typemap(directorout) bool {
    $result = PyObject_IsTrue($1);
}

// int return typemap for director methods (OnValidateNick, OnCheckPassword)
%typemap(directorout) int {
    $result = (int)PyLong_AsLong($1);
}

// ============================================================================
// HubContext - Main API class
// ============================================================================

/*
 * Docstrings for Python help()
 */
%feature("autodoc", "1");

%feature("docstring") nVerliHub::HubContext "
Central context object for managing the Verlihub hub.

This is the main entry point for Python to control the C++ hub core.
Create an instance using the static Create() method.

Example:
    ctx = verlihub_core.HubContext.Create('/etc/verlihub')
    if ctx.Initialize():
        ctx.Start(411, '0.0.0.0')
        # Hub runs in background threads
        ctx.Stop()
";

%feature("docstring") nVerliHub::HubContext::Create "
Factory method to create a HubContext.

Args:
    config_dir: Path to verlihub configuration directory

Returns:
    HubContext instance or None on failure
";

%feature("docstring") nVerliHub::HubContext::Initialize "
Initialize the hub (load config, connect to database).

Must be called after Create() and before Start().

Returns:
    True on success
";

%feature("docstring") nVerliHub::HubContext::Start "
Start the hub server.

Args:
    port: Port to listen on (0 = use config)
    listen_ip: IP to bind to (empty = use config)

Returns:
    True if started successfully
";

%feature("docstring") nVerliHub::HubContext::Stop "
Stop the hub server.

Gracefully disconnects all users and stops listening.
Blocks until shutdown is complete.
";

%feature("docstring") nVerliHub::HubContext::IsRunning "
Check if hub is currently running.
";

%feature("docstring") nVerliHub::HubContext::GetUserInfoSnapshots "
Get snapshots of all online users.
Thread-safe: copies all user data under a single lock.

Returns:
    List of UserInfoSnapshot objects with nick, ip, user_class, share, etc.
";

%feature("docstring") nVerliHub::HubContext::GetUserInfo "
Get snapshot of a single user by nick.

Args:
    nick: User's nickname
    out: UserInfoSnapshot to fill

Returns:
    True if user was found and data was copied
";

%feature("docstring") nVerliHub::HubContext::SetEventCallback "
Set the event callback handler.

Only one handler can be active at a time.
Pass None to remove the handler.

Args:
    callback: IHubEventCallback instance (must outlive context)
";

// ============================================================================
// Plugin Management Method Docstrings
// ============================================================================

%feature("docstring") nVerliHub::HubContext::LoadPlugin "
Load a plugin from a shared library file.

Args:
    plugin_path: Path to the plugin .so file

Returns:
    True if plugin was loaded successfully
";

%feature("docstring") nVerliHub::HubContext::UnloadPlugin "
Unload a plugin by name.

Args:
    plugin_name: Name of the plugin to unload (e.g., 'lua', 'python')

Returns:
    True if plugin was unloaded successfully
";

%feature("docstring") nVerliHub::HubContext::ReloadPlugin "
Reload a plugin.

Args:
    plugin_name: Name of the plugin to reload

Returns:
    True if plugin was reloaded successfully
";

%feature("docstring") nVerliHub::HubContext::GetLoadedPlugins "
Get list of loaded plugins.

Returns:
    List of PluginInfo objects
";

%feature("docstring") nVerliHub::HubContext::IsPluginLoaded "
Check if a specific plugin is loaded.

Args:
    plugin_name: Name of the plugin (e.g., 'lua', 'python')

Returns:
    True if the plugin is loaded
";

%feature("docstring") nVerliHub::HubContext::ExecuteLuaScript "
Execute a Lua script (requires Lua plugin to be loaded).

Args:
    script_path: Path to the Lua script file

Returns:
    True if script executed successfully
";

%feature("docstring") nVerliHub::HubContext::UnloadLuaScript "
Unload a Lua script.

Args:
    script_path: Path to the Lua script to unload

Returns:
    True if script was unloaded successfully
";

%feature("docstring") nVerliHub::HubContext::GetLoadedLuaScripts "
Get list of loaded Lua scripts.

Returns:
    List of script paths
";

%feature("docstring") nVerliHub::HubContext::ExecutePythonScript "
Execute a Python script (requires Python plugin to be loaded).

Args:
    script_path: Path to the Python script file

Returns:
    True if script executed successfully
";

%feature("docstring") nVerliHub::HubContext::UnloadPythonScript "
Unload a Python script.

Args:
    script_path: Path to the Python script to unload

Returns:
    True if script was unloaded successfully
";

%feature("docstring") nVerliHub::HubContext::GetLoadedPythonScripts "
Get list of loaded Python scripts.

Returns:
    List of script paths
";

// ============================================================================
// Phase 1: Active/Passive Messaging Docstrings
// ============================================================================

%feature("docstring") nVerliHub::HubContext::SendToActive "
Send raw NMDC message to all active-mode users.

Args:
    message: NMDC message to send

Returns:
    True if hub is running
";

%feature("docstring") nVerliHub::HubContext::SendToPassive "
Send raw NMDC message to all passive-mode users.

Args:
    message: NMDC message to send

Returns:
    True if hub is running
";

%feature("docstring") nVerliHub::HubContext::SendToActiveClass "
Send raw NMDC message to active-mode users in a class range.

Args:
    message: NMDC message to send
    min_class: Minimum user class (inclusive)
    max_class: Maximum user class (inclusive)

Returns:
    True if hub is running
";

%feature("docstring") nVerliHub::HubContext::SendToPassiveClass "
Send raw NMDC message to passive-mode users in a class range.

Args:
    message: NMDC message to send
    min_class: Minimum user class (inclusive)
    max_class: Maximum user class (inclusive)

Returns:
    True if hub is running
";

%feature("docstring") nVerliHub::HubContext::GetActiveUserCount "
Get count of active-mode online users.
";

%feature("docstring") nVerliHub::HubContext::GetPassiveUserCount "
Get count of passive-mode online users.
";

// ============================================================================
// Phase 1: Disconnect / PM / BroadcastChat Docstrings
// ============================================================================

%feature("docstring") nVerliHub::HubContext::DisconnectUser "
Disconnect a user silently (no kick message).

Args:
    nick: User nickname to disconnect

Returns:
    True if user was found and disconnected
";

%feature("docstring") nVerliHub::HubContext::SendPM "
Send a private message.

Args:
    from_nick: Sender nickname
    to_nick: Recipient nickname
    message: Message content

Returns:
    True if recipient was found and message sent
";

%feature("docstring") nVerliHub::HubContext::BroadcastChat "
Broadcast a chat message from a given sender to all users.

Args:
    from_nick: Sender nickname
    message: Chat message content

Returns:
    True if hub is running
";

// ============================================================================
// Phase 1: Flood Protection Configuration Docstrings
// ============================================================================

%feature("docstring") nVerliHub::HubContext::SetFloodConfig "
Set the rate limit for a specific flood message type.

Flood types: 0=Chat, 1=PM, 2=Search, 3=MyINFO, 4=CTM

Args:
    type: Flood type integer (0-4)
    period_ms: Token refill period in milliseconds
    max_tokens: Maximum tokens (burst capacity)
";

// ============================================================================
// Phase 1: Ban Cache Management Docstrings
// ============================================================================

%feature("docstring") nVerliHub::HubContext::LoadBanCache "
Load the ban cache from Python-provided lists.
Replaces the current cache atomically for fast-path rejection.

Args:
    ips: List of banned IP addresses
    nicks: List of banned nicknames
";

%feature("docstring") nVerliHub::HubContext::AddBanCacheIP "
Add a single IP to the ban cache.

Args:
    ip: IP address to ban
";

%feature("docstring") nVerliHub::HubContext::AddBanCacheNick "
Add a single nick to the ban cache.

Args:
    nick: Nickname to ban
";

%feature("docstring") nVerliHub::HubContext::RemoveBanCacheIP "
Remove a single IP from the ban cache.

Args:
    ip: IP address to unban
";

%feature("docstring") nVerliHub::HubContext::RemoveBanCacheNick "
Remove a single nick from the ban cache.

Args:
    nick: Nickname to unban
";

%feature("docstring") nVerliHub::HubContext::ClearBanCache "
Clear the entire ban cache.
";

// ============================================================================
// Phase 2: ForceMove Docstrings
// ============================================================================

%feature("docstring") nVerliHub::HubContext::ForceMove "
Force-move (redirect) a user to another hub address.
Sends $ForceMove and disconnects the user.

Args:
    nick: User to redirect
    address: Hub address to redirect to (e.g. 'other-hub.example.com:411')

Returns:
    True if user was found and redirected
";

// ============================================================================
// Phase 2: Protocol Statistics Docstrings
// ============================================================================

%feature("docstring") nVerliHub::HubContext::GetProtocolStats "
Get a snapshot of protocol-level statistics.

Returns:
    ProtocolStatsSnapshot with message counters
";

%feature("docstring") nVerliHub::HubContext::ProtocolStatsSnapshot "
Snapshot of protocol-level message counters.

Attributes:
    messages_in: Total messages received
    messages_out: Total messages sent
    chat_count: Chat messages processed
    pm_count: Private messages processed
    search_count: Search requests processed
    myinfo_count: MyINFO updates processed
    ctm_count: ConnectToMe requests processed
    sr_count: Search results relayed
    mcto_count: MCTo messages processed
    flood_blocked: Messages blocked by flood limiter
    ban_blocked: Connections/nicks blocked by ban cache
";

// ============================================================================
// Phase 2: GeoIP Lookup Docstrings
// ============================================================================

%feature("docstring") nVerliHub::HubContext::LookupGeoIP "
Look up GeoIP data for an IP address.

Args:
    ip: IPv4 or IPv6 address string

Returns:
    GeoIPInfo with country_code, country_name, city, and available flag
";

%feature("docstring") nVerliHub::HubContext::GeoIPInfo "
Result of a GeoIP lookup.

Attributes:
    country_code: Two-letter ISO 3166-1 code (e.g. 'US')
    country_name: English country name
    city: City name
    available: True if the lookup succeeded
";

// ============================================================================
// unique_ptr handling for HubContext::Create
// ============================================================================

// SWIG 4.0.x generates SwigValueWrapper for unique_ptr return types, which
// tries to copy-construct the unique_ptr (a deleted operation).  SWIG 4.2+
// has a move-aware SwigValueWrapper so it compiles, but 4.0.x doesn't.
//
// Work-around: hide the original Create() from SWIG and provide a raw-pointer
// returning wrapper via %extend with a different name, then alias it back to
// Create in Python.  %newobject tells SWIG that Python owns the returned
// object and should call delete when done.
%ignore nVerliHub::HubContext::Create;

%extend nVerliHub::HubContext {
    static nVerliHub::HubContext *_Create(const std::string &config_path) {
        return nVerliHub::HubContext::Create(config_path).release();
    }
}
%newobject nVerliHub::HubContext::_Create;

// Ignore internal methods not needed in Python
%ignore nVerliHub::HubContext::GetServer;
%ignore nVerliHub::HubContext::GetNMDCServer;
%ignore nVerliHub::HubContext::GetPluginManager;
%ignore nVerliHub::HubContext::GetICUConverter;
%ignore nVerliHub::HubContext::GetGeoIP;
%ignore nVerliHub::HubContext::ForEachUser;
%ignore nVerliHub::HubContext::ForEachUserInClass;
%ignore nVerliHub::HubContext::Log;
%ignore nVerliHub::HubContext::LogFmt;
%ignore nVerliHub::HubContext::FireEvent;

// ============================================================================
// HubEventType enum - expose to Python
// ============================================================================

// Make enum values accessible as verlihub_core.HubEventType_UserConnect etc.

// ============================================================================
// Include the headers to generate wrappers
// ============================================================================

%include "core/hub_context.h"
// Don't include thread_safe_collections.h - internal implementation detail

// ============================================================================
// NMDCProtocol — expose parsing and message construction utilities
// ============================================================================

%feature("docstring") nVerliHub::NMDCProtocol::ParseTag
"Parse an NMDC tag string like '<ClientName V:x.y,M:A,H:1/0/0,S:3>' into a TagData struct.";

%feature("docstring") nVerliHub::NMDCProtocol::ParseMyINFO
"Parse a $MyINFO protocol message into a MyINFOData struct.";

%feature("docstring") nVerliHub::NMDCProtocol::MakeForceMove
"Construct a $ForceMove <address>| protocol message.";

%feature("docstring") nVerliHub::NMDCProtocol::MakeChat
"Construct a main chat protocol message: <from> message|";

%feature("docstring") nVerliHub::NMDCProtocol::MakePrivateMessage
"Construct a PM: $To: <to> From: <from> $<<from>> <message>|";

%feature("docstring") nVerliHub::NMDCProtocol::MakeBotMyINFO
"Construct a $MyINFO for a hub bot with default speed/share.";

%feature("docstring") nVerliHub::NMDCProtocol::MakeBotList
"Construct a $BotList nick1$$nick2$$| message.";

%feature("docstring") nVerliHub::NMDCProtocol::MakeMCTo
"Construct a $MCTo: <to> $<from> <message>| message.";

%feature("docstring") nVerliHub::NMDCProtocol::MakeHubTopic
"Construct a $HubTopic <topic>| message.";

%feature("docstring") nVerliHub::NMDCProtocol::Escape
"Escape special NMDC characters as /%DCNnnn%/ sequences.";

%feature("docstring") nVerliHub::NMDCProtocol::UnEscape
"Unescape /%DCNnnn%/ sequences in a string.";

// Rename 'from' fields in protocol structs to avoid Python keyword clash.
// SWIG would auto-rename to '_from' with a warning; we make it explicit.
%rename("from_nick") nVerliHub::NMDCProtocol::PrivateMessageData::from;
%rename("from_nick") nVerliHub::NMDCProtocol::MCToData::from;

// Expose the full NMDCProtocol namespace — structs + free functions
%include "core/nmdc_protocol.h"

// ============================================================================
// Additional Python-friendly methods
// ============================================================================

%extend nVerliHub::HubContext {
    /*
     * Python context manager support (with statement).
     * 
     * Example:
     *   with verlihub_core.HubContext.Create('/etc/verlihub') as ctx:
     *       ctx.Initialize()
     *       ctx.Start()
     *       # ... hub runs ...
     *   # ctx.Stop() called automatically
     */
    PyObject* __enter__() {
        Py_INCREF($self);
        return SWIG_NewPointerObj($self, SWIGTYPE_p_nVerliHub__HubContext, 0);
    }
    
    void __exit__(PyObject* exc_type, PyObject* exc_val, PyObject* exc_tb) {
        if ($self->IsRunning()) {
            $self->Stop();
        }
    }
    
    /*
     * Property-style access to common values.
     */
    
    %pythoncode %{
    @property
    def user_count(self):
        """Get the current number of online users."""
        return self.GetUserCount()
    
    @property
    def total_share(self):
        """Get the total share size in bytes."""
        return self.GetTotalShare()
    
    @property
    def hub_name(self):
        """Get the hub name."""
        return self.GetHubName()
    
    @property
    def hub_topic(self):
        """Get/set the hub topic."""
        return self.GetHubTopic()
    
    @hub_topic.setter
    def hub_topic(self, value):
        self.SetHubTopic(value)
    
    @property
    def is_running(self):
        """Check if the hub is running."""
        return self.IsRunning()
    
    @property
    def active_user_count(self):
        """Get the count of active-mode users."""
        return self.GetActiveUserCount()
    
    @property
    def passive_user_count(self):
        """Get the count of passive-mode users."""
        return self.GetPassiveUserCount()
    
    @property 
    def has_pending_shutdown(self):
        """Check if shutdown has been requested."""
        return self.HasPendingShutdown()
    
    @staticmethod
    def Create(config_path):
        """Create a new HubContext (caller owns the returned object)."""
        return HubContext._Create(config_path)
    
    def lookup_geoip(self, ip):
        """
        Look up GeoIP data for an IP address.

        Args:
            ip: IPv4 or IPv6 address string

        Returns:
            dict with country_code, country_name, city, available keys
            or None if GeoIP is not available
        """
        info = self.LookupGeoIP(ip)
        return {
            'country_code': info.country_code,
            'country_name': info.country_name,
            'city': info.city,
            'available': info.available,
        }

    def get_protocol_stats(self):
        """
        Get protocol-level message counters.

        Returns:
            dict with messages_in, messages_out, chat_count, pm_count, etc.
        """
        s = self.GetProtocolStats()
        return {
            'messages_in': s.messages_in,
            'messages_out': s.messages_out,
            'chat_count': s.chat_count,
            'pm_count': s.pm_count,
            'search_count': s.search_count,
            'myinfo_count': s.myinfo_count,
            'ctm_count': s.ctm_count,
            'sr_count': s.sr_count,
            'mcto_count': s.mcto_count,
            'flood_blocked': s.flood_blocked,
            'ban_blocked': s.ban_blocked,
        }
    %}
}

// ============================================================================
// Export thread-safe user info (without exposing cUser directly)
// ============================================================================

/*
 * For Python, we expose user information as dictionaries rather than
 * exposing the cUser class directly. This provides better isolation
 * and avoids lifetime management issues.
 */

%extend nVerliHub::HubContext {
    %pythoncode %{
    def get_user_info(self, nick):
        """
        Get information about an online user.
        
        Args:
            nick: User's nickname
            
        Returns:
            dict with user info, or None if user not found
        """
        snap = UserInfoSnapshot()
        if self.GetUserInfo(nick, snap):
            return {
                'nick': snap.nick,
                'user_class': snap.user_class,
                'share': snap.share,
                'ip': snap.ip,
                'country': snap.country,
                'client': snap.client_name,
                'description': snap.description,
                'tag': snap.tag,
                'speed': snap.speed,
                'email': snap.email,
                'login_time': snap.login_time,
                'status': '',
            }
        return None
    
    def get_all_users(self):
        """
        Get list of all online user nicknames.
        
        Returns:
            list of nicknames
        """
        return list(self.GetUserNicks())
    
    def get_user_list_snapshots(self):
        """
        Get list of all online users with full info.
        
        Returns:
            list of dicts with user info
        """
        result = []
        for snap in self.GetUserInfoSnapshots():
            result.append({
                'nick': snap.nick,
                'user_class': snap.user_class,
                'share': snap.share,
                'ip': snap.ip,
                'country': snap.country,
                'client': snap.client_name,
                'description': snap.description,
                'tag': snap.tag,
                'speed': snap.speed,
                'email': snap.email,
                'login_time': snap.login_time,
                'status': '',
            })
        return result
    %}
}
