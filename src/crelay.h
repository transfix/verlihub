/*
	Copyright (C) 2003-2026 Verlihub Team, info at verlihub dot net

	Verlihub is free software; you can redistribute it
	and/or modify it under the terms of the GNU General
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

#ifndef CRELAY_H
#define CRELAY_H

#ifdef WITH_NMDCPB

#include <string>
#include <map>
#include <mutex>
#include <vector>
#include <ctime>

using namespace std;

namespace nVerliHub {
	namespace nSocket {
		class cConnDC;
	}

	namespace nProtocol {

/**
 * A single relay session between two users.
 *
 * Relay sessions allow two passive users (both behind NAT) to exchange
 * data through the hub. The hub acts as a transparent forwarder without
 * inspecting the payload (which is typically encrypted via E2EPM).
 */
struct cRelaySession {
	uint32_t mId;                    // Unique session ID
	nSocket::cConnDC *mConnA;       // Initiator connection
	nSocket::cConnDC *mConnB;       // Responder connection
	string mNickA;                   // Initiator nick (for cleanup when conn drops)
	string mNickB;                   // Responder nick
	string mToken;                   // Random token for handshake correlation
	time_t mCreated;                 // Session creation time
	time_t mLastActivity;            // Last data forwarded
	uint64_t mBytesRelayed;          // Total bytes relayed
	bool mEstablished;               // Both sides accepted

	cRelaySession():
		mId(0),
		mConnA(NULL),
		mConnB(NULL),
		mCreated(0),
		mLastActivity(0),
		mBytesRelayed(0),
		mEstablished(false)
	{}
};

/**
 * Manages hub relay sessions for NMDCpb HubRelay feature.
 *
 * Thread-safe: all public methods acquire mMutex internally.
 */
class cRelayManager {
public:
	cRelayManager();
	~cRelayManager();

	/**
	 * Handle a relay request from a user.
	 * Creates a pending session and returns the session ID (> 0) on success,
	 * or 0 on failure (quota exceeded, target offline, etc.).
	 */
	uint32_t RequestRelay(nSocket::cConnDC *from, const string &targetNick,
	                      const string &token, const string &purpose);

	/**
	 * Handle a relay acknowledgment from the target user.
	 * Returns the session ID if accepted, 0 on failure.
	 */
	uint32_t AckRelay(nSocket::cConnDC *from, const string &token, bool accepted);

	/**
	 * Forward relay data from one peer to the other.
	 * Returns bytes forwarded, or -1 on error.
	 */
	int RelayData(nSocket::cConnDC *from, uint32_t relayId,
	              const string &data);

	/**
	 * Close a relay session.
	 * reason: 0=normal, 1=timeout, 2=error, 3=user_disconnect
	 * Returns 0 on success, -1 if session not found.
	 */
	int CloseRelay(uint32_t relayId, int reason);

	/**
	 * Clean up timed-out sessions. Call from OnTimer.
	 */
	void CleanupTimedOut(time_t now, unsigned int timeoutSec);

	/**
	 * Clean up all sessions for a disconnecting user.
	 */
	void OnUserDisconnect(nSocket::cConnDC *conn);

	/**
	 * Get session count for a specific user.
	 */
	unsigned int GetSessionCount(nSocket::cConnDC *conn);

	/**
	 * Get total active session count.
	 */
	unsigned int GetTotalSessions();

	/**
	 * Get total bytes relayed across all sessions.
	 */
	uint64_t GetTotalBytesRelayed();

private:
	map<uint32_t, cRelaySession> mSessions;
	map<string, uint32_t> mPendingByToken;  // token → session id
	uint32_t mNextId;
	uint64_t mTotalBytesRelayed;
	mutex mMutex;

	// Send a $PB relay-closed notification to a connection
	void SendRelayClosed(nSocket::cConnDC *conn, uint32_t sessionId,
	                     int reason, const string &nick);
};

	} // namespace nProtocol
} // namespace nVerliHub

#endif // WITH_NMDCPB
#endif // CRELAY_H
