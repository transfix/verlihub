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

#ifndef NMDC_PROTOCOL_H
#define NMDC_PROTOCOL_H

/**
 * @file nmdc_protocol.h
 * @brief Standalone NMDC protocol utilities for verlihub-py.
 *
 * This module provides NMDC protocol message construction and parsing
 * without any database dependency. It is part of the verlihub-py core
 * library and replaces the legacy cDCProto for the database-free hub
 * server.
 */

#include <string>
#include <vector>
#include <cstdint>

namespace nVerliHub {

namespace NMDCProtocol {

// ============================================================================
// Lock / Key Exchange
// ============================================================================

/// Generate a random lock string for the NMDC handshake
std::string GenerateLock();

/**
 * Convert an NMDC $Lock to the corresponding $Key (server-side algorithm).
 *
 * Algorithm:
 * 1. key[0] = lock[0] ^ lock[len-1] ^ lock[len-2] ^ 5
 * 2. key[i] = lock[i] ^ lock[i-1]
 * 3. Nibble swap each byte
 * 4. Escape special characters as /%DCNnnn%/
 */
std::string Lock2Key(const std::string& lock);

/// Unescape /%DCNnnn%/ sequences in a string
std::string UnEscape(const std::string& input);

/// Escape special NMDC characters (0, 5, 36, 96, 124) as /%DCNnnn%/
std::string Escape(const std::string& input);

// ============================================================================
// Message Construction
// ============================================================================

/// "$Lock <lock> Pk=verlihub-py|"
std::string MakeLock(const std::string& lock);

/// "$Supports <features>|"
std::string MakeSupports();

/// "$HubName <name>|"
std::string MakeHubName(const std::string& name);

/// "$Hello <nick>|"
std::string MakeHello(const std::string& nick);

/// "$GetPass|"
std::string MakeGetPass();

/// "$BadPass|"
std::string MakeBadPass();

/// "$LogedIn|"
std::string MakeLoggedIn();

/// "$ValidateDenide <nick>|" (typo is standard NMDC)
std::string MakeValidateDenide(const std::string& nick);

/// "$HubIsFull|"
std::string MakeHubIsFull();

/// "$Quit <nick>|"
std::string MakeQuit(const std::string& nick);

/// "$NickList nick1$$nick2$$|"
std::string MakeNickList(const std::vector<std::string>& nicks);

/// "$OpList nick1$$nick2$$|"
std::string MakeOpList(const std::vector<std::string>& nicks);

/// "$MyINFO $ALL <nick> <desc>$ $<speed><flag>$<email>$<share>$|"
std::string MakeBotMyINFO(const std::string& nick, const std::string& desc,
                          const std::string& email = "");

/// "<from> message|"
std::string MakeChat(const std::string& from, const std::string& message);

/// "$To: <to> From: <from> $<<from>> <message>|"
std::string MakePrivateMessage(const std::string& from, const std::string& to,
                               const std::string& message);

/// "$HubTopic <topic>|"
std::string MakeHubTopic(const std::string& topic);

/// "$UserIP <nick> <ip>$$|"
std::string MakeUserIP(const std::string& nick, const std::string& ip);

// ============================================================================
// Message Parsing
// ============================================================================

/// Parsed $MyINFO data
struct MyINFOData {
    std::string nick;
    std::string description;
    std::string tag;
    std::string speed;
    std::string email;
    uint64_t share_size{0};
    bool valid{false};
};

/// Parse "$MyINFO $ALL <nick> <desc>$ $<speed><flag>$<email>$<share>$"
MyINFOData ParseMyINFO(const std::string& msg);

/// Parsed private message data
struct PrivateMessageData {
    std::string to;
    std::string from;
    std::string message;
    bool valid{false};
};

/// Parse "$To: <nick> From: <from> $<<from>> <message>"
PrivateMessageData ParsePrivateMessage(const std::string& msg);

/// Parsed main chat data
struct ChatMessageData {
    std::string nick;
    std::string message;
    bool valid{false};
};

/// Parse "<nick> message"
ChatMessageData ParseChat(const std::string& msg);

/// Check if a message starts with a specific NMDC command
bool IsCommand(const std::string& msg, const std::string& cmd);

/// Get the parameter after a command, e.g. "$ValidateNick test" → "test"
std::string GetCommandParam(const std::string& msg, const std::string& cmd);

}  // namespace NMDCProtocol
}  // namespace nVerliHub

#endif  // NMDC_PROTOCOL_H
