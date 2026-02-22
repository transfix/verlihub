/*
	NMDCpb C++ Unit Tests

	Tests for:
	- Base64url encode/decode (cPbTranslate)
	- Protobuf PbEnvelope serialization/deserialization
	- PbToLegacy translation (chat, PM, action)
	- LegacyToPb translation (chat, PM, action)
	- Round-trip integrity

	Build: requires protobuf and WITH_NMDCPB defined.
	Run: ctest --test-dir build or ./build/test_nmdcpb
*/

#ifdef WITH_NMDCPB

#include <iostream>
#include <string>
#include <cassert>
#include <cstring>
#include "cpbtranslate.h"
#include "nmdcpb.pb.h"

using namespace nVerliHub::nProtocol;
using namespace std;

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
		if (!(cond)) { \
			FAIL(#cond " is false"); \
			return; \
		} \
	} while(0)

#define ASSERT_EQ(a, b) \
	do { \
		if ((a) != (b)) { \
			FAIL(#a " != " #b); \
			cerr << "    expected: [" << (b) << "]" << endl; \
			cerr << "    actual:   [" << (a) << "]" << endl; \
			return; \
		} \
	} while(0)

// ============================================================================
// Base64url tests
// ============================================================================

void test_base64url_empty()
{
	TEST(base64url_empty);

	string encoded = cPbTranslate::Base64UrlEncode("");
	ASSERT_EQ(encoded, "");

	string decoded;
	ASSERT_TRUE(cPbTranslate::Base64UrlDecode("", decoded));
	ASSERT_EQ(decoded, "");

	PASS();
}

void test_base64url_simple()
{
	TEST(base64url_simple);

	// "Hello" in base64url = "SGVsbG8"
	string input = "Hello";
	string encoded = cPbTranslate::Base64UrlEncode(input);
	ASSERT_EQ(encoded, "SGVsbG8");

	string decoded;
	ASSERT_TRUE(cPbTranslate::Base64UrlDecode(encoded, decoded));
	ASSERT_EQ(decoded, input);

	PASS();
}

void test_base64url_binary()
{
	TEST(base64url_binary);

	// Binary data with bytes that differ between base64 and base64url
	string input;
	input += '\x3e'; // would be '+' in base64, '-' in base64url
	input += '\x3f'; // would be '/' in base64, '_' in base64url
	input += '\x00';

	string encoded = cPbTranslate::Base64UrlEncode(input);

	// Should NOT contain + or /
	ASSERT_TRUE(encoded.find('+') == string::npos);
	ASSERT_TRUE(encoded.find('/') == string::npos);

	string decoded;
	ASSERT_TRUE(cPbTranslate::Base64UrlDecode(encoded, decoded));
	ASSERT_EQ(decoded, input);

	PASS();
}

void test_base64url_roundtrip_various_lengths()
{
	TEST(base64url_roundtrip_various_lengths);

	// Test lengths 0-32 to cover all padding cases
	for (size_t len = 0; len <= 32; len++) {
		string input(len, '\0');

		for (size_t i = 0; i < len; i++)
			input[i] = static_cast<char>(i * 7 + 13); // pseudo-random

		string encoded = cPbTranslate::Base64UrlEncode(input);
		string decoded;
		ASSERT_TRUE(cPbTranslate::Base64UrlDecode(encoded, decoded));
		ASSERT_EQ(decoded, input);
	}

	PASS();
}

// ============================================================================
// Protobuf serialization tests
// ============================================================================

void test_pb_envelope_chat_serialize()
{
	TEST(pb_envelope_chat_serialize);

	nmdcpb::PbEnvelope env;
	env.set_route(nmdcpb::PbEnvelope::BROADCAST);
	env.set_from_nick("TestUser");
	env.set_timestamp(1234567890000);

	auto *chat = env.mutable_chat();
	chat->set_text("Hello, world!");
	chat->set_is_action(false);

	string raw;
	ASSERT_TRUE(env.SerializeToString(&raw));
	ASSERT_TRUE(raw.size() > 0);

	// Deserialize back
	nmdcpb::PbEnvelope env2;
	ASSERT_TRUE(env2.ParseFromString(raw));
	ASSERT_TRUE(env2.has_chat());
	ASSERT_EQ(env2.from_nick(), "TestUser");
	ASSERT_EQ(env2.chat().text(), "Hello, world!");
	ASSERT_EQ(env2.chat().is_action(), false);
	ASSERT_EQ(env2.route(), nmdcpb::PbEnvelope::BROADCAST);

	PASS();
}

void test_pb_envelope_pm_serialize()
{
	TEST(pb_envelope_pm_serialize);

	nmdcpb::PbEnvelope env;
	env.set_route(nmdcpb::PbEnvelope::DIRECT);
	env.set_from_nick("Sender");
	env.set_to_nick("Receiver");

	auto *chat = env.mutable_chat();
	chat->set_text("Private message");
	chat->set_is_pm(true);
	chat->set_target_nick("Receiver");

	string raw;
	ASSERT_TRUE(env.SerializeToString(&raw));

	nmdcpb::PbEnvelope env2;
	ASSERT_TRUE(env2.ParseFromString(raw));
	ASSERT_TRUE(env2.has_chat());
	ASSERT_EQ(env2.chat().is_pm(), true);
	ASSERT_EQ(env2.chat().target_nick(), "Receiver");
	ASSERT_EQ(env2.to_nick(), "Receiver");

	PASS();
}

// ============================================================================
// PbToLegacy translation tests
// ============================================================================

void test_pb_to_legacy_public_chat()
{
	TEST(pb_to_legacy_public_chat);

	// Build a PbEnvelope with public chat
	nmdcpb::PbEnvelope env;
	env.set_route(nmdcpb::PbEnvelope::BROADCAST);
	env.set_from_nick("Alice");

	auto *chat = env.mutable_chat();
	chat->set_text("Hello everyone!");

	string raw;
	ASSERT_TRUE(env.SerializeToString(&raw));

	string base64 = cPbTranslate::Base64UrlEncode(raw);
	string legacy;

	ASSERT_TRUE(cPbTranslate::PbToLegacy(base64, "Alice", legacy));
	ASSERT_EQ(legacy, "<Alice> Hello everyone!");

	PASS();
}

void test_pb_to_legacy_action()
{
	TEST(pb_to_legacy_action);

	nmdcpb::PbEnvelope env;
	env.set_route(nmdcpb::PbEnvelope::BROADCAST);
	env.set_from_nick("Bob");

	auto *chat = env.mutable_chat();
	chat->set_text("waves");
	chat->set_is_action(true);

	string raw;
	ASSERT_TRUE(env.SerializeToString(&raw));

	string base64 = cPbTranslate::Base64UrlEncode(raw);
	string legacy;

	ASSERT_TRUE(cPbTranslate::PbToLegacy(base64, "Bob", legacy));
	ASSERT_EQ(legacy, "* Bob waves");

	PASS();
}

void test_pb_to_legacy_pm()
{
	TEST(pb_to_legacy_pm);

	nmdcpb::PbEnvelope env;
	env.set_route(nmdcpb::PbEnvelope::DIRECT);
	env.set_from_nick("Alice");
	env.set_to_nick("Bob");

	auto *chat = env.mutable_chat();
	chat->set_text("Hey Bob!");
	chat->set_is_pm(true);
	chat->set_target_nick("Bob");

	string raw;
	ASSERT_TRUE(env.SerializeToString(&raw));

	string base64 = cPbTranslate::Base64UrlEncode(raw);
	string legacy;

	ASSERT_TRUE(cPbTranslate::PbToLegacy(base64, "Alice", legacy));
	ASSERT_EQ(legacy, "$To: Bob From: Alice $<Alice> Hey Bob!");

	PASS();
}

void test_pb_to_legacy_non_chat_returns_false()
{
	TEST(pb_to_legacy_non_chat_returns_false);

	// UserInfo message — no legacy equivalent
	nmdcpb::PbEnvelope env;
	env.set_route(nmdcpb::PbEnvelope::BROADCAST);
	env.set_from_nick("Alice");

	auto *info = env.mutable_user_info();
	info->set_nick("Alice");
	info->set_share_size(1000000);

	string raw;
	ASSERT_TRUE(env.SerializeToString(&raw));

	string base64 = cPbTranslate::Base64UrlEncode(raw);
	string legacy;

	ASSERT_TRUE(!cPbTranslate::PbToLegacy(base64, "Alice", legacy));

	PASS();
}

void test_pb_to_legacy_invalid_base64()
{
	TEST(pb_to_legacy_invalid_base64);

	string legacy;
	// Invalid protobuf data (will fail to parse)
	ASSERT_TRUE(!cPbTranslate::PbToLegacy("####invaliddata####", "Nick", legacy));

	PASS();
}

// ============================================================================
// LegacyToPb translation tests
// ============================================================================

void test_legacy_to_pb_public_chat()
{
	TEST(legacy_to_pb_public_chat);

	string pb_out;
	ASSERT_TRUE(cPbTranslate::LegacyToPb("Alice", "Hello world", false, false, "", pb_out));
	ASSERT_TRUE(!pb_out.empty());

	// Decode and verify
	string raw;
	ASSERT_TRUE(cPbTranslate::Base64UrlDecode(pb_out, raw));

	nmdcpb::PbEnvelope env;
	ASSERT_TRUE(env.ParseFromString(raw));
	ASSERT_TRUE(env.has_chat());
	ASSERT_EQ(env.from_nick(), "Alice");
	ASSERT_EQ(env.route(), nmdcpb::PbEnvelope::BROADCAST);
	ASSERT_EQ(env.chat().text(), "Hello world");
	ASSERT_EQ(env.chat().is_action(), false);
	ASSERT_EQ(env.chat().is_pm(), false);

	PASS();
}

void test_legacy_to_pb_pm()
{
	TEST(legacy_to_pb_pm);

	string pb_out;
	ASSERT_TRUE(cPbTranslate::LegacyToPb("Alice", "Hi Bob!", false, true, "Bob", pb_out));

	string raw;
	ASSERT_TRUE(cPbTranslate::Base64UrlDecode(pb_out, raw));

	nmdcpb::PbEnvelope env;
	ASSERT_TRUE(env.ParseFromString(raw));
	ASSERT_TRUE(env.has_chat());
	ASSERT_EQ(env.route(), nmdcpb::PbEnvelope::DIRECT);
	ASSERT_EQ(env.to_nick(), "Bob");
	ASSERT_EQ(env.chat().is_pm(), true);
	ASSERT_EQ(env.chat().target_nick(), "Bob");
	ASSERT_EQ(env.chat().text(), "Hi Bob!");

	PASS();
}

void test_legacy_to_pb_action()
{
	TEST(legacy_to_pb_action);

	string pb_out;
	ASSERT_TRUE(cPbTranslate::LegacyToPb("Bob", "waves hello", true, false, "", pb_out));

	string raw;
	ASSERT_TRUE(cPbTranslate::Base64UrlDecode(pb_out, raw));

	nmdcpb::PbEnvelope env;
	ASSERT_TRUE(env.ParseFromString(raw));
	ASSERT_TRUE(env.has_chat());
	ASSERT_EQ(env.chat().is_action(), true);
	ASSERT_EQ(env.chat().text(), "waves hello");

	PASS();
}

// ============================================================================
// Round-trip tests
// ============================================================================

void test_roundtrip_public_chat()
{
	TEST(roundtrip_public_chat);

	// Legacy → PB → Legacy
	string pb_out;
	ASSERT_TRUE(cPbTranslate::LegacyToPb("Alice", "Test message", false, false, "", pb_out));

	string legacy;
	ASSERT_TRUE(cPbTranslate::PbToLegacy(pb_out, "Alice", legacy));
	ASSERT_EQ(legacy, "<Alice> Test message");

	PASS();
}

void test_roundtrip_pm()
{
	TEST(roundtrip_pm);

	string pb_out;
	ASSERT_TRUE(cPbTranslate::LegacyToPb("Alice", "Private", false, true, "Bob", pb_out));

	string legacy;
	ASSERT_TRUE(cPbTranslate::PbToLegacy(pb_out, "Alice", legacy));
	ASSERT_EQ(legacy, "$To: Bob From: Alice $<Alice> Private");

	PASS();
}

void test_roundtrip_action()
{
	TEST(roundtrip_action);

	string pb_out;
	ASSERT_TRUE(cPbTranslate::LegacyToPb("Bob", "dances", true, false, "", pb_out));

	string legacy;
	ASSERT_TRUE(cPbTranslate::PbToLegacy(pb_out, "Bob", legacy));
	ASSERT_EQ(legacy, "* Bob dances");

	PASS();
}

void test_unicode_chat()
{
	TEST(unicode_chat);

	// Test with Unicode text
	string pb_out;
	ASSERT_TRUE(cPbTranslate::LegacyToPb("Алиса", "Привет мир! 🌍", false, false, "", pb_out));

	string legacy;
	ASSERT_TRUE(cPbTranslate::PbToLegacy(pb_out, "Алиса", legacy));
	ASSERT_EQ(legacy, "<Алиса> Привет мир! 🌍");

	PASS();
}

void test_empty_text()
{
	TEST(empty_text);

	string pb_out;
	ASSERT_TRUE(cPbTranslate::LegacyToPb("Nick", "", false, false, "", pb_out));

	string legacy;
	ASSERT_TRUE(cPbTranslate::PbToLegacy(pb_out, "Nick", legacy));
	ASSERT_EQ(legacy, "<Nick> ");

	PASS();
}

void test_special_chars_in_text()
{
	TEST(special_chars_in_text);

	// Test with NMDC-special characters: | $ < >
	string text = "Hello | world $ <tag> end";
	string pb_out;
	ASSERT_TRUE(cPbTranslate::LegacyToPb("Alice", text, false, false, "", pb_out));

	string legacy;
	ASSERT_TRUE(cPbTranslate::PbToLegacy(pb_out, "Alice", legacy));
	ASSERT_EQ(legacy, "<Alice> Hello | world $ <tag> end");

	PASS();
}

// ============================================================================
// Main
// ============================================================================

int main()
{
	GOOGLE_PROTOBUF_VERIFY_VERSION;

	cout << "NMDCpb C++ Unit Tests" << endl;
	cout << "=====================" << endl;

	cout << endl << "Base64url:" << endl;
	test_base64url_empty();
	test_base64url_simple();
	test_base64url_binary();
	test_base64url_roundtrip_various_lengths();

	cout << endl << "Protobuf serialization:" << endl;
	test_pb_envelope_chat_serialize();
	test_pb_envelope_pm_serialize();

	cout << endl << "PbToLegacy translation:" << endl;
	test_pb_to_legacy_public_chat();
	test_pb_to_legacy_action();
	test_pb_to_legacy_pm();
	test_pb_to_legacy_non_chat_returns_false();
	test_pb_to_legacy_invalid_base64();

	cout << endl << "LegacyToPb translation:" << endl;
	test_legacy_to_pb_public_chat();
	test_legacy_to_pb_pm();
	test_legacy_to_pb_action();

	cout << endl << "Round-trip:" << endl;
	test_roundtrip_public_chat();
	test_roundtrip_pm();
	test_roundtrip_action();
	test_unicode_chat();
	test_empty_text();
	test_special_chars_in_text();

	cout << endl << "=====================" << endl;
	cout << g_tests_passed << "/" << g_tests_run << " tests passed" << endl;

	google::protobuf::ShutdownProtobufLibrary();

	return (g_tests_passed == g_tests_run) ? 0 : 1;
}

#else // !WITH_NMDCPB

#include <iostream>

int main()
{
	std::cout << "NMDCpb tests skipped (built without WITH_NMDCPB)" << std::endl;
	return 0;
}

#endif // WITH_NMDCPB
