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
	string mPurpose;                 // Purpose (e.g. "file_transfer", "e2epm")
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
 * Token-bucket rate limiter for per-user bandwidth throttling.
 */
struct cBandwidthBucket {
	uint64_t mTokens;        // Available bytes (tokens)
	uint64_t mCapacity;      // Max burst (= rate * 1 second)
	time_t mLastRefill;      // Last refill timestamp
	uint64_t mTotalBytes;    // Lifetime total bytes

	cBandwidthBucket(): mTokens(0), mCapacity(0), mLastRefill(0), mTotalBytes(0) {}

	void Init(uint64_t rate, time_t now) {
		mCapacity = rate;
		mTokens = rate;
		mLastRefill = now;
	}

	/// Refill tokens based on elapsed time. Updates capacity to match current rate.
	uint64_t Refill(time_t now, uint64_t rate) {
		mCapacity = rate; // track current rate as burst limit
		if (now > mLastRefill) {
			uint64_t elapsed = (uint64_t)(now - mLastRefill);
			uint64_t add = elapsed * rate;
			mTokens = min(mTokens + add, mCapacity);
			mLastRefill = now;
		}
		// Clamp to new capacity (handles rate decreases)
		if (mTokens > mCapacity)
			mTokens = mCapacity;
		return mTokens;
	}

	/// Try to consume n bytes. Returns true if allowed.
	bool Consume(uint64_t n) {
		if (mTokens >= n) {
			mTokens -= n;
			mTotalBytes += n;
			return true;
		}
		return false;
	}
};

/**
 * Snapshot of relay statistics for dashboard/API.
 */
struct cRelayStats {
	unsigned int mActiveSessions;
	unsigned int mPendingSessions;
	uint64_t mTotalBytesRelayed;
	uint64_t mGlobalBandwidthUsed;  // bytes/sec estimate

	struct UserRelayStat {
		string mNick;
		unsigned int mSessions;
		uint64_t mBytesRelayed;
		uint64_t mBandwidthUsed;
	};

	vector<UserRelayStat> mPerUser;
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
	 * Applies per-user and global bandwidth throttling.
	 * Returns bytes forwarded, or negative on error:
	 *   -1 = session not found / not established / wrong peer
	 *   -2 = per-user bandwidth limit exceeded
	 *   -3 = global bandwidth limit exceeded
	 *   -4 = payload too large
	 */
	int RelayData(nSocket::cConnDC *from, uint32_t relayId,
	              const string &data, uint64_t maxPayload,
	              uint64_t perUserRate, uint64_t globalRate);

	/**
	 * Close a relay session.
	 * reason: 0=normal, 1=timeout, 2=error, 3=user_disconnect, 4=rejected
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
	 * Set bandwidth cap for an active connection.
	 * Used by dashboard to dynamically throttle a user.
	 * rate = 0 means unlimited (use global default).
	 */
	void SetUserBandwidthCap(nSocket::cConnDC *conn, uint64_t rate);

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

	/**
	 * Collect relay statistics snapshot for dashboard/API.
	 */
	cRelayStats GetStats();

private:
	map<uint32_t, cRelaySession> mSessions;
	map<string, uint32_t> mPendingByToken;  // token → session id
	map<nSocket::cConnDC *, cBandwidthBucket> mUserBandwidth; // per-user throttle
	cBandwidthBucket mGlobalBandwidth;  // hub-wide throttle
	uint32_t mNextId;
	uint64_t mTotalBytesRelayed;
	mutex mMutex;

	// Get or create a per-user bandwidth bucket
	cBandwidthBucket &GetUserBucket(nSocket::cConnDC *conn, uint64_t defaultRate);

	// Send a $PBR relay-closed notification to a connection
	void SendRelayClosed(nSocket::cConnDC *conn, uint32_t sessionId,
	                     int reason, const string &nick);
};

	} // namespace nProtocol
} // namespace nVerliHub

#endif // WITH_NMDCPB
#endif // CRELAY_H
