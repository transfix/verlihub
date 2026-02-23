/*
	NMDCpb Relay Manager C++ Unit Tests

	Tests cRelayManager session lifecycle:
	- Request/Ack/Close relay sessions
	- Timeout cleanup
	- User disconnect cleanup
	- Session counting

	Uses unity-build approach: blocks real cconndc.h/cuser.h via include
	guards and provides minimal stubs, then #includes crelay.cpp directly.

	Build: requires WITH_NMDCPB defined (no protobuf needed).
	Run: ctest --test-dir build -R relay or ./build/test_relay
*/

#ifdef WITH_NMDCPB

// Block real headers via include guards
#define CCONNDC_H
#define CUSER_H

#include <iostream>
#include <string>
#include <cassert>
#include <cstring>
#include <ctime>
#include <sstream>

using namespace std;

// Minimal stubs for types that crelay.cpp accesses
namespace nVerliHub {

	class cUserBase {
	public:
		string mNick;
		cUserBase(const string &nick = "") : mNick(nick) {}
		virtual ~cUserBase() {}
	};

	class cUser : public cUserBase {
	public:
		cUser(const string &nick = "") : cUserBase(nick) {}
	};

	namespace nSocket {
		class cConnDC {
		public:
			cUser *mpUser;
			unsigned long mFeatures;

			cConnDC() : mpUser(NULL), mFeatures(0) {}

			int Send(string &data, bool AddPipe = true, bool Flush = true) {
				// Stub: just return data size to indicate success
				return (int)data.size();
			}
		};
	}
}

// Unity-build: include crelay.cpp directly (it skips blocked headers)
#include "crelay.cpp"

using namespace nVerliHub;
using namespace nVerliHub::nProtocol;
using namespace nVerliHub::nSocket;

static int g_tests_run = 0;
static int g_tests_passed = 0;

#define TEST(name) \
	do { \
		g_tests_run++; \
		cout << "  [" << g_tests_run << "] " << #name << " ... "; \
	} while(0)

#define PASS() \
	do { \
		g_tests_passed++; \
		cout << "PASS" << endl; \
	} while(0)

#define FAIL(msg) \
	do { \
		cout << "FAIL: " << msg << endl; \
	} while(0)

#define ASSERT_TRUE(cond) \
	do { \
		if (!(cond)) { FAIL(#cond " is false"); return; } \
	} while(0)

#define ASSERT_EQ(a, b) \
	do { \
		if ((a) != (b)) { FAIL(#a " != " #b); return; } \
	} while(0)

#define ASSERT_GT(a, b) \
	do { \
		if (!((a) > (b))) { FAIL(#a " not > " #b); return; } \
	} while(0)

// --- Fake connections for testing ---

static cUser g_userA("Alice");
static cUser g_userB("Bob");
static cUser g_userC("Charlie");

static cConnDC g_connA;
static cConnDC g_connB;
static cConnDC g_connC;

void setup_fake_conns() {
	g_connA.mpUser = &g_userA;
	g_connB.mpUser = &g_userB;
	g_connC.mpUser = &g_userC;
}

// ===== Tests =====

void test_request_relay()
{
	TEST(request_relay);
	cRelayManager mgr;
	setup_fake_conns();

	uint32_t id = mgr.RequestRelay(&g_connA, "Bob", "token123", "e2epm");
	ASSERT_GT(id, (uint32_t)0);
	ASSERT_EQ(mgr.GetTotalSessions(), 1u);
	PASS();
}

void test_request_duplicate_token_fails()
{
	TEST(request_duplicate_token_fails);
	cRelayManager mgr;
	setup_fake_conns();

	uint32_t id1 = mgr.RequestRelay(&g_connA, "Bob", "tok1", "e2epm");
	ASSERT_GT(id1, (uint32_t)0);

	uint32_t id2 = mgr.RequestRelay(&g_connA, "Charlie", "tok1", "e2epm");
	ASSERT_EQ(id2, (uint32_t)0); // duplicate token
	PASS();
}

void test_request_null_fails()
{
	TEST(request_null_fails);
	cRelayManager mgr;

	ASSERT_EQ(mgr.RequestRelay(nullptr, "Bob", "tok", "e2epm"), (uint32_t)0);
	ASSERT_EQ(mgr.RequestRelay(&g_connA, "", "tok", "e2epm"), (uint32_t)0);
	ASSERT_EQ(mgr.RequestRelay(&g_connA, "Bob", "", "e2epm"), (uint32_t)0);
	PASS();
}

void test_ack_relay()
{
	TEST(ack_relay);
	cRelayManager mgr;
	setup_fake_conns();

	uint32_t id = mgr.RequestRelay(&g_connA, "Bob", "tok_ack", "e2epm");
	ASSERT_GT(id, (uint32_t)0);

	uint32_t acked = mgr.AckRelay(&g_connB, "tok_ack", true);
	ASSERT_EQ(acked, id);
	ASSERT_EQ(mgr.GetTotalSessions(), 1u);
	PASS();
}

void test_ack_relay_reject()
{
	TEST(ack_relay_reject);
	cRelayManager mgr;
	setup_fake_conns();

	mgr.RequestRelay(&g_connA, "Bob", "tok_rej", "e2epm");
	uint32_t acked = mgr.AckRelay(&g_connB, "tok_rej", false);
	ASSERT_EQ(acked, (uint32_t)0); // rejected
	ASSERT_EQ(mgr.GetTotalSessions(), 0u); // session removed
	PASS();
}

void test_ack_unknown_token()
{
	TEST(ack_unknown_token);
	cRelayManager mgr;
	setup_fake_conns();

	uint32_t acked = mgr.AckRelay(&g_connB, "nonexistent", true);
	ASSERT_EQ(acked, (uint32_t)0);
	PASS();
}

void test_relay_data_before_established()
{
	TEST(relay_data_before_established);
	cRelayManager mgr;
	setup_fake_conns();

	uint32_t id = mgr.RequestRelay(&g_connA, "Bob", "tok_notyet", "e2epm");
	// Session exists but not established (no ack yet)
	int result = mgr.RelayData(&g_connA, id, "test data");
	ASSERT_EQ(result, -1); // should fail
	PASS();
}

void test_relay_data_after_established()
{
	TEST(relay_data_after_established);
	cRelayManager mgr;
	setup_fake_conns();

	uint32_t id = mgr.RequestRelay(&g_connA, "Bob", "tok_est", "e2epm");
	mgr.AckRelay(&g_connB, "tok_est", true);

	// Note: RelayData calls target->Send() which is a stub returning 0.
	// The relay manager returns data.size() on success internally,
	// but Send() may "fail" in test. We check that the session was found
	// and the right peer was selected.
	int result = mgr.RelayData(&g_connA, id, "encrypted_data");
	ASSERT_GT(result, 0);
	ASSERT_EQ(mgr.GetTotalBytesRelayed(), (uint64_t)14); // "encrypted_data" = 14 bytes
	PASS();
}

void test_relay_data_wrong_peer()
{
	TEST(relay_data_wrong_peer);
	cRelayManager mgr;
	setup_fake_conns();

	uint32_t id = mgr.RequestRelay(&g_connA, "Bob", "tok_wp", "e2epm");
	mgr.AckRelay(&g_connB, "tok_wp", true);

	int result = mgr.RelayData(&g_connC, id, "spoof data"); // connC is not in session
	ASSERT_EQ(result, -1);
	PASS();
}

void test_close_relay()
{
	TEST(close_relay);
	cRelayManager mgr;
	setup_fake_conns();

	uint32_t id = mgr.RequestRelay(&g_connA, "Bob", "tok_close", "e2epm");
	mgr.AckRelay(&g_connB, "tok_close", true);

	int result = mgr.CloseRelay(id, 0); // normal close
	ASSERT_EQ(result, 0);
	ASSERT_EQ(mgr.GetTotalSessions(), 0u);
	PASS();
}

void test_close_nonexistent()
{
	TEST(close_nonexistent);
	cRelayManager mgr;

	int result = mgr.CloseRelay(99999, 0);
	ASSERT_EQ(result, -1);
	PASS();
}

void test_cleanup_timedout()
{
	TEST(cleanup_timedout);
	cRelayManager mgr;
	setup_fake_conns();

	mgr.RequestRelay(&g_connA, "Bob", "tok_to", "e2epm");
	ASSERT_EQ(mgr.GetTotalSessions(), 1u);

	// Simulate time passing beyond timeout
	time_t now = time(NULL) + 400; // 400 seconds later
	mgr.CleanupTimedOut(now, 300); // timeout = 300 sec
	ASSERT_EQ(mgr.GetTotalSessions(), 0u);
	PASS();
}

void test_cleanup_not_expired()
{
	TEST(cleanup_not_expired);
	cRelayManager mgr;
	setup_fake_conns();

	mgr.RequestRelay(&g_connA, "Bob", "tok_nexp", "e2epm");
	ASSERT_EQ(mgr.GetTotalSessions(), 1u);

	// Not enough time passed
	time_t now = time(NULL) + 100; // only 100 seconds
	mgr.CleanupTimedOut(now, 300);
	ASSERT_EQ(mgr.GetTotalSessions(), 1u); // still alive
	PASS();
}

void test_on_user_disconnect()
{
	TEST(on_user_disconnect);
	cRelayManager mgr;
	setup_fake_conns();

	mgr.RequestRelay(&g_connA, "Bob", "tok_dc", "e2epm");
	mgr.AckRelay(&g_connB, "tok_dc", true);
	ASSERT_EQ(mgr.GetTotalSessions(), 1u);

	mgr.OnUserDisconnect(&g_connA);
	ASSERT_EQ(mgr.GetTotalSessions(), 0u);
	PASS();
}

void test_disconnect_other_peer()
{
	TEST(disconnect_other_peer);
	cRelayManager mgr;
	setup_fake_conns();

	mgr.RequestRelay(&g_connA, "Bob", "tok_dc2", "e2epm");
	mgr.AckRelay(&g_connB, "tok_dc2", true);

	mgr.OnUserDisconnect(&g_connB);
	ASSERT_EQ(mgr.GetTotalSessions(), 0u);
	PASS();
}

void test_disconnect_unrelated_user()
{
	TEST(disconnect_unrelated_user);
	cRelayManager mgr;
	setup_fake_conns();

	mgr.RequestRelay(&g_connA, "Bob", "tok_unrel", "e2epm");
	mgr.AckRelay(&g_connB, "tok_unrel", true);

	mgr.OnUserDisconnect(&g_connC); // Charlie is not in any session
	ASSERT_EQ(mgr.GetTotalSessions(), 1u); // session untouched
	PASS();
}

void test_get_session_count()
{
	TEST(get_session_count);
	cRelayManager mgr;
	setup_fake_conns();

	mgr.RequestRelay(&g_connA, "Bob", "tok_sc1", "e2epm");
	mgr.RequestRelay(&g_connA, "Charlie", "tok_sc2", "e2epm");

	ASSERT_EQ(mgr.GetSessionCount(&g_connA), 2u);
	ASSERT_EQ(mgr.GetSessionCount(&g_connB), 0u);
	ASSERT_EQ(mgr.GetTotalSessions(), 2u);
	PASS();
}

void test_multiple_sessions_independent()
{
	TEST(multiple_sessions_independent);
	cRelayManager mgr;
	setup_fake_conns();

	uint32_t id1 = mgr.RequestRelay(&g_connA, "Bob", "tok_m1", "e2epm");
	uint32_t id2 = mgr.RequestRelay(&g_connA, "Charlie", "tok_m2", "e2epm");
	mgr.AckRelay(&g_connB, "tok_m1", true);
	mgr.AckRelay(&g_connC, "tok_m2", true);

	ASSERT_EQ(mgr.GetTotalSessions(), 2u);

	// Close one
	mgr.CloseRelay(id1, 0);
	ASSERT_EQ(mgr.GetTotalSessions(), 1u);

	// The other is still alive
	int result = mgr.RelayData(&g_connA, id2, "still works");
	ASSERT_GT(result, 0);

	PASS();
}

void test_session_ids_unique()
{
	TEST(session_ids_unique);
	cRelayManager mgr;
	setup_fake_conns();

	uint32_t id1 = mgr.RequestRelay(&g_connA, "Bob", "tok_u1", "e2epm");
	uint32_t id2 = mgr.RequestRelay(&g_connA, "Charlie", "tok_u2", "e2epm");

	ASSERT_TRUE(id1 != id2);
	ASSERT_TRUE(id1 > 0);
	ASSERT_TRUE(id2 > 0);
	PASS();
}

int main()
{
	cout << "=== cRelayManager Unit Tests ===" << endl;

	test_request_relay();
	test_request_duplicate_token_fails();
	test_request_null_fails();
	test_ack_relay();
	test_ack_relay_reject();
	test_ack_unknown_token();
	test_relay_data_before_established();
	test_relay_data_after_established();
	test_relay_data_wrong_peer();
	test_close_relay();
	test_close_nonexistent();
	test_cleanup_timedout();
	test_cleanup_not_expired();
	test_on_user_disconnect();
	test_disconnect_other_peer();
	test_disconnect_unrelated_user();
	test_get_session_count();
	test_multiple_sessions_independent();
	test_session_ids_unique();

	cout << endl << "=== Results: " << g_tests_passed << "/" << g_tests_run << " passed ===" << endl;

	return (g_tests_passed == g_tests_run) ? 0 : 1;
}

#else // WITH_NMDCPB

#include <iostream>

int main()
{
	std::cout << "Relay tests skipped (WITH_NMDCPB not defined)" << std::endl;
	return 0;
}

#endif
