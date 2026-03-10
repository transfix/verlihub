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

#include <string>
#include <unordered_map>
#include <vector>
#include <mutex>
#include <atomic>
#include <memory>
#include <chrono>

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

    // =========================================================================
    // Internal Helpers
    // =========================================================================

    /// Send data to a specific connection (appends | delimiter)
    void SendToConn(nSocket::cAsyncConn* conn, const std::string& data);

    /// Send data to all logged-in connections (appends | delimiter)
    void SendToAllConns(const std::string& data);

    /// Remove a client from all maps and notify others
    void RemoveClient(nSocket::cAsyncConn* conn);

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
    // Connection Factory
    // =========================================================================

    /// Our connection factory (owned, installed as mFactory on base class)
    std::unique_ptr<NMDCConnFactory> m_conn_factory;
};

}  // namespace nVerliHub

#endif  // NMDC_HUB_SERVER_H
