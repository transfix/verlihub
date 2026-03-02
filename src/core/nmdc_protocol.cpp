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

#include "nmdc_protocol.h"
#include <random>
#include <sstream>
#include <cstring>

namespace nVerliHub {
namespace NMDCProtocol {

// ============================================================================
// Lock / Key Exchange
// ============================================================================

std::string GenerateLock() {
    static const char charset[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    static std::mt19937 gen{std::random_device{}()};
    static std::uniform_int_distribution<> dis(0, sizeof(charset) - 2);

    std::string lock = "EXTENDEDPROTOCOL";
    // Add random chars to make each lock unique
    for (int i = 0; i < 20; ++i) {
        lock += charset[dis(gen)];
    }
    return lock;
}

std::string UnEscape(const std::string& input) {
    std::string result;
    result.reserve(input.size());

    for (size_t i = 0; i < input.size(); ++i) {
        if (i + 7 < input.size() &&
            input[i] == '/' && input[i+1] == '%' &&
            input[i+2] == 'D' && input[i+3] == 'C' && input[i+4] == 'N') {
            // Parse /%DCNnnn%/
            size_t end = input.find("%/", i + 5);
            if (end != std::string::npos && end - (i + 5) <= 3) {
                std::string num_str = input.substr(i + 5, end - (i + 5));
                int val = std::stoi(num_str);
                result += static_cast<char>(val);
                i = end + 1;  // skip past %/
                continue;
            }
        }
        result += input[i];
    }

    return result;
}

std::string Escape(const std::string& input) {
    std::string result;
    result.reserve(input.size() * 2);

    for (unsigned char c : input) {
        switch (c) {
            case 0:
            case 5:
            case 36:   // $
            case 96:   // `
            case 124:  // |
            case 126:  // ~
            {
                char buf[16];
                snprintf(buf, sizeof(buf), "/%%DCN%03d%%/", c);
                result += buf;
                break;
            }
            default:
                result += static_cast<char>(c);
                break;
        }
    }

    return result;
}

std::string Lock2Key(const std::string& lock) {
    if (lock.size() < 2) return "";

    // Step 1: Unescape the lock
    std::string raw = UnEscape(lock);
    size_t len = raw.size();
    if (len < 2) return "";

    // Step 2: XOR chain
    std::string key(len, '\0');
    key[0] = raw[0] ^ raw[len - 1] ^ raw[len - 2] ^ 5;

    for (size_t i = 1; i < len; ++i) {
        key[i] = raw[i] ^ raw[i - 1];
    }

    // Step 3: Nibble swap
    for (size_t i = 0; i < len; ++i) {
        unsigned char b = static_cast<unsigned char>(key[i]);
        key[i] = static_cast<char>(((b << 4) & 0xF0) | ((b >> 4) & 0x0F));
    }

    // Step 4: Escape special characters
    return Escape(key);
}

// ============================================================================
// Message Construction
// ============================================================================

std::string MakeLock(const std::string& lock) {
    return "$Lock " + lock + " Pk=verlihub-py";
}

std::string MakeSupports() {
    // Note: ZPipe0 removed — we don't implement compression
    return "$Supports UserCommand NoGetINFO NoHello UserIP2 HubINFO BotINFO BotList";
}

std::string MakeHubName(const std::string& name) {
    return "$HubName " + name;
}

/// "$HubName <name> - <topic>|" when topic is non-empty
std::string MakeHubNameWithTopic(const std::string& name, const std::string& topic) {
    if (topic.empty()) return "$HubName " + name;
    return "$HubName " + name + " - " + topic;
}

std::string MakeHello(const std::string& nick) {
    return "$Hello " + nick;
}

std::string MakeGetPass() {
    return "$GetPass";
}

std::string MakeBadPass() {
    return "$BadPass";
}

std::string MakeLoggedIn() {
    return "$LogedIn";
}

std::string MakeValidateDenide(const std::string& nick) {
    return "$ValidateDenide " + nick;
}

std::string MakeHubIsFull() {
    return "$HubIsFull";
}

std::string MakeQuit(const std::string& nick) {
    return "$Quit " + nick;
}

std::string MakeNickList(const std::vector<std::string>& nicks) {
    std::string result = "$NickList ";
    for (const auto& nick : nicks) {
        result += nick;
        result += "$$";
    }
    return result;
}

std::string MakeOpList(const std::vector<std::string>& nicks) {
    std::string result = "$OpList ";
    for (const auto& nick : nicks) {
        result += nick;
        result += "$$";
    }
    return result;
}

std::string MakeBotMyINFO(const std::string& nick, const std::string& desc,
                          const std::string& email) {
    return "$MyINFO $ALL " + nick + " " + desc +
           "$ $Bot\x01$" + email + "$0$";
}

std::string MakeChat(const std::string& from, const std::string& message) {
    return "<" + from + "> " + message;
}

std::string MakePrivateMessage(const std::string& from, const std::string& to,
                               const std::string& message) {
    return "$To: " + to + " From: " + from + " $<" + from + "> " + message;
}

std::string MakeHubTopic(const std::string& topic) {
    return "$HubTopic " + topic;
}

std::string MakeUserIP(const std::string& nick, const std::string& ip) {
    return "$UserIP " + nick + " " + ip + "$$";
}

// ============================================================================
// Message Parsing
// ============================================================================

MyINFOData ParseMyINFO(const std::string& msg) {
    MyINFOData data;

    // Expected format: "$MyINFO $ALL <nick> <desc_and_tag>$ $<speed><flag>$<email>$<share>$"
    const std::string prefix = "$MyINFO $ALL ";
    if (msg.find(prefix) != 0) return data;

    std::string rest = msg.substr(prefix.size());

    // Find first space after nick
    size_t nick_end = rest.find(' ');
    if (nick_end == std::string::npos) return data;

    data.nick = rest.substr(0, nick_end);
    rest = rest.substr(nick_end + 1);

    // Find separator "$ $" between desc and speed
    size_t sep = rest.find("$ $");
    if (sep == std::string::npos) return data;

    std::string desc_part = rest.substr(0, sep);
    rest = rest.substr(sep + 3);  // skip "$ $"

    // Parse tag from description if present
    size_t tag_start = desc_part.find('<');
    size_t tag_end = desc_part.rfind('>');
    if (tag_start != std::string::npos && tag_end != std::string::npos && tag_end > tag_start) {
        data.description = desc_part.substr(0, tag_start);
        data.tag = desc_part.substr(tag_start, tag_end - tag_start + 1);
    } else {
        data.description = desc_part;
    }

    // Parse speed$email$share$
    // speed ends at first $
    // The last byte of speed is a status flag byte (away/TLS/firewall/etc.)
    size_t pos = rest.find('$');
    if (pos == std::string::npos) return data;
    std::string speed_raw = rest.substr(0, pos);
    if (!speed_raw.empty()) {
        data.status_flag = static_cast<unsigned char>(speed_raw.back());
        data.speed = speed_raw.substr(0, speed_raw.size() - 1);
    }
    rest = rest.substr(pos + 1);

    // email ends at next $
    pos = rest.find('$');
    if (pos == std::string::npos) return data;
    data.email = rest.substr(0, pos);
    rest = rest.substr(pos + 1);

    // share ends at next $
    pos = rest.find('$');
    if (pos != std::string::npos) {
        std::string share_str = rest.substr(0, pos);
        try {
            data.share_size = std::stoull(share_str);
        } catch (...) {
            data.share_size = 0;
        }
    }

    data.valid = true;
    return data;
}

PrivateMessageData ParsePrivateMessage(const std::string& msg) {
    PrivateMessageData data;

    // "$To: <to> From: <from> $<<from>> <message>"
    if (msg.find("$To: ") != 0) return data;

    size_t from_pos = msg.find(" From: ");
    if (from_pos == std::string::npos) return data;

    data.to = msg.substr(5, from_pos - 5);
    size_t msg_sep = msg.find(" $<", from_pos);
    if (msg_sep == std::string::npos) return data;

    data.from = msg.substr(from_pos + 7, msg_sep - (from_pos + 7));

    size_t msg_start = msg.find("> ", msg_sep);
    if (msg_start == std::string::npos) return data;

    data.message = msg.substr(msg_start + 2);
    data.valid = true;
    return data;
}

ChatMessageData ParseChat(const std::string& msg) {
    ChatMessageData data;

    if (msg.empty() || msg[0] != '<') return data;

    size_t end = msg.find("> ");
    if (end == std::string::npos) return data;

    data.nick = msg.substr(1, end - 1);
    data.message = msg.substr(end + 2);
    data.valid = true;
    return data;
}

bool IsCommand(const std::string& msg, const std::string& cmd) {
    if (msg.size() < cmd.size()) return false;
    if (msg.compare(0, cmd.size(), cmd) != 0) return false;
    // Must be followed by space, | or end of string
    if (msg.size() > cmd.size()) {
        char next = msg[cmd.size()];
        return (next == ' ' || next == '|');
    }
    return true;
}

std::string GetCommandParam(const std::string& msg, const std::string& cmd) {
    if (!IsCommand(msg, cmd)) return "";
    if (msg.size() <= cmd.size() + 1) return "";
    return msg.substr(cmd.size() + 1);
}

// ============================================================================
// Tag Parsing
// ============================================================================

TagData ParseTag(const std::string& tag) {
    TagData data;
    if (tag.size() < 3 || tag.front() != '<' || tag.back() != '>') return data;

    // Strip < and >
    std::string inner = tag.substr(1, tag.size() - 2);

    // Client name is everything before " V:" (or the whole string if no V:)
    auto vpos = inner.find(" V:");
    if (vpos != std::string::npos) {
        data.client_name = inner.substr(0, vpos);
        // Version is from V: to the next comma or end
        size_t vstart = vpos + 3;
        size_t vend = inner.find(',', vstart);
        data.client_version = (vend != std::string::npos)
            ? inner.substr(vstart, vend - vstart)
            : inner.substr(vstart);
    } else {
        data.client_name = inner;
    }

    // Parse comma-separated key:value pairs after version
    // M:A|P|5  H:normal/registered/op  S:slots  L:limit
    size_t pos = inner.find(',');
    while (pos != std::string::npos) {
        size_t next = inner.find(',', pos + 1);
        std::string field = (next != std::string::npos)
            ? inner.substr(pos + 1, next - pos - 1)
            : inner.substr(pos + 1);

        if (field.size() >= 2 && field[1] == ':') {
            char key = field[0];
            std::string val = field.substr(2);

            switch (key) {
                case 'M':
                    if (!val.empty()) data.mode = val[0];
                    break;
                case 'S':
                    try { data.slots = std::stoi(val); } catch (...) {}
                    break;
                case 'L':
                    try { data.upload_limit = std::stoi(val); } catch (...) {}
                    break;
                case 'H': {
                    // H:normal/registered/op
                    size_t s1 = val.find('/');
                    if (s1 != std::string::npos) {
                        try { data.hubs_normal = std::stoi(val.substr(0, s1)); } catch (...) {}
                        size_t s2 = val.find('/', s1 + 1);
                        if (s2 != std::string::npos) {
                            try { data.hubs_registered = std::stoi(val.substr(s1 + 1, s2 - s1 - 1)); } catch (...) {}
                            try { data.hubs_operator = std::stoi(val.substr(s2 + 1)); } catch (...) {}
                        } else {
                            try { data.hubs_registered = std::stoi(val.substr(s1 + 1)); } catch (...) {}
                        }
                    } else {
                        try { data.hubs_normal = std::stoi(val); } catch (...) {}
                    }
                    break;
                }
                default:
                    break;
            }
        }
        pos = next;
    }

    data.valid = !data.client_name.empty();
    return data;
}

// ============================================================================
// Search Result Parsing
// ============================================================================

SearchResultData ParseSR(const std::string& msg) {
    SearchResultData data;

    // "$SR <nick> <result_payload>\x05<to_nick>"
    const std::string prefix = "$SR ";
    if (msg.find(prefix) != 0) return data;

    std::string rest = msg.substr(prefix.size());

    // Nick is up to first space
    size_t nick_end = rest.find(' ');
    if (nick_end == std::string::npos) return data;
    data.from_nick = rest.substr(0, nick_end);

    // Find the \x05 separator for target nick
    size_t sep = rest.rfind('\x05');
    if (sep != std::string::npos && sep > nick_end) {
        data.payload = rest.substr(nick_end + 1, sep - nick_end - 1);
        data.to_nick = rest.substr(sep + 1);
    } else {
        // No target nick — active search result broadcast
        data.payload = rest.substr(nick_end + 1);
    }

    data.valid = !data.from_nick.empty();
    return data;
}

// ============================================================================
// Additional Message Construction
// ============================================================================

std::string MakeForceMove(const std::string& address) {
    return "$ForceMove " + address;
}

std::string MakeBotList(const std::vector<std::string>& nicks) {
    std::string result = "$BotList ";
    for (const auto& nick : nicks) {
        result += nick;
        result += "$$";
    }
    return result;
}

}  // namespace NMDCProtocol
}  // namespace nVerliHub
