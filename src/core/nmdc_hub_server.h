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

#ifndef NMDC_HUB_SERVER_H
#define NMDC_HUB_SERVER_H

/**
 * @file nmdc_hub_server.h
 * @brief NMDC hub server for verlihub-py.
 *
 * NMDCHubServer inherits from cAsyncSocketServer to get the proven
 * socket I/O infrastructure (poll/select, connection management,
 * non-blocking I/O) while implementing NMDC protocol handling.
 *
 * All authentication and persistence decisions are delegated to
 * Python through IHubEventCallback (required). This allows verlihub-py
 * to support SQLite, PostgreSQL, MySQL, or any other database backend
 * managed by the Python layer.
 */

#include "casyncsocketserver.h"
#include "casyncconn.h"
#include "nmdc_protocol.h"
#include "geo_ip_lookup.h"
#include "czlib.h"

#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <mutex>
#include <atomic>
#include <memory>
#include <chrono>
#include <array>

namespace nVerliHub {

// Forward declarations
struct UserInfoSnapshot;
class IHubEventCallback;
class NMDCHubServer;

// ============================================================================
// Connection Factory for NMDCHubServer
// ============================================================================

/**
 * Connection factory that creates plain cAsyncConn objects and notifies
 * the NMDCHubServer when connections are deleted (disconnected).
 *
 * This is essential because cAsyncSocketServer::delConnection() is NOT
 * virtual, so we can't override it. Instead, the factory's DeleteConn()
 * is called just before the connection object is freed.
 */
class NMDCConnFactory : public nSocket::cConnFactory {
public:
    explicit NMDCConnFactory(NMDCHubServer* server);
    ~NMDCConnFactory() override = default;

    nSocket::cAsyncConn* CreateConn(nSocket::tSocket sd = 0) override;
    void DeleteConn(nSocket::cAsyncConn*& conn) override;

private:
    NMDCHubServer* m_server;
};

// ============================================================================
// Flood Protection Types
// ============================================================================

/// Message types subject to rate limiting
enum class FloodType : int {
    Chat = 0,
    PM,
    Search,
    MyINFO,
    CTM,      ///< $ConnectToMe + $RevConnectToMe
    ExtJSON,  ///< $ExtJSON protocol extensions
    Count     ///< Sentinel — number of flood types
};

/// Configuration for one flood type: token-bucket parameters
struct FloodLimit {
    int period_ms{1000};   ///< Refill period in milliseconds
    int max_tokens{5};      ///< Max tokens (burst capacity)
};

/// Per-client token-bucket state for one flood type
struct FloodBucket {
    int tokens{0};
    std::chrono::steady_clock::time_point last_refill;
};

/// Per-client aggregate flood state (all types)
struct FloodState {
    std::array<FloodBucket, static_cast<size_t>(FloodType::Count)> buckets;

    void Init(const std::array<FloodLimit, static_cast<size_t>(FloodType::Count)>& limits) {
        auto now = std::chrono::steady_clock::now();
        for (size_t i = 0; i < buckets.size(); ++i) {
            buckets[i].tokens = limits[i].max_tokens;
            buckets[i].last_refill = now;
        }
    }
};

// ============================================================================
// NMDC Connection State
// ============================================================================

/// State machine for NMDC client handshake
enum class NMDCConnState {
    Connected,          ///< Just connected, $Lock sent
    WaitingKey,         ///< Waiting for $Key response
    WaitingValidateNick,///< Got $Key, waiting for $ValidateNick
    WaitingMyPass,      ///< Nick requires password, waiting for $MyPass
    WaitingMyINFO,      ///< Nick validated, waiting for $MyINFO
    LoggedIn,           ///< Fully logged in and in user list
    Closing             ///< Being disconnected
};

// ============================================================================
// NMDC Client Info (in-memory state per connection)
// ============================================================================

/// In-memory representation of a connected NMDC client
struct NMDCClient {
    nSocket::cAsyncConn* conn{nullptr};
    NMDCConnState state{NMDCConnState::Connected};
    std::string nick;
    std::string ip;
    std::string myinfo_raw;  ///< Raw $MyINFO string to broadcast
    NMDCProtocol::MyINFOData myinfo;
    int user_class{0};       ///< 0=guest, 1=reg, 3=vip, 5=op, 10=admin
    std::string country_code; ///< Two-letter ISO country code (GeoIP lookup on login)
    std::string country_name; ///< Full country name (GeoIP)
    std::string city;         ///< City name (GeoIP)
    std::string lock;        ///< Lock string sent to this client
    int login_attempts{0};   ///< Password attempt counter
    std::chrono::steady_clock::time_point connect_time; ///< When the client connected

    // ----- Parsed from tag -----
    std::string client_version; ///< Client version (e.g. "2.4.2")
    char mode{'\0'};            ///< 'A' = active, 'P' = passive, '5' = SOCKS5
    int slots{0};               ///< Upload slots
    int hubs_normal{0};         ///< Hubs as normal user
    int hubs_registered{0};     ///< Hubs as registered user
    int hubs_operator{0};       ///< Hubs as operator

    // ----- Parsed from $Supports / $MyINFO -----
    unsigned char status_flag{0}; ///< Status byte from MyINFO speed field
    std::string supports_text;    ///< Raw $Supports features string
    bool supports_extjson{false}; ///< Client supports ExtJSON2
    bool supports_huburl{false};  ///< Client supports HubURL
    bool supports_in{false};      ///< Client supports IN (incremental info)
    bool supports_zlib{false};    ///< Client supports ZPipe0 (ZLib compression)
    std::string hub_url;          ///< Reported hub URL from $MyHubURL
    std::string ext_json;         ///< Last $ExtJSON payload

    // ----- Flood Protection -----
    FloodState flood;              ///< Token-bucket state per message type
    int flood_warnings{0};         ///< Consecutive flood warnings (disconnect threshold)
};

// ============================================================================
// NMDCHubServer - NMDC protocol hub
// ============================================================================

/**
 * NMDC hub server for verlihub-py.
 *
 * Inherits cAsyncSocketServer for socket infrastructure and
 * implements NMDC protocol handling. All database operations
 * (user auth, bans, config persistence) are delegated to Python
 * via IHubEventCallback, which MUST be set before starting.
 *
 * Thread safety: The server event loop runs in a single thread
 * (via run()). Python callbacks are invoked from that thread.
 * The public SendTo* methods are safe to call from any thread
 * as they write to the socket buffers (thread-safe in cAsyncConn).
 */
class NMDCHubServer : public nSocket::cAsyncSocketServer {
public:
    /**
     * Construct the hub server.
     *
     * @param config_dir Configuration directory (not used for DB,
     *                   but kept for consistency and logging)
     */
    explicit NMDCHubServer(const std::string& config_dir = ".");

    ~NMDCHubServer() override;

    // Non-copyable
    NMDCHubServer(const NMDCHubServer&) = delete;
    NMDCHubServer& operator=(const NMDCHubServer&) = delete;

    // =========================================================================
    // Configuration (call before StartListening)
    // =========================================================================

    void SetHubName(const std::string& name) { m_hub_name = name; }
    void SetHubTopic(const std::string& topic) {
        m_hub_topic = topic;
        // Broadcast topic change to all connected users
        std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
        if (!topic.empty()) {
            SendToAllConns(NMDCProtocol::MakeHubTopic(topic));
        }
        // Update $HubName to include new topic
        SendToAllConns(NMDCProtocol::MakeHubNameWithTopic(m_hub_name, topic));
    }
    void SetHubSecurity(const std::string& name) { m_hub_security = name; }
    void SetOpChatName(const std::string& name) { m_opchat_name = name; }
    void SetMaxUsers(int max) { m_max_users = max; }
    void SetMOTD(const std::string& motd) { m_motd = motd; }

    const std::string& GetHubName() const { return m_hub_name; }
    const std::string& GetHubTopic() const { return m_hub_topic; }
    const std::string& GetHubSecurity() const { return m_hub_security; }

    // =========================================================================
    // Flood Protection Configuration
    // =========================================================================

    /**
     * Set the rate limit for a specific message type.
     * @param type     Which message type to configure
     * @param period_ms  Token refill period in milliseconds
     * @param max_tokens Maximum tokens (burst capacity)
     */
    void SetFloodConfig(FloodType type, int period_ms, int max_tokens);

    /**
     * Get the current flood limit for a message type.
     */
    FloodLimit GetFloodConfig(FloodType type) const;

    /**
     * Set the maximum flood warnings before automatic disconnect.
     * Default is 3.
     */
    void SetMaxFloodWarnings(int max) { m_max_flood_warnings = max; }

    // =========================================================================
    // Ban Cache (fast-path IP/nick rejection)
    // =========================================================================

    /**
     * Load the ban cache from Python-provided sets.
     * Replaces the current cache atomically.
     */
    void LoadBanCache(const std::vector<std::string>& ips,
                      const std::vector<std::string>& nicks);

    /**
     * Add a single entry to the ban cache.
     */
    void AddBanCacheIP(const std::string& ip);
    void AddBanCacheNick(const std::string& nick);

    /**
     * Remove a single entry from the ban cache.
     */
    void RemoveBanCacheIP(const std::string& ip);
    void RemoveBanCacheNick(const std::string& nick);

    /**
     * Clear the entire ban cache.
     */
    void ClearBanCache();

    // =========================================================================
    // ZLib Compression Configuration
    // =========================================================================

    /**
     * Enable/disable ZLib compression for clients that support ZPipe0.
     * When enabled, large outbound data is compressed before sending.
     */
    void SetZLibEnabled(bool enabled) { m_zlib_enabled = enabled; }
    bool IsZLibEnabled() const { return m_zlib_enabled; }

    /**
     * Set minimum data size (bytes) before compression is attempted.
     * Default is 128 bytes.
     */
    void SetZLibMinSize(size_t min_size) { m_zlib_min_size = min_size; }
    size_t GetZLibMinSize() const { return m_zlib_min_size; }

    // =========================================================================
    // Event Callback (Python bridge)
    // =========================================================================

    /**
     * Set the event callback handler for auth and event notifications.
     * MUST be set before StartListening — the hub refuses connections
     * without a callback because auth decisions require it.
     */
    void SetCallback(IHubEventCallback* cb);

    /// Check if a callback is set
    bool HasCallback() const { return m_callback != nullptr; }

    /**
     * Set the GeoIP lookup engine.  Owned externally (by HubContext).
     * When set, country codes are resolved on user login.
     */
    void SetGeoIP(GeoIPLookup* geo) { m_geoip = geo; }

    // =========================================================================
    // Messaging (thread-safe, can be called from Python threads)
    // =========================================================================

    /// Send raw NMDC message to a specific user by nick
    bool SendToNick(const std::string& nick, const std::string& data);

    /// Broadcast raw NMDC message to all logged-in users
    void SendToAll(const std::string& data);

    /// Send chat message from hub bot to all users
    void SendChatToAll(const std::string& from, const std::string& message);

    /// Send private message
    bool SendPM(const std::string& from, const std::string& to,
                const std::string& message);

    // =========================================================================
    // Active / Passive Messaging (thread-safe)
    // =========================================================================

    /// Send raw NMDC message to all active-mode users
    void SendToActive(const std::string& data);

    /// Send raw NMDC message to all passive-mode users
    void SendToPassive(const std::string& data);

    /// Send raw NMDC message to active-mode users in a class range
    void SendToActiveClass(const std::string& data, int min_class, int max_class);

    /// Send raw NMDC message to passive-mode users in a class range
    void SendToPassiveClass(const std::string& data, int min_class, int max_class);

    /// Get count of active-mode users
    size_t GetActiveUserCount() const;

    /// Get count of passive-mode users
    size_t GetPassiveUserCount() const;

    // =========================================================================
    // User Information (thread-safe reads)
    // =========================================================================

    /// Get list of all logged-in user nicks
    std::vector<std::string> GetNickList() const;

    /// Get list of operator nicks
    std::vector<std::string> GetOpList() const;

    /// Get current user count
    size_t GetUserCount() const;

    /// Check if a nick is online
    bool IsNickOnline(const std::string& nick) const;

    /// Get total share in bytes
    uint64_t GetTotalShare() const;

    /// Get snapshot of a single user by nick (thread-safe copy)
    bool GetUserInfo(const std::string& nick, UserInfoSnapshot& out) const;

    /// Get snapshots of ALL logged-in users (thread-safe, single lock)
    std::vector<UserInfoSnapshot> GetUserInfoSnapshots() const;

    // =========================================================================
    // User Management
    // =========================================================================

    /// Kick a user by nick (uses configured hub security bot name as default op)
    bool KickUser(const std::string& nick, const std::string& reason,
                  const std::string& op = "");

    /// Disconnect a user by nick (no message, just close)
    bool DisconnectUser(const std::string& nick);

    /// Force-move a user to another hub address
    bool ForceMove(const std::string& nick, const std::string& address);

    // =========================================================================
    // Protocol Statistics
    // =========================================================================

    /// Message count per command type
    struct ProtocolStats {
        std::atomic<uint64_t> messages_in{0};   ///< Total messages received
        std::atomic<uint64_t> messages_out{0};   ///< Total messages sent
        std::atomic<uint64_t> chat_count{0};
        std::atomic<uint64_t> pm_count{0};
        std::atomic<uint64_t> search_count{0};
        std::atomic<uint64_t> myinfo_count{0};
        std::atomic<uint64_t> ctm_count{0};
        std::atomic<uint64_t> sr_count{0};
        std::atomic<uint64_t> mcto_count{0};
        std::atomic<uint64_t> flood_blocked{0};  ///< Messages blocked by flood limiter
        std::atomic<uint64_t> ban_blocked{0};     ///< Connections blocked by ban cache
    };

    /// Get protocol statistics (snapshot)
    struct ProtocolStatsSnapshot {
        uint64_t messages_in{0};
        uint64_t messages_out{0};
        uint64_t chat_count{0};
        uint64_t pm_count{0};
        uint64_t search_count{0};
        uint64_t myinfo_count{0};
        uint64_t ctm_count{0};
        uint64_t sr_count{0};
        uint64_t mcto_count{0};
        uint64_t flood_blocked{0};
        uint64_t ban_blocked{0};
    };

    ProtocolStatsSnapshot GetProtocolStats() const;

    // =========================================================================
    // Runtime Configuration Setters (thread-safe, called from Python)
    // =========================================================================

    /// Set the login timeout in seconds
    void SetLoginTimeout(int seconds) { m_login_timeout_sec = seconds; }

    /// Set the maximum number of password attempts before disconnect
    void SetMaxLoginAttempts(int attempts) { m_max_login_attempts = attempts; }

protected:
    // =========================================================================
    // cAsyncSocketServer overrides
    // =========================================================================

    /// Called when a new TCP connection is accepted
    int OnNewConn(nSocket::cAsyncConn* conn) override;

    /// Called when a complete NMDC message is received (delimited by |)
    void OnNewMessage(nSocket::cAsyncConn* conn, std::string* msg) override;

    /// Called periodically (every ~1 second)
    int OnTimer(const nSocket::cTime& now) override;

public:
    /**
     * Called by NMDCConnFactory::DeleteConn() when a connection is about
     * to be freed. Cleans up our client/nick maps before the pointer
     * becomes invalid.  Must be public so the factory can call it.
     */
    void OnClientDeleted(nSocket::cAsyncConn* conn);

private:
    // =========================================================================
    // NMDC Protocol Handlers
    // =========================================================================

    void HandleSupports(NMDCClient& client, const std::string& msg);
    void HandleKey(NMDCClient& client, const std::string& msg);
    void HandleValidateNick(NMDCClient& client, const std::string& msg);
    void HandleMyPass(NMDCClient& client, const std::string& msg);
    void HandleMyINFO(NMDCClient& client, const std::string& msg);
    void HandleGetNickList(NMDCClient& client);
    void HandleChat(NMDCClient& client, const std::string& msg);
    void HandlePrivateMessage(NMDCClient& client, const std::string& msg);
    void HandleSearch(NMDCClient& client, const std::string& msg);
    void HandleConnectToMe(NMDCClient& client, const std::string& msg);
    void HandleRevConnectToMe(NMDCClient& client, const std::string& msg);
    void HandleSR(NMDCClient& client, const std::string& msg);
    void HandleQuit(NMDCClient& client);
    void HandleMCTo(NMDCClient& client, const std::string& msg);
    void HandleUserIP(NMDCClient& client, const std::string& msg);
    void HandleWhoIP(NMDCClient& client, const std::string& msg);
    void HandleOpForceMove(NMDCClient& client, const std::string& msg);
    void HandleExtJSON(NMDCClient& client, const std::string& msg);
    void HandleMyHubURL(NMDCClient& client, const std::string& msg);
    void HandleIN(NMDCClient& client, const std::string& msg);

    // =========================================================================
    // Internal Helpers
    // =========================================================================

    /// Send data to a specific connection (appends | delimiter)
    void SendToConn(nSocket::cAsyncConn* conn, const std::string& data);

    /// Send data with optional ZLib compression for clients that support ZPipe0
    void SendToConnCompressed(NMDCClient& client, const std::string& data);

    /// Send data to all logged-in connections (appends | delimiter)
    void SendToAllConns(const std::string& data);

    /// Send data to connections matching a mode and optional class range
    void SendToConnsFiltered(const std::string& data, char mode_filter,
                             int min_class = 0, int max_class = 10);

    /// Remove a client from all maps and notify others
    void RemoveClient(nSocket::cAsyncConn* conn);

    /// Check token-bucket flood limiter; returns true if message is allowed
    bool CheckFlood(NMDCClient& client, FloodType type);

    /// Check if an IP is in the ban cache
    bool IsIPBanned(const std::string& ip) const;

    /// Check if a nick is in the ban cache
    bool IsNickBanned(const std::string& nick) const;

    /// Build $NickList and $OpList and send to a client
    void SendUserLists(NMDCClient& client);

    /// Announce a new user to all existing users
    void AnnounceNewUser(const NMDCClient& client);

    /// Send the hub bot's $MyINFO to a client
    void SendHubBotInfo(NMDCClient& client);

    /// Send the MOTD (Message of the Day) to a newly logged-in client
    void SendMOTD(NMDCClient& client);

    // =========================================================================
    // State
    // =========================================================================

    /// Map from connection pointer to client info
    std::unordered_map<nSocket::cAsyncConn*, NMDCClient> m_clients;

    /// Map from nick to connection pointer (for fast nick lookup)
    std::unordered_map<std::string, nSocket::cAsyncConn*> m_nick_to_conn;

    /// Mutex for client maps (protects m_clients and m_nick_to_conn).
    /// Recursive because OnNewMessage holds the lock while dispatching to
    /// Handle* methods, whose director callbacks (OnUserConnect etc.) may
    /// call GetUserInfo/GetUserInfoSnapshots which also lock this mutex.
    mutable std::recursive_mutex m_clients_mutex;

    /// Event callback (Python bridge, not owned)
    IHubEventCallback* m_callback{nullptr};

    /// GeoIP lookup engine (not owned, set by HubContext)
    GeoIPLookup* m_geoip{nullptr};

    // =========================================================================
    // Hub Configuration (in-memory, DB access via Python callback)
    // =========================================================================

    std::string m_hub_name{"Verlihub Hub"};
    std::string m_hub_topic;
    std::string m_hub_security{"Hub-Security"};
    std::string m_opchat_name{"OpChat"};  ///< Operator chat bot nick
    std::string m_motd;  ///< Message of the Day (sent to users on login)
    int m_max_users{1000};
    int m_max_login_attempts{3};
    int m_login_timeout_sec{60};  ///< Seconds to complete login before disconnect

    // =========================================================================
    // Counters
    // =========================================================================

    std::atomic<size_t> m_user_count{0};
    std::atomic<uint64_t> m_total_share{0};

    // =========================================================================
    // Flood Protection State
    // =========================================================================

    /// Per-type flood limits (token bucket parameters)
    std::array<FloodLimit, static_cast<size_t>(FloodType::Count)> m_flood_limits{{
        {1000, 5},   // Chat:    5 msgs / 1s
        {1000, 5},   // PM:      5 msgs / 1s
        {5000, 5},   // Search:  5 searches / 5s
        {5000, 2},   // MyINFO:  2 updates / 5s
        {1000, 10},  // CTM:     10 CTM/RCTM / 1s
        {5000, 3},   // ExtJSON: 3 updates / 5s
    }};

    /// Maximum flood warnings before auto-disconnect
    int m_max_flood_warnings{3};

    // =========================================================================
    // Ban Cache State
    // =========================================================================

    std::unordered_set<std::string> m_banned_ips;
    std::unordered_set<std::string> m_banned_nicks;
    mutable std::mutex m_ban_cache_mutex;

    // =========================================================================
    // ZLib Compression State
    // =========================================================================

    /// Whether ZLib compression is enabled for clients that support ZPipe0
    bool m_zlib_enabled{false};

    /// Minimum data size (bytes) before compression is attempted
    size_t m_zlib_min_size{128};

    /// ZLib compressor instance (lazy-initialized)
    std::unique_ptr<nUtils::cZLib> m_zlib;

    // =========================================================================
    // Connection Factory
    // =========================================================================

    /// Our connection factory (owned, installed as mFactory on base class)
    std::unique_ptr<NMDCConnFactory> m_conn_factory;

    // =========================================================================
    // Protocol Statistics
    // =========================================================================

    ProtocolStats m_proto_stats;
};

}  // namespace nVerliHub

#endif  // NMDC_HUB_SERVER_H
