/*
	Copyright (C) 2003-2005 Daniel Muller, dan at verliba dot cz
	Copyright (C) 2006-2025 Verlihub Team, info at verlihub dot net

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

	Please see https://www.gnu.org/licenses/ for a copy
	of the GNU General Public License.
*/

#ifdef WITH_NMDCPB

#include "cpbtranslate.h"
#include "nmdcpb.pb.h"
#include <sstream>
#include <cstdint>
#include <cstring>
#include <chrono>

namespace nVerliHub {
namespace nProtocol {

// ---------------------------------------------------------------------------
// Base64url encode/decode (RFC 4648 §5, no padding)
// ---------------------------------------------------------------------------

static const char kBase64UrlChars[] =
	"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

static const unsigned char kBase64UrlDecodeTable[256] = {
	// 0-42: invalid
	255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
	255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
	255,255,255,255,255,255,255,255,255,255,255,255,255,
	// 45 '-' = 62
	62,
	// 46 '.': invalid
	255,
	// 47 '/': invalid
	255,
	// 48-57 '0'-'9' = 52-61
	52,53,54,55,56,57,58,59,60,61,
	// 58-64: invalid
	255,255,255,255,255,255,255,
	// 65-90 'A'-'Z' = 0-25
	0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,
	// 91-96: invalid
	255,255,255,255,
	// 95 '_' = 63
	63,
	// 96: invalid
	255,
	// 97-122 'a'-'z' = 26-51
	26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,
	// 123-255: invalid
	255,255,255,255,255,
	255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
	255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
	255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
	255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
	255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
	255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
	255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
	255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255
};

std::string cPbTranslate::Base64UrlEncode(const std::string &input)
{
	std::string out;
	size_t len = input.size();

	if (!len)
		return out;

	out.reserve(((len + 2) / 3) * 4);

	const unsigned char *data = reinterpret_cast<const unsigned char *>(input.data());
	size_t i = 0;

	for (; i + 2 < len; i += 3) {
		uint32_t n = (static_cast<uint32_t>(data[i]) << 16) |
			(static_cast<uint32_t>(data[i + 1]) << 8) |
			static_cast<uint32_t>(data[i + 2]);

		out += kBase64UrlChars[(n >> 18) & 0x3F];
		out += kBase64UrlChars[(n >> 12) & 0x3F];
		out += kBase64UrlChars[(n >> 6) & 0x3F];
		out += kBase64UrlChars[n & 0x3F];
	}

	if (i + 1 == len) {
		uint32_t n = static_cast<uint32_t>(data[i]) << 16;
		out += kBase64UrlChars[(n >> 18) & 0x3F];
		out += kBase64UrlChars[(n >> 12) & 0x3F];
	} else if (i + 2 == len) {
		uint32_t n = (static_cast<uint32_t>(data[i]) << 16) |
			(static_cast<uint32_t>(data[i + 1]) << 8);

		out += kBase64UrlChars[(n >> 18) & 0x3F];
		out += kBase64UrlChars[(n >> 12) & 0x3F];
		out += kBase64UrlChars[(n >> 6) & 0x3F];
	}

	return out;
}

bool cPbTranslate::Base64UrlDecode(const std::string &input, std::string &output)
{
	output.clear();
	size_t len = input.size();

	if (!len)
		return true;

	// pad to multiple of 4
	size_t padded = len;

	while (padded % 4)
		padded++;

	output.reserve((padded / 4) * 3);

	size_t i = 0;

	while (i < len) {
		uint32_t sextet[4] = {0, 0, 0, 0};
		int count = 0;

		while (count < 4 && i < len) {
			unsigned char c = static_cast<unsigned char>(input[i++]);
			unsigned char val = kBase64UrlDecodeTable[c];

			if (val == 255) // skip padding chars '=' as well
				continue;

			sextet[count++] = val;
		}

		if (count >= 2) {
			output += static_cast<char>((sextet[0] << 2) | (sextet[1] >> 4));
		}

		if (count >= 3) {
			output += static_cast<char>(((sextet[1] & 0x0F) << 4) | (sextet[2] >> 2));
		}

		if (count >= 4) {
			output += static_cast<char>(((sextet[2] & 0x03) << 6) | sextet[3]);
		}
	}

	return true;
}

// ---------------------------------------------------------------------------
// PbToLegacy — decode NMDCpb and produce legacy NMDC text
// ---------------------------------------------------------------------------

bool cPbTranslate::PbToLegacy(const std::string &base64data, const std::string &nick, std::string &legacy_out)
{
	// decode base64url
	std::string raw;

	if (!Base64UrlDecode(base64data, raw))
		return false;

	// parse protobuf envelope
	nmdcpb::PbEnvelope envelope;

	if (!envelope.ParseFromString(raw))
		return false;

	// only chat messages have a legacy equivalent
	if (!envelope.has_chat())
		return false;

	const nmdcpb::PbChat &chat = envelope.chat();

	if (chat.is_pm()) {
		// PM: $To: <target> From: <hub_or_sender> $<<nick>> <text>
		// We produce the standard NMDC PM format
		const std::string &target = chat.target_nick();

		if (target.empty())
			return false;

		std::ostringstream os;

		if (chat.is_action())
			os << "$To: " << target << " From: " << nick << " $<" << nick << "> /me " << chat.text();
		else
			os << "$To: " << target << " From: " << nick << " $<" << nick << "> " << chat.text();

		legacy_out = os.str();
	} else {
		// Public chat: <<nick>> <text>
		std::ostringstream os;

		if (chat.is_action())
			os << "* " << nick << " " << chat.text();
		else
			os << "<" << nick << "> " << chat.text();

		legacy_out = os.str();
	}

	return true;
}

// ---------------------------------------------------------------------------
// LegacyToPb — encode legacy NMDC chat into NMDCpb protobuf
// ---------------------------------------------------------------------------

bool cPbTranslate::LegacyToPb(const std::string &nick, const std::string &text,
	bool is_action, bool is_pm, const std::string &target_nick, std::string &pb_out)
{
	nmdcpb::PbEnvelope envelope;

	// set routing
	if (is_pm) {
		envelope.set_route(nmdcpb::PbEnvelope::DIRECT);
		envelope.set_to_nick(target_nick);
	} else {
		envelope.set_route(nmdcpb::PbEnvelope::BROADCAST);
	}

	envelope.set_from_nick(nick);

	// timestamp
	auto now = std::chrono::system_clock::now();
	auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch());
	envelope.set_timestamp(static_cast<uint64_t>(ms.count()));

	// chat payload
	nmdcpb::PbChat *chat = envelope.mutable_chat();
	chat->set_text(text);
	chat->set_is_action(is_action);
	chat->set_is_pm(is_pm);

	if (is_pm)
		chat->set_target_nick(target_nick);

	// serialize and base64url encode
	std::string raw;

	if (!envelope.SerializeToString(&raw))
		return false;

	pb_out = Base64UrlEncode(raw);
	return true;
}

bool cPbTranslate::IsEchoRoute(const std::string &base64data)
{
	std::string decoded;

	if (!Base64UrlDecode(base64data, decoded))
		return false;

	nmdcpb::PbEnvelope envelope;

	if (!envelope.ParseFromString(decoded))
		return false;

	return envelope.route() == nmdcpb::PbEnvelope::ECHO;
}

} // namespace nProtocol
} // namespace nVerliHub

#endif // WITH_NMDCPB
