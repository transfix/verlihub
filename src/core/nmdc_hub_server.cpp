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

#include "nmdc_hub_server.h"
#include "hub_context.h"  // For IHubEventCallback
#include <zlib.h>
#include <iostream>
#include <algorithm>
#include <chrono>
#include <sstream>

namespace nVerliHub {

using namespace nSocket;
using namespace nEnums;

// =============================================================================
// Helpers
// =============================================================================

/// Extract DC client name from NMDC tag like "<EiskaltDC++ V:2.4.2,M:A,...>"
static std::string ExtractClientName(const std::string& tag) {
    if (tag.size() < 3 || tag.front() != '<') return {};
    // Find " V:" which separates name from version
    auto pos = tag.find(" V:");
    if (pos == std::string::npos) {
        // No version info — try up to closing >
        pos = tag.find('>');
        if (pos == std::string::npos) return {};
        return tag.substr(1, pos - 1);
    }
    return tag.substr(1, pos - 1);  // skip leading '<'
}

// =============================================================================
// Constructor / Destructor
// =============================================================================

NMDCHubServer::NMDCHubServer(const std::string& config_dir)
    : cAsyncSocketServer(config_dir)
{
    // Set reasonable defaults for the socket server
    mStepDelay = 1;          // 1ms main loop delay
    mMaxLineLength = 102400; // 100KB max line length
    mNoConnDelay = 100;      // 100us delay when no connections
    mChooseTimeOut = 4;      // 4ms poll timeout
    mAcceptNum = 10;         // Accept up to 10 connections per cycle
    mAcceptTry = 3;          // 3 accept retries
    mNoReadTry = 5;          // 5 read retries
    mNoReadDelay = 10;       // 10us read delay

    // Install our connection factory so we get notified when connections
    // are deleted (since delConnection is not virtual).
    m_conn_factory = std::make_unique<NMDCConnFactory>(this);
    mFactory = m_conn_factory.get();
}

NMDCHubServer::~NMDCHubServer() {
    // Clear factory before base destructor runs to avoid dangling pointer
    mFactory = nullptr;
}

// =============================================================================
// Callback (Python bridge)
// =============================================================================

void NMDCHubServer::SetCallback(IHubEventCallback* cb) {
    if (!cb) {
        throw std::invalid_argument(
            "NMDCHubServer::SetCallback: callback must not be null — "
            "verlihub-py requires the Python event handler for auth");
    }
    m_callback = cb;
}

// =============================================================================
// NMDCConnFactory implementation
// =============================================================================

NMDCConnFactory::NMDCConnFactory(NMDCHubServer* server)
    : cConnFactory(nullptr), m_server(server)
{}

cAsyncConn* NMDCConnFactory::CreateConn(tSocket sd) {
    auto* conn = new cAsyncConn((int)sd, m_server, eCT_CLIENT);
    conn->mxMyFactory = this;
    return conn;
}

void NMDCConnFactory::DeleteConn(cAsyncConn*& conn) {
    if (conn && m_server) {
        m_server->OnClientDeleted(conn);
    }
    delete conn;
    conn = nullptr;
}

// =============================================================================
// cAsyncSocketServer Overrides
// =============================================================================

int NMDCHubServer::OnNewConn(cAsyncConn* conn) {
    if (!conn) return -1;

    // Refuse all connections if Python callback is not wired.
    // This should never happen in verlihub-py — SetCallback is called
    // during Start() before the listener opens.
    if (!m_callback) {
        return -1;
    }

    std::string ip = conn->AddrIP();

    // Fast-path ban cache check: reject known-banned IPs immediately
    if (IsIPBanned(ip)) {
        m_proto_stats.ban_blocked.fetch_add(1, std::memory_order_relaxed);
        return -1;  // Reject silently
    }

    // Check max users
    if (m_user_count.load(std::memory_order_relaxed) >= 
        static_cast<size_t>(m_max_users)) {
        std::string msg = NMDCProtocol::MakeHubIsFull();
        conn->Write(msg + "|", true);
        return -1;  // Reject connection
    }

    // Create client state
    NMDCClient client;
    client.conn = conn;
    client.ip = ip;
    client.lock = NMDCProtocol::GenerateLock();
    client.state = NMDCConnState::WaitingKey;
    client.connect_time = std::chrono::steady_clock::now();
    client.flood.Init(m_flood_limits);

    // Send $Lock
    std::string lock_msg = NMDCProtocol::MakeLock(client.lock);
    conn->Write(lock_msg + "|", true);

    // Store client
    {
        std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
        m_clients[conn] = std::move(client);
    }

    return 0;
}

void NMDCHubServer::OnNewMessage(cAsyncConn* conn, std::string* msg) {
    if (!conn || !msg) {
        delete msg;
        return;
    }

    std::string message = *msg;
    delete msg;

    if (message.empty()) return;

    // Count incoming messages
    m_proto_stats.messages_in.fetch_add(1, std::memory_order_relaxed);

    // Find the client
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    auto it = m_clients.find(conn);
    if (it == m_clients.end()) return;

    NMDCClient& client = it->second;

    // Route based on message type
    if (NMDCProtocol::IsCommand(message, "$Key")) {
        HandleKey(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$Supports")) {
        HandleSupports(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$ValidateNick")) {
        HandleValidateNick(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$MyPass")) {
        HandleMyPass(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$MyINFO")) {
        if (!CheckFlood(client, FloodType::MyINFO)) return;
        HandleMyINFO(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$GetNickList")) {
        HandleGetNickList(client);
    } else if (NMDCProtocol::IsCommand(message, "$MCTo:")) {
        if (!CheckFlood(client, FloodType::PM)) return;
        HandleMCTo(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$To:")) {
        if (!CheckFlood(client, FloodType::PM)) return;
        HandlePrivateMessage(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$Search")) {
        if (!CheckFlood(client, FloodType::Search)) return;
        HandleSearch(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$ConnectToMe")) {
        if (!CheckFlood(client, FloodType::CTM)) return;
        HandleConnectToMe(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$RevConnectToMe")) {
        if (!CheckFlood(client, FloodType::CTM)) return;
        HandleRevConnectToMe(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$SR")) {
        HandleSR(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$Quit")) {
        HandleQuit(client);
    } else if (NMDCProtocol::IsCommand(message, "$UserIP")) {
        HandleUserIP(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$WhoIP")) {
        HandleWhoIP(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$OpForceMove")) {
        HandleOpForceMove(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$ExtJSON")) {
        if (!CheckFlood(client, FloodType::ExtJSON)) return;
        HandleExtJSON(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$MyHubURL")) {
        HandleMyHubURL(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$IN")) {
        if (!CheckFlood(client, FloodType::ExtJSON)) return;
        HandleIN(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$Version")) {
        // Ignore version announcements
    } else if (NMDCProtocol::IsCommand(message, "$GetINFO")) {
        // Ignore GetINFO (we send NoGetINFO in $Supports)
    } else if (NMDCProtocol::IsCommand(message, "$BotINFO")) {
        // Ignore bot info requests for now
    } else if (NMDCProtocol::IsCommand(message, "$HubINFO")) {
        // Ignore hub info requests for now
    } else if (!message.empty() && message[0] == '<') {
        if (!CheckFlood(client, FloodType::Chat)) return;
        HandleChat(client, message);
    }
    // Silently ignore unknown commands
}

void NMDCHubServer::OnClientDeleted(cAsyncConn* conn) {
    if (!conn) return;

    // Remove client and notify others
    RemoveClient(conn);
}

int NMDCHubServer::OnTimer(const cTime& now) {
    // Login timeout: disconnect clients stuck in handshake
    if (m_login_timeout_sec > 0) {
        auto tp_now = std::chrono::steady_clock::now();
        std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);

        std::vector<cAsyncConn*> to_disconnect;
        for (auto& [conn, client] : m_clients) {
            if (client.state != NMDCConnState::LoggedIn &&
                client.state != NMDCConnState::Closing) {
                auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(
                    tp_now - client.connect_time).count();
                if (elapsed > m_login_timeout_sec) {
                    to_disconnect.push_back(conn);
                }
            }
        }

        for (auto* conn : to_disconnect) {
            auto it = m_clients.find(conn);
            if (it != m_clients.end()) {
                it->second.state = NMDCConnState::Closing;
                conn->CloseNice(100);
            }
        }
    }

    return 0;
}

// =============================================================================
// Protocol Handlers
// =============================================================================

void NMDCHubServer::HandleSupports(NMDCClient& client, const std::string& msg) {
    // Store the client's supported features
    std::string features = NMDCProtocol::GetCommandParam(msg, "$Supports");
    client.supports_text = features;

    // Parse individual feature flags
    client.supports_extjson = (features.find("ExtJSON2") != std::string::npos);
    client.supports_huburl  = (features.find("HubURL")   != std::string::npos);
    client.supports_in      = (features.find("IN")        != std::string::npos);
    client.supports_zlib    = (features.find("ZPipe0")    != std::string::npos);

    // Send our supports back
    SendToConn(client.conn, NMDCProtocol::MakeSupports());
}

void NMDCHubServer::HandleKey(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::WaitingKey) return;

    // Verify the key matches our lock
    std::string expected_key = NMDCProtocol::Lock2Key(client.lock);
    std::string received_key = NMDCProtocol::GetCommandParam(msg, "$Key");

    // Key validation: we accept it even if it doesn't match perfectly
    // (many clients have slightly different implementations)
    // The important thing is that they responded to our $Lock
    client.state = NMDCConnState::WaitingValidateNick;
}

void NMDCHubServer::HandleValidateNick(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::WaitingValidateNick &&
        client.state != NMDCConnState::WaitingKey) {
        // Some clients send ValidateNick before or simultaneously with Key
        // Be lenient
    }

    std::string nick = NMDCProtocol::GetCommandParam(msg, "$ValidateNick");
    if (nick.empty()) {
        SendToConn(client.conn, NMDCProtocol::MakeValidateDenide(""));
        client.state = NMDCConnState::Closing;
        return;
    }

    // Fast-path ban cache check: reject known-banned nicks
    if (IsNickBanned(nick)) {
        m_proto_stats.ban_blocked.fetch_add(1, std::memory_order_relaxed);
        SendToConn(client.conn, NMDCProtocol::MakeValidateDenide(nick));
        client.state = NMDCConnState::Closing;
        return;
    }

    // Check if nick is already in use
    if (m_nick_to_conn.count(nick) > 0) {
        SendToConn(client.conn, NMDCProtocol::MakeValidateDenide(nick));
        client.state = NMDCConnState::Closing;
        return;
    }

    client.nick = nick;

    // Ask Python callback for validation (callback guaranteed by SetCallback)
    int auth_result = m_callback->OnValidateNick(nick, client.ip);

    if (auth_result < 0) {
        // Nick rejected
        SendToConn(client.conn, NMDCProtocol::MakeValidateDenide(nick));
        client.state = NMDCConnState::Closing;
        return;
    }

    // Send hub name (with topic if set, like old core)
    SendToConn(client.conn, NMDCProtocol::MakeHubNameWithTopic(m_hub_name, m_hub_topic));

    if (auth_result > 0) {
        // Registered user - needs password
        // IMPORTANT: Don't send $Hello yet! $Hello signals "accepted" to DC clients.
        // If we send $Hello before $GetPass, clients like EiskaltDC++ skip $MyPass.
        client.user_class = auth_result;
        client.state = NMDCConnState::WaitingMyPass;
        SendToConn(client.conn, NMDCProtocol::MakeGetPass());
    } else {
        // Guest - no password needed
        client.user_class = 0;
        client.state = NMDCConnState::WaitingMyINFO;
        SendToConn(client.conn, NMDCProtocol::MakeHello(nick));
        SendToConn(client.conn, NMDCProtocol::MakeLoggedIn());

        // Notify callback
        m_callback->OnUserLogin(nick, client.user_class);
    }
}

void NMDCHubServer::HandleMyPass(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::WaitingMyPass) return;

    std::string password = NMDCProtocol::GetCommandParam(msg, "$MyPass");

    // Ask Python to verify password (callback guaranteed by SetCallback)
    int auth_class = m_callback->OnCheckPassword(client.nick, password);

    if (auth_class < 0) {
        client.login_attempts++;
        if (client.login_attempts >= m_max_login_attempts) {
            SendToConn(client.conn, NMDCProtocol::MakeBadPass());
            client.state = NMDCConnState::Closing;
        } else {
            SendToConn(client.conn, NMDCProtocol::MakeBadPass());
            // Let them try again (stay in WaitingMyPass state)
        }
        return;
    }

    // Password correct
    client.user_class = auth_class;
    client.state = NMDCConnState::WaitingMyINFO;
    SendToConn(client.conn, NMDCProtocol::MakeHello(client.nick));
    SendToConn(client.conn, NMDCProtocol::MakeLoggedIn());

    // Notify callback
    m_callback->OnUserLogin(client.nick, client.user_class);
}

void NMDCHubServer::HandleMyINFO(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::WaitingMyINFO &&
        client.state != NMDCConnState::LoggedIn) {
        return;
    }
    m_proto_stats.myinfo_count.fetch_add(1, std::memory_order_relaxed);

    auto info = NMDCProtocol::ParseMyINFO(msg);
    if (!info.valid) return;

    // Check nick matches
    if (info.nick != client.nick) return;

    bool was_already_logged_in = (client.state == NMDCConnState::LoggedIn);

    // Update client info
    client.myinfo = info;
    client.myinfo_raw = msg;

    if (!was_already_logged_in) {
        // First MyINFO - complete login
        client.state = NMDCConnState::LoggedIn;

        // Parse tag fields (mode, slots, hubs, version)
        if (!info.tag.empty()) {
            auto tag_data = NMDCProtocol::ParseTag(info.tag);
            if (tag_data.valid) {
                client.client_version = tag_data.client_version;
                client.mode = tag_data.mode;
                client.slots = tag_data.slots;
                client.hubs_normal = tag_data.hubs_normal;
                client.hubs_registered = tag_data.hubs_registered;
                client.hubs_operator = tag_data.hubs_operator;
            }
        }

        // Store status flag from speed field
        client.status_flag = info.status_flag;

        // GeoIP lookup (like old core's GetCCC on login)
        if (m_geoip && !client.ip.empty()) {
            auto geo = m_geoip->Lookup(client.ip);
            client.country_code = geo.country_code;
            client.country_name = geo.country_name;
            client.city = geo.city;
        }

        // Add to nick map
        m_nick_to_conn[client.nick] = client.conn;
        m_user_count.fetch_add(1, std::memory_order_relaxed);
        m_total_share.fetch_add(info.share_size, std::memory_order_relaxed);

        // Notify callback
        m_callback->OnUserConnect(client.nick, client.ip);

        // Send hub topic if set
        if (!m_hub_topic.empty()) {
            SendToConn(client.conn, NMDCProtocol::MakeHubTopic(m_hub_topic));
        }

        // Send hub bot info
        SendHubBotInfo(client);

        // Send MOTD to the newly logged-in user
        SendMOTD(client);

        // Announce new user to existing users (broadcast MyINFO)
        AnnounceNewUser(client);

        // Send existing users' MyINFO to the new user
        for (auto& [c, other] : m_clients) {
            if (c != client.conn && other.state == NMDCConnState::LoggedIn &&
                !other.myinfo_raw.empty()) {
                SendToConn(client.conn, other.myinfo_raw);
            }
        }

        // Note: We intentionally do NOT send $UserIP to avoid leaking
        // IP addresses in DC clients that display them in chat.
    } else {
        // Updated MyINFO - recalculate share and broadcast
        m_total_share.fetch_sub(client.myinfo.share_size, std::memory_order_relaxed);
        m_total_share.fetch_add(info.share_size, std::memory_order_relaxed);
        // Broadcast updated MyINFO to all
        SendToAllConns(client.myinfo_raw);
    }
}

void NMDCHubServer::HandleGetNickList(NMDCClient& client) {
    SendUserLists(client);
}

void NMDCHubServer::HandleChat(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;
    m_proto_stats.chat_count.fetch_add(1, std::memory_order_relaxed);

    auto chat = NMDCProtocol::ParseChat(msg);
    if (!chat.valid) return;

    // Verify the nick matches
    if (chat.nick != client.nick) return;

    // Ask Python if message should be allowed
    if (!m_callback->OnChatMessage(client.nick, chat.message)) {
        return;  // Message blocked
    }

    // Broadcast to all logged-in users
    SendToAllConns(msg);
}

void NMDCHubServer::HandlePrivateMessage(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;
    m_proto_stats.pm_count.fetch_add(1, std::memory_order_relaxed);

    auto pm = NMDCProtocol::ParsePrivateMessage(msg);
    if (!pm.valid) return;

    // Verify sender
    if (pm.from != client.nick) return;

    // Ask Python callback
    if (!m_callback->OnPrivateMessage(pm.from, pm.to, pm.message)) {
        return;  // Blocked
    }

    // Deliver to recipient
    auto it = m_nick_to_conn.find(pm.to);
    if (it != m_nick_to_conn.end()) {
        SendToConn(it->second, msg);
    }
}

void NMDCHubServer::HandleSearch(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;
    m_proto_stats.search_count.fetch_add(1, std::memory_order_relaxed);

    // Ask Python callback
    std::string query = NMDCProtocol::GetCommandParam(msg, "$Search");
    if (!m_callback->OnSearch(client.nick, query)) {
        return;  // Blocked
    }

    // Broadcast search to all users (they respond directly via UDP or TCP)
    SendToAllConns(msg);
}

void NMDCHubServer::HandleConnectToMe(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;
    m_proto_stats.ctm_count.fetch_add(1, std::memory_order_relaxed);

    // $ConnectToMe <remote_nick> <ip>:<port>
    // Forward to the target user
    std::string params = NMDCProtocol::GetCommandParam(msg, "$ConnectToMe");
    size_t space = params.find(' ');
    if (space == std::string::npos) return;

    std::string target_nick = params.substr(0, space);
    std::string addr = params.substr(space + 1);

    // Validate: the IP in the address must match the sender's actual IP
    // to prevent connection spoofing
    size_t colon = addr.find(':');
    if (colon != std::string::npos) {
        std::string claimed_ip = addr.substr(0, colon);
        if (claimed_ip != client.ip) {
            return;  // IP mismatch — silently drop
        }
    }

    auto it = m_nick_to_conn.find(target_nick);
    if (it != m_nick_to_conn.end()) {
        SendToConn(it->second, msg);
    }
}

void NMDCHubServer::HandleRevConnectToMe(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;

    // $RevConnectToMe <sender_nick> <target_nick>
    std::string params = NMDCProtocol::GetCommandParam(msg, "$RevConnectToMe");
    size_t space = params.find(' ');
    if (space == std::string::npos) return;

    std::string sender_nick = params.substr(0, space);
    std::string target_nick = params.substr(space + 1);

    // Verify the sender nick matches the connection's nick
    if (sender_nick != client.nick) return;

    auto it = m_nick_to_conn.find(target_nick);
    if (it != m_nick_to_conn.end()) {
        SendToConn(it->second, msg);
    }
}

void NMDCHubServer::HandleSR(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;
    m_proto_stats.sr_count.fetch_add(1, std::memory_order_relaxed);

    // Parse the search result
    auto sr = NMDCProtocol::ParseSR(msg);
    if (!sr.valid) return;

    // Verify sender nick
    if (sr.from_nick != client.nick) return;

    if (!sr.to_nick.empty()) {
        // Directed search result — deliver to specific user
        // Strip the target nick (\x05nick) before sending
        auto it = m_nick_to_conn.find(sr.to_nick);
        if (it != m_nick_to_conn.end()) {
            // Rebuild without the \x05<to_nick> suffix — recipient doesn't need it
            std::string stripped = "$SR " + sr.from_nick + " " + sr.payload;
            SendToConn(it->second, stripped);
        }
    }
    // Active search results are sent directly via UDP, not relayed through hub
}

void NMDCHubServer::HandleQuit(NMDCClient& client) {
    client.state = NMDCConnState::Closing;
    // Connection will be cleaned up by the normal close path
}

// =============================================================================
// Internal Helpers
// =============================================================================

void NMDCHubServer::SendToConn(cAsyncConn* conn, const std::string& data) {
    if (!conn || !conn->ok) return;
    conn->Write(data + "|", true);
    m_proto_stats.messages_out.fetch_add(1, std::memory_order_relaxed);
}

void NMDCHubServer::SendToConnCompressed(NMDCClient& client, const std::string& data) {
    if (!client.conn || !client.conn->ok) return;

    std::string msg = data + "|";

    // Compress if ZLib is enabled, client supports it, and data is large enough
    if (m_zlib_enabled && client.supports_zlib && msg.size() >= m_zlib_min_size) {
        if (!m_zlib) {
            m_zlib = std::make_unique<nUtils::cZLib>();
        }

        size_t out_len = 0;
        int err = 0;
        char* compressed = m_zlib->Compress(msg.c_str(), msg.size(), out_len, err, Z_DEFAULT_COMPRESSION);

        if (compressed && err == Z_OK && out_len > 0 && out_len < msg.size()) {
            // Send $ZOn| header followed by compressed data
            std::string zon = "$ZOn|";
            zon.append(compressed, out_len);
            client.conn->Write(zon, true);
            m_proto_stats.messages_out.fetch_add(1, std::memory_order_relaxed);
            return;
        }
    }

    // Fallback: send uncompressed
    client.conn->Write(msg, true);
    m_proto_stats.messages_out.fetch_add(1, std::memory_order_relaxed);
}

void NMDCHubServer::SendToAllConns(const std::string& data) {
    std::string msg = data + "|";
    for (auto& [conn, client] : m_clients) {
        if (client.state == NMDCConnState::LoggedIn && conn && conn->ok) {
            conn->Write(msg, true);
        }
    }
}

void NMDCHubServer::RemoveClient(cAsyncConn* conn) {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);

    auto it = m_clients.find(conn);
    if (it == m_clients.end()) return;

    NMDCClient& client = it->second;

    if (client.state == NMDCConnState::LoggedIn) {
        // Notify other users
        SendToAllConns(NMDCProtocol::MakeQuit(client.nick));

        // Update counters
        m_user_count.fetch_sub(1, std::memory_order_relaxed);
        if (client.myinfo.share_size > 0) {
            uint64_t current = m_total_share.load(std::memory_order_relaxed);
            uint64_t sub = std::min(current, client.myinfo.share_size);
            m_total_share.fetch_sub(sub, std::memory_order_relaxed);
        }

        // Notify Python
        m_callback->OnUserDisconnect(client.nick);

        // Remove from nick map
        m_nick_to_conn.erase(client.nick);
    }

    m_clients.erase(it);
}

void NMDCHubServer::SendUserLists(NMDCClient& client) {
    std::vector<std::string> all_nicks;
    std::vector<std::string> op_nicks;
    std::vector<std::string> bot_nicks;

    for (auto& [c, other] : m_clients) {
        if (other.state == NMDCConnState::LoggedIn) {
            all_nicks.push_back(other.nick);
            if (other.user_class >= 3) {  // VIP and above are in OpList
                op_nicks.push_back(other.nick);
            }
        }
    }

    // Always include hub security bot in lists
    all_nicks.push_back(m_hub_security);
    op_nicks.push_back(m_hub_security);
    bot_nicks.push_back(m_hub_security);

    // Include OpChat bot in lists (if configured)
    if (!m_opchat_name.empty()) {
        all_nicks.push_back(m_opchat_name);
        op_nicks.push_back(m_opchat_name);
        bot_nicks.push_back(m_opchat_name);
    }

    SendToConn(client.conn, NMDCProtocol::MakeNickList(all_nicks));
    SendToConn(client.conn, NMDCProtocol::MakeOpList(op_nicks));

    // Send BotList if client supports it
    if (client.supports_text.find("BotList") != std::string::npos) {
        SendToConn(client.conn, NMDCProtocol::MakeBotList(bot_nicks));
    }
}

void NMDCHubServer::AnnounceNewUser(const NMDCClient& client) {
    if (client.myinfo_raw.empty()) return;

    // Broadcast $MyINFO to all other logged-in users
    for (auto& [c, other] : m_clients) {
        if (c != client.conn && other.state == NMDCConnState::LoggedIn) {
            SendToConn(c, client.myinfo_raw);
        }
    }

    // Also broadcast $Hello to all (some clients need this)
    SendToAllConns(NMDCProtocol::MakeHello(client.nick));
}

void NMDCHubServer::SendHubBotInfo(NMDCClient& client) {
    // Send the hub security bot's MyINFO
    std::string bot_myinfo = NMDCProtocol::MakeBotMyINFO(
        m_hub_security,
        "Verlihub-py Hub Security Bot<Bot V:1.0,M:A,H:0/0/1,S:0>"
    );
    SendToConn(client.conn, bot_myinfo);

    // Send OpChat bot's MyINFO (if configured)
    if (!m_opchat_name.empty()) {
        std::string opchat_myinfo = NMDCProtocol::MakeBotMyINFO(
            m_opchat_name,
            "Operator Chat<Bot V:1.0,M:A,H:0/0/1,S:0>"
        );
        SendToConn(client.conn, opchat_myinfo);
    }
}

void NMDCHubServer::SendMOTD(NMDCClient& client) {
    if (m_motd.empty()) return;

    // Send the entire MOTD as a single chat message from the hub security bot.
    // Replace newlines with \r\n so multi-line MOTDs render properly in DC clients
    // while remaining a single protocol message.
    std::string msg;
    msg.reserve(m_motd.size());
    for (size_t i = 0; i < m_motd.size(); ++i) {
        char c = m_motd[i];
        if (c == '\r') continue;  // strip \r, we add our own \r\n below
        if (c == '\n') {
            msg += "\r\n";
        } else {
            msg += c;
        }
    }
    // Remove trailing whitespace/newlines
    while (!msg.empty() && (msg.back() == '\r' || msg.back() == '\n' || msg.back() == ' '))
        msg.pop_back();

    SendToConn(client.conn, NMDCProtocol::MakeChat(m_hub_security, msg));
}

// =============================================================================
// Public Messaging API
// =============================================================================

bool NMDCHubServer::SendToNick(const std::string& nick, const std::string& data) {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    auto it = m_nick_to_conn.find(nick);
    if (it == m_nick_to_conn.end()) return false;
    SendToConn(it->second, data);
    return true;
}

void NMDCHubServer::SendToAll(const std::string& data) {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    SendToAllConns(data);
}

void NMDCHubServer::SendChatToAll(const std::string& from, const std::string& message) {
    std::string msg = NMDCProtocol::MakeChat(from, message);
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    SendToAllConns(msg);
}

bool NMDCHubServer::SendPM(const std::string& from, const std::string& to,
                           const std::string& message) {
    std::string msg = NMDCProtocol::MakePrivateMessage(from, to, message);
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    auto it = m_nick_to_conn.find(to);
    if (it == m_nick_to_conn.end()) return false;
    SendToConn(it->second, msg);
    return true;
}

// =============================================================================
// Public User Information API
// =============================================================================

std::vector<std::string> NMDCHubServer::GetNickList() const {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    std::vector<std::string> nicks;
    nicks.reserve(m_clients.size());
    for (auto& [conn, client] : m_clients) {
        if (client.state == NMDCConnState::LoggedIn) {
            nicks.push_back(client.nick);
        }
    }
    return nicks;
}

std::vector<std::string> NMDCHubServer::GetOpList() const {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    std::vector<std::string> nicks;
    for (auto& [conn, client] : m_clients) {
        if (client.state == NMDCConnState::LoggedIn && client.user_class >= 3) {
            nicks.push_back(client.nick);
        }
    }
    return nicks;
}

size_t NMDCHubServer::GetUserCount() const {
    return m_user_count.load(std::memory_order_relaxed);
}

bool NMDCHubServer::IsNickOnline(const std::string& nick) const {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    return m_nick_to_conn.count(nick) > 0;
}

uint64_t NMDCHubServer::GetTotalShare() const {
    return m_total_share.load(std::memory_order_relaxed);
}

bool NMDCHubServer::GetUserInfo(const std::string& nick, UserInfoSnapshot& out) const {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    auto it = m_nick_to_conn.find(nick);
    if (it == m_nick_to_conn.end()) return false;
    auto cit = m_clients.find(it->second);
    if (cit == m_clients.end()) return false;
    const NMDCClient& c = cit->second;
    if (c.state != NMDCConnState::LoggedIn) return false;
    out.nick        = c.nick;
    out.ip          = c.ip;
    out.user_class  = c.user_class;
    out.share       = c.myinfo.share_size;
    out.description = c.myinfo.description;
    out.tag         = c.myinfo.tag;
    out.speed       = c.myinfo.speed;
    out.email       = c.myinfo.email;
    out.country     = c.country_code;
    out.country_name= c.country_name;
    out.city        = c.city;
    out.client_name = ExtractClientName(c.myinfo.tag);
    out.client_version = c.client_version;
    out.mode        = c.mode;
    out.slots       = c.slots;
    out.hubs_normal = c.hubs_normal;
    out.hubs_registered = c.hubs_registered;
    out.hubs_operator = c.hubs_operator;
    out.status_flag = c.status_flag;
    out.supports    = c.supports_text;
    out.login_time  = std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::steady_clock::now() - c.connect_time).count();
    return true;
}

std::vector<UserInfoSnapshot> NMDCHubServer::GetUserInfoSnapshots() const {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    std::vector<UserInfoSnapshot> result;
    result.reserve(m_clients.size());
    for (auto& [conn, c] : m_clients) {
        if (c.state != NMDCConnState::LoggedIn) continue;
        UserInfoSnapshot s;
        s.nick        = c.nick;
        s.ip          = c.ip;
        s.user_class  = c.user_class;
        s.share       = c.myinfo.share_size;
        s.description = c.myinfo.description;
        s.tag         = c.myinfo.tag;
        s.speed       = c.myinfo.speed;
        s.email       = c.myinfo.email;
        s.country     = c.country_code;
        s.country_name= c.country_name;
        s.city        = c.city;
        s.client_name = ExtractClientName(c.myinfo.tag);
        s.client_version = c.client_version;
        s.mode        = c.mode;
        s.slots       = c.slots;
        s.hubs_normal = c.hubs_normal;
        s.hubs_registered = c.hubs_registered;
        s.hubs_operator = c.hubs_operator;
        s.status_flag = c.status_flag;
        s.supports    = c.supports_text;
        s.login_time  = std::chrono::duration_cast<std::chrono::seconds>(
            std::chrono::steady_clock::now() - c.connect_time).count();
        result.push_back(std::move(s));
    }
    return result;
}

// =============================================================================
// Public User Management API
// =============================================================================

bool NMDCHubServer::KickUser(const std::string& nick, const std::string& reason,
                             const std::string& op) {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    auto it = m_nick_to_conn.find(nick);
    if (it == m_nick_to_conn.end()) return false;

    cAsyncConn* conn = it->second;
    auto client_it = m_clients.find(conn);
    if (client_it == m_clients.end()) return false;

    // Send kick message
    std::string kick_from = op.empty() ? m_hub_security : op;
    std::string kick_msg = NMDCProtocol::MakeChat(kick_from,
        "You are being kicked: " + reason);
    SendToConn(conn, kick_msg);

    // Mark for closing
    client_it->second.state = NMDCConnState::Closing;
    conn->CloseNice(500);

    return true;
}

bool NMDCHubServer::DisconnectUser(const std::string& nick) {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    auto it = m_nick_to_conn.find(nick);
    if (it == m_nick_to_conn.end()) return false;

    cAsyncConn* conn = it->second;
    auto client_it = m_clients.find(conn);
    if (client_it == m_clients.end()) return false;

    client_it->second.state = NMDCConnState::Closing;
    conn->CloseNice(100);

    return true;
}

// =============================================================================
// Flood Protection
// =============================================================================

bool NMDCHubServer::CheckFlood(NMDCClient& client, FloodType type) {
    // Operators (class >= 3) are exempt from flood checks
    if (client.user_class >= 3) return true;

    auto idx = static_cast<size_t>(type);
    auto& bucket = client.flood.buckets[idx];
    const auto& limit = m_flood_limits[idx];

    auto now = std::chrono::steady_clock::now();
    auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        now - bucket.last_refill).count();

    // Refill tokens based on elapsed time
    if (elapsed_ms >= limit.period_ms) {
        int periods = static_cast<int>(elapsed_ms / limit.period_ms);
        bucket.tokens = std::min(bucket.tokens + periods, limit.max_tokens);
        bucket.last_refill += std::chrono::milliseconds(
            static_cast<long long>(periods) * limit.period_ms);
    }

    // Consume a token
    if (bucket.tokens > 0) {
        --bucket.tokens;
        // Reset warning counter on successful messages
        if (client.flood_warnings > 0) {
            client.flood_warnings = 0;
        }
        return true;  // Message allowed
    }

    // Flood detected — warn or disconnect
    ++client.flood_warnings;
    m_proto_stats.flood_blocked.fetch_add(1, std::memory_order_relaxed);
    if (client.flood_warnings >= m_max_flood_warnings) {
        // Disconnect the client
        SendToConn(client.conn,
            NMDCProtocol::MakeChat(m_hub_security,
                "You have been disconnected for flooding."));
        client.state = NMDCConnState::Closing;
        client.conn->CloseNice(100);
    } else {
        // Warn
        SendToConn(client.conn,
            NMDCProtocol::MakeChat(m_hub_security,
                "Warning: you are sending messages too fast."));
    }
    return false;  // Message blocked
}

void NMDCHubServer::SetFloodConfig(FloodType type, int period_ms, int max_tokens) {
    auto idx = static_cast<size_t>(type);
    if (idx >= m_flood_limits.size()) return;
    m_flood_limits[idx].period_ms = std::max(100, period_ms);    // Min 100ms
    m_flood_limits[idx].max_tokens = std::max(1, max_tokens);    // Min 1 token
}

FloodLimit NMDCHubServer::GetFloodConfig(FloodType type) const {
    auto idx = static_cast<size_t>(type);
    if (idx >= m_flood_limits.size()) return {};
    return m_flood_limits[idx];
}

// =============================================================================
// Ban Cache
// =============================================================================

void NMDCHubServer::LoadBanCache(const std::vector<std::string>& ips,
                                  const std::vector<std::string>& nicks) {
    std::lock_guard<std::mutex> lock(m_ban_cache_mutex);
    m_banned_ips.clear();
    m_banned_ips.insert(ips.begin(), ips.end());
    m_banned_nicks.clear();
    m_banned_nicks.insert(nicks.begin(), nicks.end());
}

void NMDCHubServer::AddBanCacheIP(const std::string& ip) {
    std::lock_guard<std::mutex> lock(m_ban_cache_mutex);
    m_banned_ips.insert(ip);
}

void NMDCHubServer::AddBanCacheNick(const std::string& nick) {
    std::lock_guard<std::mutex> lock(m_ban_cache_mutex);
    m_banned_nicks.insert(nick);
}

void NMDCHubServer::RemoveBanCacheIP(const std::string& ip) {
    std::lock_guard<std::mutex> lock(m_ban_cache_mutex);
    m_banned_ips.erase(ip);
}

void NMDCHubServer::RemoveBanCacheNick(const std::string& nick) {
    std::lock_guard<std::mutex> lock(m_ban_cache_mutex);
    m_banned_nicks.erase(nick);
}

void NMDCHubServer::ClearBanCache() {
    std::lock_guard<std::mutex> lock(m_ban_cache_mutex);
    m_banned_ips.clear();
    m_banned_nicks.clear();
}

bool NMDCHubServer::IsIPBanned(const std::string& ip) const {
    std::lock_guard<std::mutex> lock(m_ban_cache_mutex);
    return m_banned_ips.count(ip) > 0;
}

bool NMDCHubServer::IsNickBanned(const std::string& nick) const {
    std::lock_guard<std::mutex> lock(m_ban_cache_mutex);
    return m_banned_nicks.count(nick) > 0;
}

// =============================================================================
// Active / Passive Messaging
// =============================================================================

void NMDCHubServer::SendToConnsFiltered(const std::string& data, char mode_filter,
                                         int min_class, int max_class) {
    std::string msg = data + "|";
    for (auto& [conn, client] : m_clients) {
        if (client.state == NMDCConnState::LoggedIn && conn && conn->ok &&
            client.mode == mode_filter &&
            client.user_class >= min_class && client.user_class <= max_class) {
            conn->Write(msg, true);
        }
    }
}

void NMDCHubServer::SendToActive(const std::string& data) {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    SendToConnsFiltered(data, 'A');
}

void NMDCHubServer::SendToPassive(const std::string& data) {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    SendToConnsFiltered(data, 'P');
}

void NMDCHubServer::SendToActiveClass(const std::string& data,
                                       int min_class, int max_class) {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    SendToConnsFiltered(data, 'A', min_class, max_class);
}

void NMDCHubServer::SendToPassiveClass(const std::string& data,
                                        int min_class, int max_class) {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    SendToConnsFiltered(data, 'P', min_class, max_class);
}

size_t NMDCHubServer::GetActiveUserCount() const {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    size_t count = 0;
    for (auto& [conn, client] : m_clients) {
        if (client.state == NMDCConnState::LoggedIn && client.mode == 'A')
            ++count;
    }
    return count;
}

size_t NMDCHubServer::GetPassiveUserCount() const {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    size_t count = 0;
    for (auto& [conn, client] : m_clients) {
        if (client.state == NMDCConnState::LoggedIn && client.mode == 'P')
            ++count;
    }
    return count;
}

// =============================================================================
// ForceMove
// =============================================================================

bool NMDCHubServer::ForceMove(const std::string& nick, const std::string& address) {
    std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
    auto it = m_nick_to_conn.find(nick);
    if (it == m_nick_to_conn.end()) return false;

    cAsyncConn* conn = it->second;
    auto client_it = m_clients.find(conn);
    if (client_it == m_clients.end()) return false;

    SendToConn(conn, NMDCProtocol::MakeForceMove(address));
    client_it->second.state = NMDCConnState::Closing;
    conn->CloseNice(500);
    return true;
}

// =============================================================================
// New Protocol Handlers (Phase 2)
// =============================================================================

void NMDCHubServer::HandleMCTo(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;
    m_proto_stats.mcto_count.fetch_add(1, std::memory_order_relaxed);

    auto mcto = NMDCProtocol::ParseMCTo(msg);
    if (!mcto.valid) return;

    // Verify sender nick matches connection's nick
    if (mcto.from != client.nick) return;

    // Ask Python callback (treat as PM for filtering purposes)
    if (!m_callback->OnPrivateMessage(mcto.from, mcto.to, mcto.message)) {
        return;
    }

    // Deliver to target only
    auto it = m_nick_to_conn.find(mcto.to);
    if (it != m_nick_to_conn.end()) {
        SendToConn(it->second, msg);
    }
}

void NMDCHubServer::HandleUserIP(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;

    // Operator-only: class >= 3
    if (client.user_class < 3) return;

    // Format: "$UserIP nick1$$nick2$$"
    std::string params = NMDCProtocol::GetCommandParam(msg, "$UserIP");
    if (params.empty()) return;

    // Parse nick list (separated by $$)
    std::vector<std::pair<std::string, std::string>> results;
    size_t pos = 0;
    while (pos < params.size()) {
        size_t end = params.find("$$", pos);
        std::string nick;
        if (end == std::string::npos) {
            nick = params.substr(pos);
            pos = params.size();
        } else {
            nick = params.substr(pos, end - pos);
            pos = end + 2;
        }
        if (nick.empty()) continue;

        auto it = m_nick_to_conn.find(nick);
        if (it != m_nick_to_conn.end()) {
            auto cit = m_clients.find(it->second);
            if (cit != m_clients.end() && cit->second.state == NMDCConnState::LoggedIn) {
                results.emplace_back(nick, cit->second.ip);
            }
        }
    }

    if (!results.empty()) {
        SendToConn(client.conn, NMDCProtocol::MakeUserIPList(results));
    }
}

void NMDCHubServer::HandleWhoIP(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;

    // Operator-only: class >= 3
    if (client.user_class < 3) return;

    // Format: "$WhoIP <ip>"
    std::string ip = NMDCProtocol::GetCommandParam(msg, "$WhoIP");
    if (ip.empty()) return;

    // Find all users with this IP
    std::vector<std::pair<std::string, std::string>> results;
    for (auto& [conn, c] : m_clients) {
        if (c.state == NMDCConnState::LoggedIn && c.ip == ip) {
            results.emplace_back(c.nick, c.ip);
        }
    }

    if (!results.empty()) {
        SendToConn(client.conn, NMDCProtocol::MakeUserIPList(results));
    }
}

void NMDCHubServer::HandleOpForceMove(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;

    // Operator-only: class >= 3
    if (client.user_class < 3) return;

    // Format: "$OpForceMove $Who:<nick>$Where:<address>$Msg:<reason>"
    std::string params = NMDCProtocol::GetCommandParam(msg, "$OpForceMove");
    if (params.empty()) return;

    // Parse $Who:<nick>$Where:<address>$Msg:<reason>
    std::string nick, address, reason;

    size_t who_pos = params.find("$Who:");
    size_t where_pos = params.find("$Where:");
    size_t msg_pos = params.find("$Msg:");

    if (who_pos == std::string::npos || where_pos == std::string::npos) return;

    nick = params.substr(who_pos + 5, where_pos - (who_pos + 5));
    if (msg_pos != std::string::npos) {
        address = params.substr(where_pos + 7, msg_pos - (where_pos + 7));
        reason = params.substr(msg_pos + 5);
    } else {
        address = params.substr(where_pos + 7);
    }

    if (nick.empty() || address.empty()) return;

    // Send reason message if provided
    if (!reason.empty()) {
        SendToNick(nick, NMDCProtocol::MakeChat(m_hub_security,
            "You are being redirected: " + reason));
    }

    // Force move the target
    ForceMove(nick, address);
}

// =============================================================================
// Phase 3.6: $ExtJSON Handler
// =============================================================================

void NMDCHubServer::HandleExtJSON(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;

    // Client must have declared ExtJSON2 support
    if (!client.supports_extjson) return;

    // Format: "$ExtJSON <nick> <json_data>"
    std::string params = NMDCProtocol::GetCommandParam(msg, "$ExtJSON");
    if (params.empty()) return;

    // First token is the nick, rest is JSON
    size_t space = params.find(' ');
    if (space == std::string::npos) return;

    std::string nick = params.substr(0, space);
    if (nick != client.nick) return;  // Nick must match sender

    std::string json = params.substr(space + 1);
    if (json.empty()) return;

    // Notify Python callback
    if (m_callback) {
        if (!m_callback->OnExtJSON(client.nick, json)) return;
    }

    // Store and forward to clients that support ExtJSON2 (if changed)
    if (json != client.ext_json) {
        client.ext_json = json;
        // Forward the raw message to all clients with ExtJSON2 support
        std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
        for (auto& [conn, other] : m_clients) {
            if (other.state == NMDCConnState::LoggedIn && other.supports_extjson) {
                SendToConn(conn, msg);
            }
        }
    }
}

// =============================================================================
// Phase 3.6: $MyHubURL Handler
// =============================================================================

void NMDCHubServer::HandleMyHubURL(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;

    if (!client.supports_huburl) return;

    // Format: "$MyHubURL <url>"
    std::string url = NMDCProtocol::GetCommandParam(msg, "$MyHubURL");
    if (url.empty()) return;

    // Notify Python callback
    if (m_callback) {
        if (!m_callback->OnMyHubURL(client.nick, url)) {
            client.state = NMDCConnState::Closing;
            if (client.conn) client.conn->CloseNice(1000);
            return;
        }
    }

    client.hub_url = url;
}

// =============================================================================
// Phase 3.6: $IN Handler (Incremental Info Update)
// =============================================================================

void NMDCHubServer::HandleIN(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;

    if (!client.supports_in) return;

    // Format: "$IN <nick> <data>" where data is key/value pairs separated by $$
    std::string params = NMDCProtocol::GetCommandParam(msg, "$IN");
    if (params.empty()) return;

    size_t space = params.find(' ');
    if (space == std::string::npos) return;

    std::string nick = params.substr(0, space);
    if (nick != client.nick) return;  // Nick must match sender

    std::string data = params.substr(space + 1);

    // Notify Python callback
    if (m_callback) {
        if (!m_callback->OnUserINUpdate(client.nick, data)) return;
    }

    // Forward to clients that support IN
    {
        std::lock_guard<std::recursive_mutex> lock(m_clients_mutex);
        for (auto& [conn, other] : m_clients) {
            if (other.state == NMDCConnState::LoggedIn && other.supports_in) {
                SendToConn(conn, msg);
            }
        }
    }
}

// =============================================================================
// Protocol Statistics
// =============================================================================

NMDCHubServer::ProtocolStatsSnapshot NMDCHubServer::GetProtocolStats() const {
    ProtocolStatsSnapshot s;
    s.messages_in  = m_proto_stats.messages_in.load(std::memory_order_relaxed);
    s.messages_out = m_proto_stats.messages_out.load(std::memory_order_relaxed);
    s.chat_count   = m_proto_stats.chat_count.load(std::memory_order_relaxed);
    s.pm_count     = m_proto_stats.pm_count.load(std::memory_order_relaxed);
    s.search_count = m_proto_stats.search_count.load(std::memory_order_relaxed);
    s.myinfo_count = m_proto_stats.myinfo_count.load(std::memory_order_relaxed);
    s.ctm_count    = m_proto_stats.ctm_count.load(std::memory_order_relaxed);
    s.sr_count     = m_proto_stats.sr_count.load(std::memory_order_relaxed);
    s.mcto_count   = m_proto_stats.mcto_count.load(std::memory_order_relaxed);
    s.flood_blocked= m_proto_stats.flood_blocked.load(std::memory_order_relaxed);
    s.ban_blocked  = m_proto_stats.ban_blocked.load(std::memory_order_relaxed);
    return s;
}

}  // namespace nVerliHub
