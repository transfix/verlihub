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

#ifndef HUB_CONTEXT_H
#define HUB_CONTEXT_H

#include <memory>
#include <shared_mutex>
#include <mutex>
#include <atomic>
#include <functional>
#include <string>
#include <string_view>
#include <vector>
#include <thread>
#include <stop_token>
#include <source_location>
#include <sstream>
#include <chrono>
#include <span>

#include "compat_format.h"
#include "thread_safe_collections.h"

namespace nVerliHub {

// Forward declarations
class NMDCHubServer;
class GeoIPLookup;

// Legacy forward declarations (kept for plugin API compatibility)
namespace nSocket {
    class cServerDC;
    class cConnDC;
}

namespace nPlugin {
    class cVHPluginMgr;
}

namespace nUtils {
    class cICUConvert;
    class cMaxMindDB;
}

class cUser;
class cDCProto;

// ============================================================================
// Configuration structures using C++20 designated initializers
// ============================================================================

/**
 * Plugin information structure for SWIG bindings.
 * Defined at namespace level so SWIG can properly expose it.
 */
struct PluginInfo {
    std::string name;       ///< Plugin name/identifier
    std::string path;       ///< Path to plugin library
    std::string version;    ///< Plugin version string
    bool loaded{false};     ///< Whether plugin is currently loaded
};

/**
 * Hub configuration (mirrors SetupList 'config' section)
 */
struct HubConfig {
    std::string hub_name;
    std::string hub_desc;
    std::string hub_topic;
    std::string hub_host;
    std::string hub_owner;
    std::string hub_encoding{"CP1252"};
    std::string hub_security{"Hub-Security"};
    std::string opchat_name{"OpChat"};
    
    int listen_port{411};
    std::string listen_ip{"0.0.0.0"};
    int max_users{1000};
    int min_share{0};
    int max_share{0};
    
    bool tls_enabled{false};
    int tls_port{0};
    std::string tls_cert_file;
    std::string tls_key_file;
};

/**
 * Snapshot of a single online user's information.
 * Returned by GetUserInfoSnapshots() — safe to use after the call
 * (all data is copied from NMDCHubServer under lock).
 */
struct UserInfoSnapshot {
    std::string nick;
    std::string ip;
    int user_class{0};
    std::uint64_t share{0};
    std::string description;
    std::string tag;
    std::string speed;
    std::string email;
    std::string country;       ///< Two-letter ISO country code (GeoIP)
    std::string country_name;  ///< Full country name (GeoIP)
    std::string city;          ///< City name (GeoIP)
    std::string client_name;   ///< DC client software extracted from tag
    std::string client_version;///< Client version string (e.g. "2.4.2")
    char mode{'\0'};           ///< 'A' = active, 'P' = passive, '5' = SOCKS5
    int slots{0};              ///< Upload slots
    int hubs_normal{0};        ///< Hubs as normal user
    int hubs_registered{0};    ///< Hubs as registered user
    int hubs_operator{0};      ///< Hubs as operator
    unsigned char status_flag{0}; ///< Status byte (away/TLS/firewall/etc.)
    std::string supports;      ///< Raw $Supports features
    long login_time{0};        ///< Seconds since connection
};

/**
 * Logging configuration
 */
struct LogConfig {
    int log_level{0};
    bool log_to_file{false};
    std::string log_file_path;
};

// ============================================================================
// Event callback types for Python bridge
// ============================================================================

/**
 * Event types that can be sent to Python
 */
enum class HubEventType {
    UserConnect,
    UserDisconnect,
    UserLogin,
    UserLogout,
    ChatMessage,
    PrivateMessage,
    Search,
    Timer,
    HubStarted,
    HubStopping,
    ConfigChanged,
    BanAdded,
    BanRemoved,
    PluginLoaded,
    PluginUnloaded
};

/**
 * Callback interface for events (implemented by Python bridge).
 * 
 * All callbacks should return quickly and not block.
 * Return false from pre-action callbacks to block the action.
 */
class IHubEventCallback {
public:
    virtual ~IHubEventCallback() = default;
    
    // Connection events
    virtual bool OnUserConnect(std::string_view nick, std::string_view ip) { return true; }
    virtual void OnUserDisconnect(std::string_view nick) {}
    
    // Authentication (verlihub-py: delegates to Python/database)
    /**
     * Validate a nickname during NMDC handshake.
     * @return -1 = reject nick, 0 = allow as guest (no password),
     *         1-10 = registered user at this class (password required)
     */
    virtual int OnValidateNick(std::string_view nick, std::string_view ip) { return 0; }
    
    /**
     * Check a user's password.
     * @return -1 = wrong password, 0+ = user class (password accepted)
     */
    virtual int OnCheckPassword(std::string_view nick, std::string_view password) { return -1; }
    
    // Login/logout
    virtual bool OnUserLogin(std::string_view nick, int user_class) { return true; }
    virtual void OnUserLogout(std::string_view nick) {}
    
    // Chat
    virtual bool OnChatMessage(std::string_view nick, std::string_view message) { return true; }
    virtual bool OnPrivateMessage(std::string_view from, std::string_view to, 
                                   std::string_view message) { return true; }
    
    // Search
    virtual bool OnSearch(std::string_view nick, std::string_view query) { return true; }
    
    // Timer
    virtual void OnTimer(std::int64_t timestamp) {}
    
    // Hub lifecycle
    virtual void OnHubStarted() {}
    virtual void OnHubStopping() {}
    
    // Configuration — called by LoadConfiguration() so the Python layer
    // can supply values from the YAML config without env-var indirection.
    /**
     * Return a configuration value for the given section/key.
     * @param section  Config section (e.g. "hub")
     * @param key      Config key   (e.g. "hub_name")
     * @param default_val  Value to return when the Python side has no override
     * @return The value to use (std::string so the lifetime is owned by the caller)
     */
    virtual std::string OnGetConfig(const std::string& section,
                                    const std::string& key,
                                    const std::string& default_val) {
        return default_val;
    }
};

// ============================================================================
// HubContext - Central context object replacing ALL global state
// ============================================================================

/**
 * HubContext - The single source of truth for all hub state.
 * 
 * Design Principles:
 * 1. NO GLOBAL STATE - This object owns everything
 * 2. EXPLICIT PASSING - Must be passed to all functions needing hub access
 * 3. THREAD SAFE - All public methods are safe for concurrent access
 * 4. CLEAR OWNERSHIP - Uses unique_ptr/shared_ptr for owned objects
 * 5. RAII LIFETIME - Created once, destroyed on shutdown
 * 
 * Usage:
 * @code
 *   auto ctx = HubContext::Create("/path/to/config");
 *   if (!ctx->Initialize()) { ... }
 *   ctx->Start(411, "0.0.0.0");
 *   // ... hub runs ...
 *   ctx->Stop();
 * @endcode
 * 
 * This class replaces:
 * - cServerDC::sCurrentServer
 * - cpiPython::me, cpiPython::server
 * - cpiLua::me, cpiLua::server
 * - pending_signal_* global variables
 * - GetCurrentVerlihub() function
 */
class HubContext {
public:
    // =========================================================================
    // Factory and Lifecycle
    // =========================================================================
    
    /**
     * Factory method - the ONLY way to create a HubContext.
     * 
     * @param config_dir Path to verlihub configuration directory
     * @return unique_ptr to new context, or nullptr on failure
     */
    [[nodiscard]] static std::unique_ptr<HubContext> Create(std::string_view config_dir);
    
    // Destructor
    ~HubContext();
    
    // Non-copyable, non-movable (owns unique resources)
    HubContext(const HubContext&) = delete;
    HubContext& operator=(const HubContext&) = delete;
    HubContext(HubContext&&) = delete;
    HubContext& operator=(HubContext&&) = delete;
    
    /**
     * Initialize the hub (load config, connect to DB, etc.)
     * 
     * Must be called after Create() and before Start().
     * 
     * @return true on success
     */
    [[nodiscard]] bool Initialize();
    
    /**
     * Start the hub server.
     * 
     * @param port Port to listen on (0 = use config)
     * @param listen_ip IP to bind to (empty = use config)
     * @return true if started successfully
     */
    [[nodiscard]] bool Start(int port = 0, std::string_view listen_ip = "");
    
    /**
     * Stop the hub server.
     * 
     * Gracefully disconnects all users and stops listening.
     * Blocks until shutdown is complete.
     */
    void Stop();
    
    /**
     * Check if hub is currently running.
     */
    [[nodiscard]] bool IsRunning() const noexcept {
        return m_running.load(std::memory_order_acquire);
    }
    
    /**
     * Get the configuration directory path.
     */
    [[nodiscard]] std::string_view GetConfigDir() const noexcept {
        return m_config_dir;
    }
    
    // =========================================================================
    // Signal Handling (replaces global pending_signal_* variables)
    // =========================================================================
    
    /**
     * Request hub shutdown (e.g., from signal handler).
     * 
     * @param signal_code The signal that triggered shutdown (e.g., SIGTERM)
     */
    void RequestShutdown(int signal_code) noexcept {
        m_shutdown_signal.store(signal_code, std::memory_order_release);
        m_pending_shutdown.store(true, std::memory_order_release);
        m_pending_shutdown.notify_all();
    }
    
    /**
     * Request configuration reload (e.g., SIGHUP).
     */
    void RequestReload() noexcept {
        m_pending_reload.store(true, std::memory_order_release);
        m_pending_reload.notify_all();
    }
    
    /**
     * Check if shutdown has been requested.
     */
    [[nodiscard]] bool HasPendingShutdown() const noexcept {
        return m_pending_shutdown.load(std::memory_order_acquire);
    }
    
    /**
     * Check if reload has been requested.
     */
    [[nodiscard]] bool HasPendingReload() const noexcept {
        return m_pending_reload.load(std::memory_order_acquire);
    }
    
    /**
     * Get shutdown signal code (0 if not set).
     */
    [[nodiscard]] int GetShutdownSignal() const noexcept {
        return m_shutdown_signal.load(std::memory_order_acquire);
    }
    
    /**
     * Clear pending reload flag (after reload is complete).
     */
    void ClearPendingReload() noexcept {
        m_pending_reload.store(false, std::memory_order_release);
    }
    
    // =========================================================================
    // Component Access (non-owning pointers)
    // =========================================================================
    
    /**
     * Get the NMDC hub server instance (verlihub-py).
     * 
     * WARNING: Returns borrowed pointer. Do not store long-term.
     */
    [[nodiscard]] NMDCHubServer* GetNMDCServer() const noexcept {
        return m_nmdc_server;
    }
    
    /**
     * Get the legacy server instance (nullptr in verlihub-py mode).
     * Kept for plugin API compatibility.
     */
    [[nodiscard]] nSocket::cServerDC* GetServer() const noexcept {
        return nullptr;
    }
    
    /**
     * Get the plugin manager.
     */
    [[nodiscard]] nPlugin::cVHPluginMgr* GetPluginManager() const noexcept {
        return m_plugin_mgr;
    }
    
    /**
     * Get the ICU converter for encoding.
     */
    [[nodiscard]] nUtils::cICUConvert* GetICUConverter() const noexcept {
        return m_icu_convert;
    }
    
    /**
     * Get the GeoIP database.
     */
    [[nodiscard]] nUtils::cMaxMindDB* GetGeoIP() const noexcept {
        return m_geoip;
    }
    
    // =========================================================================
    // Thread-Safe User Operations
    // =========================================================================
    
    /**
     * Get current online user count.
     * Delegates to NMDCHubServer when available, falls back to local counter.
     */
    [[nodiscard]] std::size_t GetUserCount() const noexcept;
    
    /**
     * Get snapshot of all online user nicknames.
     * 
     * @return Vector of nicknames (copy, safe to use after call)
     */
    [[nodiscard]] std::vector<std::string> GetUserNicks() const;
    
    /**
     * Find a user by nickname (legacy compatibility, returns opaque cUser*).
     * 
     * WARNING: In verlihub-py mode (NMDCHubServer), always returns nullptr.
     * Use GetUserInfo() or GetUserInfoSnapshots() instead.
     * 
     * @return Pointer to user, or nullptr if not found / not supported
     */
    [[nodiscard]] cUser* FindUser(std::string_view nick) const;
    
    /**
     * Get a snapshot of a single user's info by nick.
     * Thread-safe, returns a copy.
     *
     * @param nick  User's nickname
     * @param[out] out  Filled with user data on success
     * @return true if user was found
     */
    [[nodiscard]] bool GetUserInfo(std::string_view nick, UserInfoSnapshot& out) const;
    
    /**
     * Get snapshots of ALL online users.
     * Thread-safe — copies data under a single lock acquisition.
     *
     * @return Vector of UserInfoSnapshot (empty if hub not running)
     */
    [[nodiscard]] std::vector<UserInfoSnapshot> GetUserInfoSnapshots() const;
    
    /**
     * Execute callback for each online user (thread-safe).
     * 
     * The callback executes with a read lock held.
     * Do not call other HubContext methods from the callback
     * to avoid deadlock.
     */
#ifndef SWIG
    template<typename F>
        requires std::invocable<F, cUser*>
    void ForEachUser(F&& callback) const {
        m_users.ForEach(std::forward<F>(callback));
    }
    
    /**
     * Execute callback for users in class range.
     */
    template<typename F>
        requires std::invocable<F, cUser*>
    void ForEachUserInClass(F&& callback, int min_class, int max_class) const {
        m_users.ForEachInClass(std::forward<F>(callback), min_class, max_class);
    }
#endif  // SWIG
    
    // =========================================================================
    // Thread-Safe Messaging
    // =========================================================================
    
    /**
     * Send a message to a specific user.
     * 
     * @param nick Target user nickname
     * @param message Message to send
     * @return true if user was found and message sent
     */
    [[nodiscard]] bool SendToUser(std::string_view nick, std::string_view message);
    
    /**
     * Broadcast message to all users.
     */
    [[nodiscard]] bool SendToAll(std::string_view message);
    
    /**
     * Send message to users in class range.
     * 
     * @param message Message to send
     * @param min_class Minimum user class (inclusive)
     * @param max_class Maximum user class (inclusive)
     */
    [[nodiscard]] bool SendToClass(std::string_view message, int min_class, int max_class);
    
    /**
     * Send message to operator chat.
     */
    [[nodiscard]] bool SendToOpChat(std::string_view message, std::string_view from = "");
    
    // =========================================================================
    // Thread-Safe User Management
    // =========================================================================
    
    /**
     * Kick a user from the hub.
     * 
     * @param op_nick Operator performing the kick
     * @param nick User to kick
     * @param reason Kick reason
     * @return true if user was found and kicked
     */
    [[nodiscard]] bool KickUser(std::string_view op_nick, std::string_view nick, 
                                 std::string_view reason);
    
    /**
     * Add a robot/bot to the hub.
     * 
     * @param nick Bot nickname
     * @param description Bot description
     * @param user_class Bot's user class
     * @return true if bot was added successfully
     */
    [[nodiscard]] bool AddRobot(std::string_view nick, std::string_view description,
                                 int user_class);
    
    /**
     * Remove a robot/bot.
     */
    [[nodiscard]] bool RemoveRobot(std::string_view nick);
    
    // =========================================================================
    // Thread-Safe Hub Information
    // =========================================================================
    
    /**
     * Get hub name.
     */
    [[nodiscard]] std::string GetHubName() const;
    
    /**
     * Get hub topic.
     */
    [[nodiscard]] std::string GetHubTopic() const;
    
    /**
     * Set hub topic.
     */
    bool SetHubTopic(std::string_view topic);
    
    /**
     * Get total share size.
     * Delegates to NMDCHubServer when available, falls back to local counter.
     */
    [[nodiscard]] std::uint64_t GetTotalShare() const noexcept;
    
    /**
     * Get hub character encoding.
     */
    [[nodiscard]] std::string GetHubEncoding() const;
    
    // =========================================================================
    // Thread-Safe Configuration Access
    // =========================================================================
    
    /**
     * Get a configuration value.
     * 
     * @param section Config section (e.g., "config", "pi_python")
     * @param key Configuration key
     * @param default_val Value to return if not found
     * @return Configuration value or default
     */
    [[nodiscard]] std::string GetConfig(std::string_view section, std::string_view key,
                                         std::string_view default_val = "") const;
    
    /**
     * Set a configuration value.
     * 
     * @return true if value was set successfully
     */
    [[nodiscard]] bool SetConfig(std::string_view section, std::string_view key,
                                  std::string_view value);
    
    /**
     * Get hub configuration structure (snapshot).
     */
    [[nodiscard]] HubConfig GetHubConfig() const;
    
    // =========================================================================
    // Plugin Management (for Python/Lua through SWIG)
    // =========================================================================
    
    /**
     * Load a plugin from a shared library path.
     * 
     * @param plugin_path Path to plugin .so file
     * @return true if plugin was loaded successfully
     */
    [[nodiscard]] bool LoadPlugin(std::string_view plugin_path);
    
    /**
     * Unload a plugin by name.
     * 
     * @param plugin_name Name of the plugin to unload
     * @return true if plugin was unloaded successfully
     */
    [[nodiscard]] bool UnloadPlugin(std::string_view plugin_name);
    
    /**
     * Reload a plugin.
     * 
     * @param plugin_name Name of the plugin to reload
     * @return true if plugin was reloaded successfully
     */
    [[nodiscard]] bool ReloadPlugin(std::string_view plugin_name);
    
    /**
     * Get list of loaded plugins.
     * 
     * @return Vector of plugin information
     */
    [[nodiscard]] std::vector<PluginInfo> GetLoadedPlugins() const;
    
    /**
     * Check if a specific plugin is loaded.
     * 
     * @param plugin_name Name of the plugin
     * @return true if the plugin is loaded
     */
    [[nodiscard]] bool IsPluginLoaded(std::string_view plugin_name) const;
    
    /**
     * Execute a Lua script (requires Lua plugin to be loaded).
     * 
     * @param script_path Path to the Lua script
     * @return true if script executed successfully
     */
    [[nodiscard]] bool ExecuteLuaScript(std::string_view script_path);
    
    /**
     * Unload a Lua script.
     * 
     * @param script_path Path to the Lua script
     * @return true if script was unloaded successfully
     */
    [[nodiscard]] bool UnloadLuaScript(std::string_view script_path);
    
    /**
     * Get list of loaded Lua scripts.
     * 
     * @return Vector of script paths
     */
    [[nodiscard]] std::vector<std::string> GetLoadedLuaScripts() const;
    
    /**
     * Execute a Python script (requires Python plugin to be loaded).
     * 
     * @param script_path Path to the Python script
     * @return true if script executed successfully
     */
    [[nodiscard]] bool ExecutePythonScript(std::string_view script_path);
    
    /**
     * Unload a Python script.
     * 
     * @param script_path Path to the Python script
     * @return true if script was unloaded successfully
     */
    [[nodiscard]] bool UnloadPythonScript(std::string_view script_path);
    
    /**
     * Get list of loaded Python scripts.
     * 
     * @return Vector of script paths
     */
    [[nodiscard]] std::vector<std::string> GetLoadedPythonScripts() const;
    
    // =========================================================================
    // Event Callback Registration (for Python bridge)
    // =========================================================================
    
    /**
     * Set the event callback handler.
     * 
     * Only one handler can be active at a time.
     * Pass nullptr to remove the handler.
     * 
     * @param callback Callback implementation (not owned, must outlive context)
     */
    void SetEventCallback(IHubEventCallback* callback);
    
    /**
     * Fire an event to the callback (if registered).
     * 
     * This is called internally by hub operations.
     */
    void FireEvent(HubEventType type, std::string_view data = "");
    
    // =========================================================================
    // Logging (using C++20 source_location)
    // =========================================================================
    
#ifndef SWIG
    /**
     * Log a message with automatic source location.
     */
    void Log(int level, std::string_view message,
             std::source_location loc = std::source_location::current()) const;
    
    /**
     * Log with format string and arguments.
     * Uses std::format on GCC 13+ and a lightweight fallback on GCC 11/12.
     */
    template<typename... Args>
    void LogFmt(int level, const std::string& fmt_str, Args&&... args) const {
        if (level <= m_log_level.load(std::memory_order_relaxed)) {
            Log(level, vh::fmt(fmt_str, std::forward<Args>(args)...));
        }
    }
#endif  // SWIG

private:
    // =========================================================================
    // Private Constructor (use Create() factory)
    // =========================================================================
    
    explicit HubContext(std::string_view config_dir);
    
    // =========================================================================
    // Internal Helpers
    // =========================================================================
    
    bool LoadConfiguration();
    bool ConnectDatabase();
    bool InitializeComponents();
    void CleanupComponents();
    
    // Timer thread function
    void TimerThreadFunc(std::stop_token stop_token);
    
    // =========================================================================
    // Configuration Directory
    // =========================================================================
    
    std::string m_config_dir;
    
    // =========================================================================
    // Lifecycle State (atomics for lock-free reads)
    // =========================================================================
    
    std::atomic<bool> m_initialized{false};
    std::atomic<bool> m_running{false};
    
    // =========================================================================
    // Signal Handling State (atomics, replaces globals)
    // =========================================================================
    
    std::atomic<bool> m_pending_shutdown{false};
    std::atomic<bool> m_pending_reload{false};
    std::atomic<int> m_shutdown_signal{0};
    
    // =========================================================================
    // Owned Components
    // =========================================================================
    
    /// Database-free NMDC hub server (verlihub-py)
    NMDCHubServer* m_nmdc_server{nullptr};

    /// GeoIP lookup engine (new core, no cServerDC dependency)
    GeoIPLookup* m_geo_lookup{nullptr};

    /// Plugin manager (optional, nullptr if no plugins)
    nPlugin::cVHPluginMgr* m_plugin_mgr{nullptr};
    nUtils::cICUConvert* m_icu_convert{nullptr};
    nUtils::cMaxMindDB* m_geoip{nullptr};
    
    // =========================================================================
    // Thread-Safe User Collection
    // =========================================================================
    
    ThreadSafeUserCollection m_users;
    ThreadSafeUserCollection m_operators;
    ThreadSafeUserCollection m_bots;
    
    // Lock-free counters
    LockFreeCounter<std::size_t> m_user_count{0};
    LockFreeCounter<std::size_t> m_op_count{0};
    LockFreeCounter<std::uint64_t> m_total_share{0};
    
    // =========================================================================
    // Configuration Cache (protected by mutex)
    // =========================================================================
    
    mutable std::shared_mutex m_config_mutex;
    HubConfig m_hub_config;
    
    // =========================================================================
    // Event Callback (protected by mutex)
    // =========================================================================
    
    mutable std::mutex m_callback_mutex;
    IHubEventCallback* m_event_callback{nullptr};
    
    // =========================================================================
    // Logging
    // =========================================================================
    
    std::atomic<int> m_log_level{0};
    mutable std::mutex m_log_mutex;
    
    // =========================================================================
    // Timer Thread (using C++20 jthread for auto-join)
    // =========================================================================
    
    std::jthread m_timer_thread;
    
    // =========================================================================
    // Server Event Loop Thread
    // =========================================================================
    
    std::thread m_server_thread;
};

// ============================================================================
// Helper function to get context from various objects
// ============================================================================

#ifndef SWIG
/**
 * Concept for objects that have a GetContext() method.
 */
template<typename T>
concept HasContext = requires(T& t) {
    { t.GetContext() } -> std::same_as<HubContext&>;
};
#endif  // SWIG

}  // namespace nVerliHub

#endif  // HUB_CONTEXT_H
