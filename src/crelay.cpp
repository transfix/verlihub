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

#ifdef WITH_NMDCPB

#include "crelay.h"
#include "cconndc.h"
#include "cuser.h"
#include <sstream>

using namespace std;

namespace nVerliHub {
	namespace nProtocol {

cRelayManager::cRelayManager():
	mNextId(1),
	mTotalBytesRelayed(0)
{
	mGlobalBandwidth.Init(10485760, time(NULL)); // 10 MB/s default
}

cRelayManager::~cRelayManager()
{
	lock_guard<mutex> lock(mMutex);
	mSessions.clear();
	mPendingByToken.clear();
	mUserBandwidth.clear();
}

uint32_t cRelayManager::RequestRelay(nSocket::cConnDC *from, const string &targetNick,
                                      const string &token, const string &purpose)
{
	if (!from || targetNick.empty() || token.empty())
		return 0;

	lock_guard<mutex> lock(mMutex);

	// Check if token is already in use
	if (mPendingByToken.find(token) != mPendingByToken.end())
		return 0;

	// Create session
	uint32_t id = mNextId++;
	cRelaySession &sess = mSessions[id];
	sess.mId = id;
	sess.mConnA = from;
	sess.mNickA = from->mpUser ? from->mpUser->mNick : "";
	sess.mNickB = targetNick;
	sess.mToken = token;
	sess.mPurpose = purpose;
	sess.mCreated = time(NULL);
	sess.mLastActivity = sess.mCreated;
	sess.mBytesRelayed = 0;
	sess.mEstablished = false;

	mPendingByToken[token] = id;

	return id;
}

uint32_t cRelayManager::AckRelay(nSocket::cConnDC *from, const string &token, bool accepted)
{
	if (!from || token.empty())
		return 0;

	lock_guard<mutex> lock(mMutex);

	auto pit = mPendingByToken.find(token);

	if (pit == mPendingByToken.end())
		return 0;

	uint32_t id = pit->second;
	mPendingByToken.erase(pit);

	auto sit = mSessions.find(id);

	if (sit == mSessions.end())
		return 0;

	if (!accepted) {
		// Notify initiator that relay was rejected
		if (sit->second.mConnA)
			SendRelayClosed(sit->second.mConnA, id, 4, sit->second.mNickB); // 4 = rejected

		mSessions.erase(sit);
		return 0;
	}

	// Accept: set connection B and mark established
	sit->second.mConnB = from;
	sit->second.mEstablished = true;
	sit->second.mLastActivity = time(NULL);

	return id;
}

int cRelayManager::RelayData(nSocket::cConnDC *from, uint32_t relayId,
                             const string &data, uint64_t maxPayload,
                             uint64_t perUserRate, uint64_t globalRate)
{
	if (!from || data.empty())
		return -1;

	lock_guard<mutex> lock(mMutex);

	auto it = mSessions.find(relayId);

	if (it == mSessions.end())
		return -1;

	cRelaySession &sess = it->second;

	if (!sess.mEstablished)
		return -1;

	// Check payload size
	if (maxPayload > 0 && data.size() > maxPayload)
		return -4;

	// Determine which peer to forward to
	nSocket::cConnDC *target = NULL;

	if (from == sess.mConnA)
		target = sess.mConnB;
	else if (from == sess.mConnB)
		target = sess.mConnA;
	else
		return -1; // sender is not part of this session

	if (!target)
		return -1;

	time_t now = time(NULL);
	uint64_t sz = data.size();

	// Per-user bandwidth throttle
	if (perUserRate > 0) {
		cBandwidthBucket &ub = GetUserBucket(from, perUserRate);
		ub.Refill(now, perUserRate);

		if (!ub.Consume(sz))
			return -2; // per-user limit exceeded
	}

	// Global bandwidth throttle
	if (globalRate > 0) {
		mGlobalBandwidth.Refill(now, globalRate);

		if (!mGlobalBandwidth.Consume(sz))
			return -3; // global limit exceeded
	}

	// Forward the data
	string omsg(data);
	target->Send(omsg, true);

	sess.mBytesRelayed += sz;
	sess.mLastActivity = now;
	mTotalBytesRelayed += sz;

	return (int)sz;
}

int cRelayManager::CloseRelay(uint32_t relayId, int reason)
{
	lock_guard<mutex> lock(mMutex);

	auto it = mSessions.find(relayId);

	if (it == mSessions.end())
		return -1;

	cRelaySession &sess = it->second;

	// Notify both peers
	if (sess.mConnA)
		SendRelayClosed(sess.mConnA, relayId, reason, sess.mNickB);

	if (sess.mConnB)
		SendRelayClosed(sess.mConnB, relayId, reason, sess.mNickA);

	// Remove pending token if still there
	if (!sess.mToken.empty()) {
		auto pit = mPendingByToken.find(sess.mToken);

		if (pit != mPendingByToken.end() && pit->second == relayId)
			mPendingByToken.erase(pit);
	}

	mSessions.erase(it);
	return 0;
}

void cRelayManager::CleanupTimedOut(time_t now, unsigned int timeoutSec)
{
	lock_guard<mutex> lock(mMutex);

	vector<uint32_t> expired;

	for (auto &kv : mSessions) {
		time_t last = kv.second.mLastActivity;

		if (!last)
			last = kv.second.mCreated;

		if ((now - last) > (time_t)timeoutSec)
			expired.push_back(kv.first);
	}

	for (uint32_t id : expired) {
		auto it = mSessions.find(id);

		if (it == mSessions.end())
			continue;

		cRelaySession &sess = it->second;

		// Notify peers about timeout
		if (sess.mConnA)
			SendRelayClosed(sess.mConnA, id, 1, sess.mNickB); // 1 = timeout

		if (sess.mConnB)
			SendRelayClosed(sess.mConnB, id, 1, sess.mNickA);

		// Clean up pending token
		if (!sess.mToken.empty()) {
			auto pit = mPendingByToken.find(sess.mToken);

			if (pit != mPendingByToken.end() && pit->second == id)
				mPendingByToken.erase(pit);
		}

		mSessions.erase(it);
	}
}

void cRelayManager::OnUserDisconnect(nSocket::cConnDC *conn)
{
	if (!conn)
		return;

	lock_guard<mutex> lock(mMutex);

	vector<uint32_t> toRemove;

	for (auto &kv : mSessions) {
		if (kv.second.mConnA == conn || kv.second.mConnB == conn)
			toRemove.push_back(kv.first);
	}

	for (uint32_t id : toRemove) {
		auto it = mSessions.find(id);

		if (it == mSessions.end())
			continue;

		cRelaySession &sess = it->second;

		// Notify the other peer
		nSocket::cConnDC *other = (sess.mConnA == conn) ? sess.mConnB : sess.mConnA;
		string otherNick = (sess.mConnA == conn) ? sess.mNickA : sess.mNickB;

		if (other)
			SendRelayClosed(other, id, 3, otherNick); // 3 = user_disconnect

		// Clean up token
		if (!sess.mToken.empty()) {
			auto pit = mPendingByToken.find(sess.mToken);

			if (pit != mPendingByToken.end() && pit->second == id)
				mPendingByToken.erase(pit);
		}

		mSessions.erase(it);
	}
}

unsigned int cRelayManager::GetSessionCount(nSocket::cConnDC *conn)
{
	lock_guard<mutex> lock(mMutex);

	unsigned int count = 0;

	for (auto &kv : mSessions) {
		if (kv.second.mConnA == conn || kv.second.mConnB == conn)
			count++;
	}

	return count;
}

unsigned int cRelayManager::GetTotalSessions()
{
	lock_guard<mutex> lock(mMutex);
	return (unsigned int)mSessions.size();
}

uint64_t cRelayManager::GetTotalBytesRelayed()
{
	lock_guard<mutex> lock(mMutex);
	return mTotalBytesRelayed;
}

void cRelayManager::SendRelayClosed(nSocket::cConnDC *conn, uint32_t sessionId,
                                     int reason, const string &nick)
{
	if (!conn)
		return;

	// Build a simple relay-closed JSON notification sent as $PBR
	// Format: $PBR <to> Hub RELAY_CLOSED:<id>:<reason>|
	// Clients parse this to detect session closure before timeout.
	string toNick;

	if (conn->mpUser)
		toNick = conn->mpUser->mNick;

	if (toNick.empty())
		return;

	ostringstream os;
	os << "$PBR " << toNick << " Hub RELAY_CLOSED:" << sessionId << ":" << reason << "|";
	string msg = os.str();
	conn->Send(msg, true);
}

cBandwidthBucket &cRelayManager::GetUserBucket(nSocket::cConnDC *conn, uint64_t defaultRate)
{
	auto it = mUserBandwidth.find(conn);

	if (it == mUserBandwidth.end()) {
		cBandwidthBucket &bkt = mUserBandwidth[conn];
		bkt.Init(defaultRate, time(NULL));
		return bkt;
	}

	return it->second;
}

void cRelayManager::SetUserBandwidthCap(nSocket::cConnDC *conn, uint64_t rate)
{
	if (!conn)
		return;

	lock_guard<mutex> lock(mMutex);

	if (rate == 0) {
		// Remove custom cap — fall back to global default
		mUserBandwidth.erase(conn);
		return;
	}

	cBandwidthBucket &bkt = mUserBandwidth[conn];
	bkt.Init(rate, time(NULL));
}

cRelayStats cRelayManager::GetStats()
{
	lock_guard<mutex> lock(mMutex);

	cRelayStats stats;
	stats.mActiveSessions = 0;
	stats.mPendingSessions = 0;
	stats.mTotalBytesRelayed = mTotalBytesRelayed;
	stats.mGlobalBandwidthUsed = mGlobalBandwidth.mTotalBytes;

	// Per-user aggregation
	map<string, cRelayStats::UserRelayStat> perUser;

	for (auto &kv : mSessions) {
		const cRelaySession &sess = kv.second;

		if (sess.mEstablished)
			stats.mActiveSessions++;
		else
			stats.mPendingSessions++;

		// Count for nick A
		if (!sess.mNickA.empty()) {
			auto &u = perUser[sess.mNickA];
			u.mNick = sess.mNickA;
			u.mSessions++;
			u.mBytesRelayed += sess.mBytesRelayed / 2; // split evenly
		}

		// Count for nick B
		if (!sess.mNickB.empty()) {
			auto &u = perUser[sess.mNickB];
			u.mNick = sess.mNickB;
			u.mSessions++;
			u.mBytesRelayed += sess.mBytesRelayed / 2;
		}
	}

	// Add per-user bandwidth info from buckets
	for (auto &kv : mUserBandwidth) {
		string nick;

		if (kv.first && kv.first->mpUser)
			nick = kv.first->mpUser->mNick;

		if (!nick.empty() && perUser.count(nick)) {
			perUser[nick].mBandwidthUsed = kv.second.mTotalBytes;
		}
	}

	for (auto &kv : perUser)
		stats.mPerUser.push_back(kv.second);

	return stats;
}

	} // namespace nProtocol
} // namespace nVerliHub

#endif // WITH_NMDCPB
