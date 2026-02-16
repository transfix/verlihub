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

%feature("docstring") nVerliHub::HubContext::SetEventCallback "
Set the event callback handler.

Only one handler can be active at a time.
Pass None to remove the handler.

Args:
    callback: IHubEventCallback instance (must outlive context)
";

// ============================================================================
// unique_ptr handling for HubContext::Create
// ============================================================================

// Don't ignore Create - we'll use a typemap instead
// %ignore nVerliHub::HubContext::Create;

// Typemap for unique_ptr return value: release ownership to Python
%typemap(out) std::unique_ptr<nVerliHub::HubContext> {
    $result = SWIG_NewPointerObj($1.release(), $descriptor(nVerliHub::HubContext*), SWIG_POINTER_OWN);
}

// Tell SWIG that Create should transfer ownership
%newobject nVerliHub::HubContext::Create;

// Ignore internal methods not needed in Python
%ignore nVerliHub::HubContext::GetServer;
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
    def has_pending_shutdown(self):
        """Check if shutdown has been requested."""
        return self.HasPendingShutdown()
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
        # This will be implemented to call C++ and return a dict
        # For now, just check if user exists
        if self.FindUser(nick):
            return {
                'nick': nick,
                # Additional info will be added when cUser is refactored
            }
        return None
    
    def get_all_users(self):
        """
        Get list of all online user nicknames.
        
        Returns:
            list of nicknames
        """
        return list(self.GetUserNicks())
    %}
}
