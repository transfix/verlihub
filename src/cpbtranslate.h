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

#ifndef CPBTRANSLATE_H
#define CPBTRANSLATE_H

#ifdef WITH_NMDCPB

#include <string>

namespace nVerliHub {

	namespace nProtocol {

		/**
		 * cPbTranslate — Translation layer between NMDCpb protobuf messages
		 * and legacy NMDC text protocol.
		 *
		 * Used by the hub to support mixed environments where some clients
		 * speak NMDCpb and some only understand legacy NMDC.
		 */
		class cPbTranslate {
		public:
			/**
			 * Try to decode a base64-encoded PbEnvelope from a $PB message
			 * and produce a legacy NMDC string for non-NMDCpb clients.
			 *
			 * Returns true if translation was possible (chat, action),
			 * false if the message type has no legacy equivalent (search, connect, etc).
			 *
			 * @param base64data  The base64url-encoded protobuf data from the $PB command
			 * @param nick        The sender's nick (already validated by hub)
			 * @param legacy_out  Output: the legacy NMDC message string (without trailing pipe)
			 * @return true if a legacy translation was produced
			 */
			static bool PbToLegacy(const std::string &base64data, const std::string &nick, std::string &legacy_out);

			/**
			 * Encode a legacy NMDC chat message into a base64-encoded PbEnvelope.
			 *
			 * Used when a legacy client sends a chat message that should be
			 * forwarded to NMDCpb clients in protobuf format.
			 *
			 * @param nick        The sender's nick
			 * @param text        The chat message text
			 * @param is_action   Whether this is a /me action
			 * @param is_pm       Whether this is a PM
			 * @param target_nick PM target nick (empty for public)
			 * @param pb_out      Output: base64url-encoded PbEnvelope
			 * @return true on success
			 */
			static bool LegacyToPb(const std::string &nick, const std::string &text,
				bool is_action, bool is_pm, const std::string &target_nick, std::string &pb_out);

			/**
			 * Base64url encode (RFC 4648 §5, no padding).
			 */
			static std::string Base64UrlEncode(const std::string &input);

			/**
			 * Base64url decode (RFC 4648 §5, no padding).
			 */
			static bool Base64UrlDecode(const std::string &input, std::string &output);

			/**
			 * Check if a base64-encoded PbEnvelope has the ECHO route type.
			 *
			 * Used by DC_PBR to determine whether the message should also
			 * be echoed back to the sender (ADC E-type semantics).
			 *
			 * @param base64data  The base64url-encoded protobuf data
			 * @return true if route is PbEnvelope::ECHO
			 */
			static bool IsEchoRoute(const std::string &base64data);
		};

	} // namespace nProtocol

} // namespace nVerliHub

#endif // WITH_NMDCPB
#endif // CPBTRANSLATE_H
