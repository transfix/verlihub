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
#include <iostream>
#include <algorithm>

namespace nVerliHub {

using namespace nSocket;
using namespace nEnums;

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

    std::string ip = conn->AddrIP();

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

    // Send $Lock
    std::string lock_msg = NMDCProtocol::MakeLock(client.lock);
    conn->Write(lock_msg + "|", true);

    // Store client
    {
        std::lock_guard<std::mutex> lock(m_clients_mutex);
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

    // Find the client
    std::lock_guard<std::mutex> lock(m_clients_mutex);
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
        HandleMyINFO(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$GetNickList")) {
        HandleGetNickList(client);
    } else if (NMDCProtocol::IsCommand(message, "$To:")) {
        HandlePrivateMessage(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$Search")) {
        HandleSearch(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$ConnectToMe")) {
        HandleConnectToMe(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$RevConnectToMe")) {
        HandleRevConnectToMe(client, message);
    } else if (NMDCProtocol::IsCommand(message, "$Quit")) {
        HandleQuit(client);
    } else if (NMDCProtocol::IsCommand(message, "$Version")) {
        // Ignore version announcements
    } else if (NMDCProtocol::IsCommand(message, "$GetINFO")) {
        // Ignore GetINFO (we send NoGetINFO in $Supports)
    } else if (NMDCProtocol::IsCommand(message, "$BotINFO")) {
        // Ignore bot info requests for now
    } else if (NMDCProtocol::IsCommand(message, "$HubINFO")) {
        // Ignore hub info requests for now
    } else if (!message.empty() && message[0] == '<') {
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
    // Periodic tasks - could add ping/timeout checks here
    return 0;
}

// =============================================================================
// Protocol Handlers
// =============================================================================

void NMDCHubServer::HandleSupports(NMDCClient& client, const std::string& msg) {
    // Store client features if needed - for now just acknowledge
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

    // Check if nick is already in use
    if (m_nick_to_conn.count(nick) > 0) {
        SendToConn(client.conn, NMDCProtocol::MakeValidateDenide(nick));
        client.state = NMDCConnState::Closing;
        return;
    }

    client.nick = nick;

    // Ask Python callback for validation
    int auth_result = 0;  // Default: allow as guest
    if (m_callback) {
        auth_result = m_callback->OnValidateNick(nick, client.ip);
    }

    if (auth_result < 0) {
        // Nick rejected
        SendToConn(client.conn, NMDCProtocol::MakeValidateDenide(nick));
        client.state = NMDCConnState::Closing;
        return;
    }

    // Send hub name
    SendToConn(client.conn, NMDCProtocol::MakeHubName(m_hub_name));

    if (auth_result > 0) {
        // Registered user - needs password
        client.user_class = auth_result;
        client.state = NMDCConnState::WaitingMyPass;
        SendToConn(client.conn, NMDCProtocol::MakeHello(nick));
        SendToConn(client.conn, NMDCProtocol::MakeGetPass());
    } else {
        // Guest - no password needed
        client.user_class = 0;
        client.state = NMDCConnState::WaitingMyINFO;
        SendToConn(client.conn, NMDCProtocol::MakeHello(nick));
        SendToConn(client.conn, NMDCProtocol::MakeLoggedIn());

        // Notify callback
        if (m_callback) {
            m_callback->OnUserLogin(nick, client.user_class);
        }
    }
}

void NMDCHubServer::HandleMyPass(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::WaitingMyPass) return;

    std::string password = NMDCProtocol::GetCommandParam(msg, "$MyPass");

    // Ask Python to verify password
    int auth_class = -1;
    if (m_callback) {
        auth_class = m_callback->OnCheckPassword(client.nick, password);
    }

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
    if (m_callback) {
        m_callback->OnUserLogin(client.nick, client.user_class);
    }
}

void NMDCHubServer::HandleMyINFO(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::WaitingMyINFO &&
        client.state != NMDCConnState::LoggedIn) {
        return;
    }

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

        // Add to nick map
        m_nick_to_conn[client.nick] = client.conn;
        m_user_count.fetch_add(1, std::memory_order_relaxed);
        m_total_share.fetch_add(info.share_size, std::memory_order_relaxed);

        // Notify callback
        if (m_callback) {
            m_callback->OnUserConnect(client.nick, client.ip);
        }

        // Send hub topic if set
        if (!m_hub_topic.empty()) {
            SendToConn(client.conn, NMDCProtocol::MakeHubTopic(m_hub_topic));
        }

        // Send hub bot info
        SendHubBotInfo(client);

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

    auto chat = NMDCProtocol::ParseChat(msg);
    if (!chat.valid) return;

    // Verify the nick matches
    if (chat.nick != client.nick) return;

    // Ask Python if message should be allowed
    if (m_callback) {
        if (!m_callback->OnChatMessage(client.nick, chat.message)) {
            return;  // Message blocked
        }
    }

    // Broadcast to all logged-in users
    SendToAllConns(msg);
}

void NMDCHubServer::HandlePrivateMessage(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;

    auto pm = NMDCProtocol::ParsePrivateMessage(msg);
    if (!pm.valid) return;

    // Verify sender
    if (pm.from != client.nick) return;

    // Ask Python callback
    if (m_callback) {
        if (!m_callback->OnPrivateMessage(pm.from, pm.to, pm.message)) {
            return;  // Blocked
        }
    }

    // Deliver to recipient
    auto it = m_nick_to_conn.find(pm.to);
    if (it != m_nick_to_conn.end()) {
        SendToConn(it->second, msg);
    }
}

void NMDCHubServer::HandleSearch(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;

    // Ask Python callback
    if (m_callback) {
        std::string query = NMDCProtocol::GetCommandParam(msg, "$Search");
        if (!m_callback->OnSearch(client.nick, query)) {
            return;  // Blocked
        }
    }

    // Broadcast search to all users (they respond directly via UDP or TCP)
    SendToAllConns(msg);
}

void NMDCHubServer::HandleConnectToMe(NMDCClient& client, const std::string& msg) {
    if (client.state != NMDCConnState::LoggedIn) return;

    // $ConnectToMe <remote_nick> <ip>:<port>
    // Forward to the target user
    std::string params = NMDCProtocol::GetCommandParam(msg, "$ConnectToMe");
    size_t space = params.find(' ');
    if (space == std::string::npos) return;

    std::string target_nick = params.substr(0, space);
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

    std::string target_nick = params.substr(space + 1);
    auto it = m_nick_to_conn.find(target_nick);
    if (it != m_nick_to_conn.end()) {
        SendToConn(it->second, msg);
    }
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
    std::lock_guard<std::mutex> lock(m_clients_mutex);

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
        if (m_callback) {
            m_callback->OnUserDisconnect(client.nick);
        }

        // Remove from nick map
        m_nick_to_conn.erase(client.nick);
    }

    m_clients.erase(it);
}

void NMDCHubServer::SendUserLists(NMDCClient& client) {
    std::vector<std::string> all_nicks;
    std::vector<std::string> op_nicks;

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

    SendToConn(client.conn, NMDCProtocol::MakeNickList(all_nicks));
    SendToConn(client.conn, NMDCProtocol::MakeOpList(op_nicks));
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
}

// =============================================================================
// Public Messaging API
// =============================================================================

bool NMDCHubServer::SendToNick(const std::string& nick, const std::string& data) {
    std::lock_guard<std::mutex> lock(m_clients_mutex);
    auto it = m_nick_to_conn.find(nick);
    if (it == m_nick_to_conn.end()) return false;
    SendToConn(it->second, data);
    return true;
}

void NMDCHubServer::SendToAll(const std::string& data) {
    std::lock_guard<std::mutex> lock(m_clients_mutex);
    SendToAllConns(data);
}

void NMDCHubServer::SendChatToAll(const std::string& from, const std::string& message) {
    std::string msg = NMDCProtocol::MakeChat(from, message);
    std::lock_guard<std::mutex> lock(m_clients_mutex);
    SendToAllConns(msg);
}

bool NMDCHubServer::SendPM(const std::string& from, const std::string& to,
                           const std::string& message) {
    std::string msg = NMDCProtocol::MakePrivateMessage(from, to, message);
    std::lock_guard<std::mutex> lock(m_clients_mutex);
    auto it = m_nick_to_conn.find(to);
    if (it == m_nick_to_conn.end()) return false;
    SendToConn(it->second, msg);
    return true;
}

// =============================================================================
// Public User Information API
// =============================================================================

std::vector<std::string> NMDCHubServer::GetNickList() const {
    std::lock_guard<std::mutex> lock(m_clients_mutex);
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
    std::lock_guard<std::mutex> lock(m_clients_mutex);
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
    std::lock_guard<std::mutex> lock(m_clients_mutex);
    return m_nick_to_conn.count(nick) > 0;
}

uint64_t NMDCHubServer::GetTotalShare() const {
    return m_total_share.load(std::memory_order_relaxed);
}

bool NMDCHubServer::GetUserInfo(const std::string& nick, UserInfoSnapshot& out) const {
    std::lock_guard<std::mutex> lock(m_clients_mutex);
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
    return true;
}

std::vector<UserInfoSnapshot> NMDCHubServer::GetUserInfoSnapshots() const {
    std::lock_guard<std::mutex> lock(m_clients_mutex);
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
        result.push_back(std::move(s));
    }
    return result;
}

// =============================================================================
// Public User Management API
// =============================================================================

bool NMDCHubServer::KickUser(const std::string& nick, const std::string& reason,
                             const std::string& op) {
    std::lock_guard<std::mutex> lock(m_clients_mutex);
    auto it = m_nick_to_conn.find(nick);
    if (it == m_nick_to_conn.end()) return false;

    cAsyncConn* conn = it->second;
    auto client_it = m_clients.find(conn);
    if (client_it == m_clients.end()) return false;

    // Send kick message
    std::string kick_msg = NMDCProtocol::MakeChat(op,
        "You are being kicked: " + reason);
    SendToConn(conn, kick_msg);

    // Mark for closing
    client_it->second.state = NMDCConnState::Closing;
    conn->CloseNice(500);

    return true;
}

bool NMDCHubServer::DisconnectUser(const std::string& nick) {
    std::lock_guard<std::mutex> lock(m_clients_mutex);
    auto it = m_nick_to_conn.find(nick);
    if (it == m_nick_to_conn.end()) return false;

    cAsyncConn* conn = it->second;
    auto client_it = m_clients.find(conn);
    if (client_it == m_clients.end()) return false;

    client_it->second.state = NMDCConnState::Closing;
    conn->CloseNice(100);

    return true;
}

}  // namespace nVerliHub
