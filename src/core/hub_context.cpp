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

#include "hub_context.h"
#include "nmdc_hub_server.h"
#include <iostream>
#include <chrono>
#include <sstream>
#include <fstream>
#include <thread>

namespace nVerliHub {

using namespace std::chrono_literals;

// =============================================================================
// Factory Method
// =============================================================================

std::unique_ptr<HubContext> HubContext::Create(std::string_view config_dir) {
    if (config_dir.empty()) {
        std::cerr << "HubContext::Create: config_dir cannot be empty" << std::endl;
        return nullptr;
    }
    
    // Use new directly since constructor is private
    return std::unique_ptr<HubContext>(new HubContext(config_dir));
}

// =============================================================================
// Constructor / Destructor
// =============================================================================

HubContext::HubContext(std::string_view config_dir)
    : m_config_dir(config_dir)
{
}

HubContext::~HubContext() {
    // Ensure clean shutdown
    if (m_running.load(std::memory_order_acquire)) {
        Stop();
    }
    
    // jthread automatically joins in destructor
    // unique_ptrs automatically delete owned objects
    
    CleanupComponents();
}

// =============================================================================
// Lifecycle
// =============================================================================

bool HubContext::Initialize() {
    if (m_initialized.load(std::memory_order_acquire)) {
        Log(1, "HubContext already initialized");
        return true;
    }
    
    Log(0, "Initializing HubContext...");
    
    // Load configuration
    if (!LoadConfiguration()) {
        Log(0, "Failed to load configuration");
        return false;
    }
    
    // Connect to database
    if (!ConnectDatabase()) {
        Log(0, "Failed to connect to database");
        return false;
    }
    
    // Initialize components
    if (!InitializeComponents()) {
        Log(0, "Failed to initialize components");
        CleanupComponents();
        return false;
    }
    
    m_initialized.store(true, std::memory_order_release);
    Log(0, "HubContext initialized successfully");
    return true;
}

bool HubContext::Start(int port, std::string_view listen_ip) {
    if (!m_initialized.load(std::memory_order_acquire)) {
        Log(0, "Cannot start: HubContext not initialized");
        return false;
    }
    
    if (m_running.load(std::memory_order_acquire)) {
        Log(1, "Hub already running");
        return true;
    }
    
    if (!m_nmdc_server) {
        Log(0, "Cannot start: NMDCHubServer not created");
        return false;
    }
    
    // Use provided values or fall back to config
    int actual_port = (port > 0) ? port : m_hub_config.listen_port;
    std::string actual_ip = listen_ip.empty() 
        ? m_hub_config.listen_ip 
        : std::string(listen_ip);
    
    Log(0, vh::fmt("Starting hub on {}:{}", actual_ip, actual_port));
    
    // Configure the NMDC server
    m_nmdc_server->SetHubName(m_hub_config.hub_name);
    m_nmdc_server->SetHubTopic(m_hub_config.hub_topic);
    m_nmdc_server->SetHubSecurity(m_hub_config.hub_security);
    m_nmdc_server->SetMaxUsers(m_hub_config.max_users);
    m_nmdc_server->SetCallback(m_event_callback);
    
    // Set the listen address on the server
    if (!actual_ip.empty()) {
        m_nmdc_server->mAddr = actual_ip;
    }
    
    // Start listening on the NMDC port
    if (!m_nmdc_server->StartListening(actual_port)) {
        Log(0, vh::fmt("Failed to start listening on port {}", actual_port));
        return false;
    }
    
    Log(0, vh::fmt("Listening on {}:{}", actual_ip, actual_port));
    
    m_running.store(true, std::memory_order_release);
    
    // Run the server event loop in a background thread
    m_server_thread = std::thread([this]() {
        Log(0, "Server event loop starting");
        int result = m_nmdc_server->run();
        Log(0, vh::fmt("Server event loop exited with code {}", result));
        m_running.store(false, std::memory_order_release);
    });
    
    // Start timer thread using C++20 jthread
    m_timer_thread = std::jthread([this](std::stop_token stop_token) {
        TimerThreadFunc(stop_token);
    });
    
    // Notify Python of hub start
    if (m_event_callback) {
        m_event_callback->OnHubStarted();
    }
    
    Log(0, "Hub started successfully");
    return true;
}

void HubContext::Stop() {
    if (!m_running.load(std::memory_order_acquire)) {
        return;
    }
    
    Log(0, "Stopping hub...");
    
    // Notify Python of pending shutdown
    if (m_event_callback) {
        m_event_callback->OnHubStopping();
    }
    
    // Signal timer thread to stop (jthread handles this automatically)
    m_timer_thread.request_stop();
    
    // Wait for timer thread (jthread joins automatically in destructor,
    // but we want to ensure it's done before we clean up)
    if (m_timer_thread.joinable()) {
        m_timer_thread.join();
    }
    
    // Stop the NMDCHubServer event loop
    if (m_nmdc_server) {
        Log(0, "Stopping NMDCHubServer...");
        m_nmdc_server->stop(0, 0);  // Signal the run() loop to exit immediately
    }
    
    // Wait for the server thread to finish
    if (m_server_thread.joinable()) {
        Log(0, "Waiting for server thread to finish...");
        m_server_thread.join();
        Log(0, "Server thread finished");
    }
    
    // Close all connections
    if (m_nmdc_server) {
        m_nmdc_server->close();
    }
    
    m_running.store(false, std::memory_order_release);
    
    Log(0, "Hub stopped");
}

// =============================================================================
// User Operations
// =============================================================================

std::vector<std::string> HubContext::GetUserNicks() const {
    if (m_nmdc_server) {
        return m_nmdc_server->GetNickList();
    }
    return m_users.GetNicks();
}

cUser* HubContext::FindUser(std::string_view nick) const {
    // NMDCHubServer doesn't use cUser objects - return nullptr
    // Use IsNickOnline() or GetUserNicks() instead
    return nullptr;
}

// =============================================================================
// Messaging
// =============================================================================

bool HubContext::SendToUser(std::string_view nick, std::string_view message) {
    if (!m_nmdc_server) return false;
    
    return m_nmdc_server->SendToNick(std::string(nick), std::string(message));
}

bool HubContext::SendToAll(std::string_view message) {
    if (!m_nmdc_server) return false;
    
    m_nmdc_server->SendToAll(std::string(message));
    return true;
}

bool HubContext::SendToClass(std::string_view message, int min_class, int max_class) {
    // NMDCHubServer doesn't have class-based filtering yet
    // For now, send to all
    if (!m_nmdc_server) return false;
    m_nmdc_server->SendToAll(std::string(message));
    return true;
}

bool HubContext::SendToOpChat(std::string_view message, std::string_view from) {
    if (!m_nmdc_server) return false;
    
    std::string from_str(from.empty() ? m_hub_config.hub_security : std::string(from));
    // Send as chat from the hub security bot
    m_nmdc_server->SendChatToAll(from_str, std::string(message));
    return true;
}

// =============================================================================
// User Management
// =============================================================================

bool HubContext::KickUser(std::string_view op_nick, std::string_view nick, 
                          std::string_view reason) {
    if (!m_nmdc_server) return false;
    
    return m_nmdc_server->KickUser(
        std::string(nick), std::string(reason), std::string(op_nick));
}

bool HubContext::AddRobot(std::string_view nick, std::string_view description,
                          int user_class) {
    // In verlihub-py, bots are managed differently
    Log(1, "AddRobot: not implemented in verlihub-py mode");
    return false;
}

bool HubContext::RemoveRobot(std::string_view nick) {
    Log(1, "RemoveRobot: not implemented in verlihub-py mode");
    return false;
}

// =============================================================================
// Hub Information
// =============================================================================

std::string HubContext::GetHubName() const {
    std::shared_lock lock(m_config_mutex);
    return m_hub_config.hub_name;
}

std::string HubContext::GetHubTopic() const {
    std::shared_lock lock(m_config_mutex);
    return m_hub_config.hub_topic;
}

bool HubContext::SetHubTopic(std::string_view topic) {
    {
        std::unique_lock lock(m_config_mutex);
        m_hub_config.hub_topic = topic;
    }
    
    // TODO: Broadcast topic change to users
    return true;
}

std::string HubContext::GetHubEncoding() const {
    std::shared_lock lock(m_config_mutex);
    return m_hub_config.hub_encoding;
}

// =============================================================================
// Configuration
// =============================================================================

std::string HubContext::GetConfig(std::string_view section, std::string_view key,
                                   std::string_view default_val) const {
    // TODO: Implement proper config lookup through database/cache
    std::shared_lock lock(m_config_mutex);
    
    if (section == "config") {
        if (key == "hub_name") return m_hub_config.hub_name;
        if (key == "hub_desc") return m_hub_config.hub_desc;
        if (key == "hub_topic") return m_hub_config.hub_topic;
        if (key == "hub_host") return m_hub_config.hub_host;
        if (key == "hub_owner") return m_hub_config.hub_owner;
        if (key == "hub_encoding") return m_hub_config.hub_encoding;
        if (key == "hub_security") return m_hub_config.hub_security;
        if (key == "opchat_name") return m_hub_config.opchat_name;
    }
    
    return std::string(default_val);
}

bool HubContext::SetConfig(std::string_view section, std::string_view key,
                           std::string_view value) {
    // TODO: Implement proper config setting through database
    std::unique_lock lock(m_config_mutex);
    
    if (section == "config") {
        if (key == "hub_name") { m_hub_config.hub_name = value; return true; }
        if (key == "hub_desc") { m_hub_config.hub_desc = value; return true; }
        if (key == "hub_topic") { m_hub_config.hub_topic = value; return true; }
        if (key == "hub_host") { m_hub_config.hub_host = value; return true; }
        if (key == "hub_owner") { m_hub_config.hub_owner = value; return true; }
        if (key == "hub_encoding") { m_hub_config.hub_encoding = value; return true; }
        if (key == "hub_security") { m_hub_config.hub_security = value; return true; }
        if (key == "opchat_name") { m_hub_config.opchat_name = value; return true; }
    }
    
    return false;
}

HubConfig HubContext::GetHubConfig() const {
    std::shared_lock lock(m_config_mutex);
    return m_hub_config;  // Return copy
}

// =============================================================================
// Event Callback
// =============================================================================

void HubContext::SetEventCallback(IHubEventCallback* callback) {
    std::lock_guard lock(m_callback_mutex);
    m_event_callback = callback;
}

void HubContext::FireEvent(HubEventType type, std::string_view data) {
    std::lock_guard lock(m_callback_mutex);
    if (!m_event_callback) {
        return;
    }
    
    switch (type) {
        case HubEventType::HubStarted:
            m_event_callback->OnHubStarted();
            break;
        case HubEventType::HubStopping:
            m_event_callback->OnHubStopping();
            break;
        // TODO: Handle other event types as needed
        default:
            break;
    }
}

// =============================================================================
// Logging
// =============================================================================

void HubContext::Log(int level, std::string_view message,
                     std::source_location loc) const {
    if (level > m_log_level.load(std::memory_order_relaxed)) {
        return;
    }
    
    std::lock_guard lock(m_log_mutex);
    
    // Get current time
    auto now = std::chrono::system_clock::now();
    auto time_t_now = std::chrono::system_clock::to_time_t(now);
    
    // Format: [timestamp] [level] [file:line] message
    std::cerr << vh::fmt("[{}] [L{}] [{}:{}] {}\n",
                         time_t_now, level,
                         loc.file_name(), loc.line(),
                         message);
}

// =============================================================================
// Private Helpers
// =============================================================================

/// Helper: ask the event callback for a config value; fall back to a compiled
/// default when no callback is present or the callback returns the default.
///
/// This replaces the old EnvOr() approach that read VH_HUB_* environment
/// variables — all configuration now flows through the SWIG director so
/// Python owns the source of truth.

bool HubContext::LoadConfiguration() {
    std::unique_lock lock(m_config_mutex);

    // Lambda wrappers that delegate to the IHubEventCallback director.
    auto cfg = [this](const std::string& key, const std::string& def) -> std::string {
        if (m_event_callback) {
            return m_event_callback->OnGetConfig("hub", key, def);
        }
        return def;
    };

    auto cfgInt = [this](const std::string& key, int def) -> int {
        if (m_event_callback) {
            std::string val = m_event_callback->OnGetConfig("hub", key, std::to_string(def));
            try { return std::stoi(val); } catch (...) {}
        }
        return def;
    };

    m_hub_config = HubConfig{
        .hub_name     = cfg("hub_name",      "Verlihub Hub"),
        .hub_desc     = cfg("hub_desc",       "A Verlihub Hub"),
        .hub_topic    = cfg("hub_topic",      ""),
        .hub_host     = cfg("hub_host",       "localhost"),
        .hub_owner    = cfg("hub_owner",      "admin"),
        .hub_encoding = cfg("hub_encoding",   "UTF-8"),
        .hub_security = cfg("hub_security",   "Hub-Security"),
        .opchat_name  = cfg("opchat_name",    "OpChat"),
        .listen_port  = cfgInt("listen_port",  411),
        .listen_ip    = cfg("listen_ip",      "0.0.0.0"),
        .max_users    = cfgInt("max_users",    1000),
        .min_share    = 0,
        .max_share    = 0,
        .tls_enabled  = false,
        .tls_port     = 0,
        .tls_cert_file = {},
        .tls_key_file  = {}
    };
    
    Log(0, vh::fmt("Config: hub_name='{}', hub_topic='{}'",
                   m_hub_config.hub_name, m_hub_config.hub_topic));

    return true;
}

bool HubContext::ConnectDatabase() {
    // Database connection is handled entirely by the Python side
    // verlihub-py supports SQLite, PostgreSQL, MySQL via Python's async DB layer
    return true;
}

bool HubContext::InitializeComponents() {
    // Create the database-free NMDC hub server
    try {
        Log(0, "Creating NMDCHubServer (database-free mode)");
        m_nmdc_server = new NMDCHubServer(m_config_dir);
        
        if (!m_nmdc_server) {
            Log(0, "Failed to create NMDCHubServer");
            return false;
        }
        
        Log(0, "NMDCHubServer created successfully");
        return true;
    } catch (const std::exception& e) {
        Log(0, std::string("Failed to create NMDCHubServer: ") + e.what());
        return false;
    } catch (...) {
        Log(0, "Failed to create NMDCHubServer: unknown exception");
        return false;
    }
}

void HubContext::CleanupComponents() {
    // Clear cached component pointers
    m_plugin_mgr = nullptr;
    m_icu_convert = nullptr;
    m_geoip = nullptr;
    
    // Delete the NMDC server
    if (m_nmdc_server) {
        delete m_nmdc_server;
        m_nmdc_server = nullptr;
    }
    
    // Clear user collections
    m_users.Clear();
    m_operators.Clear();
    m_bots.Clear();
    
    // Reset counters
    m_user_count.Set(0);
    m_op_count.Set(0);
    m_total_share.Set(0);
}

void HubContext::TimerThreadFunc(std::stop_token stop_token) {
    Log(2, "Timer thread started");
    
    while (!stop_token.stop_requested()) {
        // Wait for 1 second or until stop is requested
        std::this_thread::sleep_for(1s);
        
        if (stop_token.stop_requested()) {
            break;
        }
        
        // Handle pending reload
        if (HasPendingReload()) {
            Log(1, "Processing pending reload...");
            // TODO: Implement reload logic
            ClearPendingReload();
        }
        
        // Fire timer event to Python
        {
            std::lock_guard lock(m_callback_mutex);
            if (m_event_callback) {
                auto now = std::chrono::system_clock::now();
                auto timestamp = std::chrono::duration_cast<std::chrono::seconds>(
                    now.time_since_epoch()).count();
                m_event_callback->OnTimer(timestamp);
            }
        }
    }
    
    Log(2, "Timer thread stopping");
}

// =============================================================================
// Plugin Management
// =============================================================================

bool HubContext::LoadPlugin(std::string_view plugin_path) {
    if (!m_plugin_mgr) {
        Log(0, "Plugin manager not initialized");
        return false;
    }
    
    std::string path_str(plugin_path);
    Log(1, "LoadPlugin: Loading plugin from " + path_str);
    
    // The plugin manager's LoadPlugin accepts a full path to a .so file
    // Plugin system not available in verlihub-py mode
    Log(0, "Plugin system not available in verlihub-py mode");
    return false;
}

bool HubContext::UnloadPlugin(std::string_view plugin_name) {
    Log(0, "Plugin system not available in verlihub-py mode");
    return false;
}

bool HubContext::ReloadPlugin(std::string_view plugin_name) {
    Log(0, "Plugin system not available in verlihub-py mode");
    return false;
}

std::vector<PluginInfo> HubContext::GetLoadedPlugins() const {
    return {};
}

bool HubContext::IsPluginLoaded(std::string_view plugin_name) const {
    return false;
}

bool HubContext::ExecuteLuaScript(std::string_view script_path) {
    Log(0, "Lua plugin not available in verlihub-py mode");
    return false;
}

bool HubContext::UnloadLuaScript(std::string_view script_path) {
    Log(0, "Lua plugin not available in verlihub-py mode");
    return false;
}

std::vector<std::string> HubContext::GetLoadedLuaScripts() const {
    return {};
}

bool HubContext::ExecutePythonScript(std::string_view script_path) {
    Log(0, "Python plugin not available in verlihub-py mode");
    return false;
}

bool HubContext::UnloadPythonScript(std::string_view script_path) {
    Log(0, "Python plugin not available in verlihub-py mode");
    return false;
}

std::vector<std::string> HubContext::GetLoadedPythonScripts() const {
    return {};
}

}  // namespace nVerliHub
