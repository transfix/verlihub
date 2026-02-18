/*
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

#include <gtest/gtest.h>
#include <set>
#include <string>
#include <vector>

#include "../nmdc_protocol.h"
#include "../compat_format.h"

using namespace nVerliHub;
using namespace NMDCProtocol;

// =============================================================================
// GenerateLock Tests
// =============================================================================

TEST(NMDCProtocolTest, GenerateLock_StartsWithExtendedProtocol) {
    std::string lock = GenerateLock();
    EXPECT_EQ(lock.substr(0, 16), "EXTENDEDPROTOCOL");
}

TEST(NMDCProtocolTest, GenerateLock_HasCorrectLength) {
    std::string lock = GenerateLock();
    // "EXTENDEDPROTOCOL" (16) + 20 random chars = 36
    EXPECT_EQ(lock.size(), 36u);
}

TEST(NMDCProtocolTest, GenerateLock_ProducesUniqueValues) {
    std::set<std::string> locks;
    for (int i = 0; i < 100; ++i) {
        locks.insert(GenerateLock());
    }
    // All 100 locks should be unique (extremely high probability)
    EXPECT_EQ(locks.size(), 100u);
}

// =============================================================================
// Escape / UnEscape Tests
// =============================================================================

TEST(NMDCProtocolTest, Escape_NullChar) {
    std::string input(1, '\0');
    std::string escaped = Escape(input);
    EXPECT_EQ(escaped, "/%DCN000%/");
}

TEST(NMDCProtocolTest, Escape_Char5) {
    std::string input(1, '\x05');
    std::string escaped = Escape(input);
    EXPECT_EQ(escaped, "/%DCN005%/");
}

TEST(NMDCProtocolTest, Escape_Dollar) {
    std::string input = "$";
    std::string escaped = Escape(input);
    EXPECT_EQ(escaped, "/%DCN036%/");
}

TEST(NMDCProtocolTest, Escape_Backtick) {
    std::string input = "`";
    std::string escaped = Escape(input);
    EXPECT_EQ(escaped, "/%DCN096%/");
}

TEST(NMDCProtocolTest, Escape_Pipe) {
    std::string input = "|";
    std::string escaped = Escape(input);
    EXPECT_EQ(escaped, "/%DCN124%/");
}

TEST(NMDCProtocolTest, Escape_Tilde) {
    std::string input = "~";
    std::string escaped = Escape(input);
    EXPECT_EQ(escaped, "/%DCN126%/");
}

TEST(NMDCProtocolTest, Escape_NormalCharsPassthrough) {
    std::string input = "Hello World 123";
    EXPECT_EQ(Escape(input), input);
}

TEST(NMDCProtocolTest, Escape_MixedContent) {
    std::string input = "test$value|end";
    std::string escaped = Escape(input);
    EXPECT_EQ(escaped, "test/%DCN036%/value/%DCN124%/end");
}

TEST(NMDCProtocolTest, UnEscape_NullChar) {
    std::string input = "/%DCN000%/";
    std::string result = UnEscape(input);
    ASSERT_EQ(result.size(), 1u);
    EXPECT_EQ(result[0], '\0');
}

TEST(NMDCProtocolTest, UnEscape_Dollar) {
    std::string input = "/%DCN036%/";
    EXPECT_EQ(UnEscape(input), "$");
}

TEST(NMDCProtocolTest, UnEscape_Pipe) {
    std::string input = "/%DCN124%/";
    EXPECT_EQ(UnEscape(input), "|");
}

TEST(NMDCProtocolTest, UnEscape_NormalCharsPassthrough) {
    std::string input = "Hello World 123";
    EXPECT_EQ(UnEscape(input), input);
}

TEST(NMDCProtocolTest, EscapeUnEscape_Roundtrip) {
    // Test that Escape(input) -> UnEscape -> input for all special chars
    for (int c : {0, 5, 36, 96, 124, 126}) {
        std::string original(1, static_cast<char>(c));
        std::string result = UnEscape(Escape(original));
        EXPECT_EQ(result, original) << "Roundtrip failed for char " << c;
    }
}

TEST(NMDCProtocolTest, EscapeUnEscape_ComplexRoundtrip) {
    std::string original = "Hello";
    original += '\0';
    original += "$World|test~end`";
    std::string result = UnEscape(Escape(original));
    EXPECT_EQ(result, original);
}

// =============================================================================
// Lock2Key Tests
// =============================================================================

TEST(NMDCProtocolTest, Lock2Key_EmptyString) {
    EXPECT_EQ(Lock2Key(""), "");
}

TEST(NMDCProtocolTest, Lock2Key_SingleChar) {
    EXPECT_EQ(Lock2Key("x"), "");  // length < 2
}

TEST(NMDCProtocolTest, Lock2Key_ProducesNonEmptyResult) {
    std::string lock = "EXTENDEDPROTOCOL_test_lock_12345";
    std::string key = Lock2Key(lock);
    EXPECT_FALSE(key.empty());
}

TEST(NMDCProtocolTest, Lock2Key_DeterministicOutput) {
    std::string lock = "EXTENDEDPROTOCOL_test_lock_12345";
    std::string key1 = Lock2Key(lock);
    std::string key2 = Lock2Key(lock);
    EXPECT_EQ(key1, key2);
}

TEST(NMDCProtocolTest, Lock2Key_DifferentLocksGiveDifferentKeys) {
    std::string key1 = Lock2Key("EXTENDEDPROTOCOLaaaaaaaaaaaa");
    std::string key2 = Lock2Key("EXTENDEDPROTOCOLbbbbbbbbbbbb");
    EXPECT_NE(key1, key2);
}

TEST(NMDCProtocolTest, Lock2Key_WorksWithGeneratedLock) {
    // Verify Lock2Key handles locks produced by GenerateLock
    std::string lock = GenerateLock();
    std::string key = Lock2Key(lock);
    EXPECT_FALSE(key.empty());
}

// =============================================================================
// Message Construction Tests
// =============================================================================

TEST(NMDCProtocolTest, MakeLock_Format) {
    std::string result = MakeLock("MYLOCK123");
    EXPECT_EQ(result, "$Lock MYLOCK123 Pk=verlihub-py");
}

TEST(NMDCProtocolTest, MakeSupports_ContainsExpectedFeatures) {
    std::string result = MakeSupports();
    EXPECT_NE(result.find("$Supports"), std::string::npos);
    EXPECT_NE(result.find("UserCommand"), std::string::npos);
    EXPECT_NE(result.find("NoGetINFO"), std::string::npos);
    EXPECT_NE(result.find("UserIP2"), std::string::npos);
}

TEST(NMDCProtocolTest, MakeHubName_Format) {
    EXPECT_EQ(MakeHubName("My Hub"), "$HubName My Hub");
}

TEST(NMDCProtocolTest, MakeHello_Format) {
    EXPECT_EQ(MakeHello("JohnDoe"), "$Hello JohnDoe");
}

TEST(NMDCProtocolTest, MakeGetPass_Format) {
    EXPECT_EQ(MakeGetPass(), "$GetPass");
}

TEST(NMDCProtocolTest, MakeBadPass_Format) {
    EXPECT_EQ(MakeBadPass(), "$BadPass");
}

TEST(NMDCProtocolTest, MakeLoggedIn_Format) {
    EXPECT_EQ(MakeLoggedIn(), "$LogedIn");
}

TEST(NMDCProtocolTest, MakeValidateDenide_Format) {
    EXPECT_EQ(MakeValidateDenide("BadNick"), "$ValidateDenide BadNick");
}

TEST(NMDCProtocolTest, MakeValidateDenide_EmptyNick) {
    EXPECT_EQ(MakeValidateDenide(""), "$ValidateDenide ");
}

TEST(NMDCProtocolTest, MakeHubIsFull_Format) {
    EXPECT_EQ(MakeHubIsFull(), "$HubIsFull");
}

TEST(NMDCProtocolTest, MakeQuit_Format) {
    EXPECT_EQ(MakeQuit("LeavingUser"), "$Quit LeavingUser");
}

TEST(NMDCProtocolTest, MakeNickList_MultipleNicks) {
    std::vector<std::string> nicks = {"Alice", "Bob", "Charlie"};
    std::string result = MakeNickList(nicks);
    EXPECT_EQ(result, "$NickList Alice$$Bob$$Charlie$$");
}

TEST(NMDCProtocolTest, MakeNickList_Empty) {
    std::vector<std::string> nicks;
    EXPECT_EQ(MakeNickList(nicks), "$NickList ");
}

TEST(NMDCProtocolTest, MakeNickList_SingleNick) {
    std::vector<std::string> nicks = {"Alice"};
    EXPECT_EQ(MakeNickList(nicks), "$NickList Alice$$");
}

TEST(NMDCProtocolTest, MakeOpList_MultipleNicks) {
    std::vector<std::string> nicks = {"Admin", "Mod"};
    std::string result = MakeOpList(nicks);
    EXPECT_EQ(result, "$OpList Admin$$Mod$$");
}

TEST(NMDCProtocolTest, MakeOpList_Empty) {
    std::vector<std::string> nicks;
    EXPECT_EQ(MakeOpList(nicks), "$OpList ");
}

TEST(NMDCProtocolTest, MakeBotMyINFO_DefaultEmail) {
    std::string result = MakeBotMyINFO("BotNick", "My Bot Description");
    EXPECT_NE(result.find("$MyINFO $ALL BotNick"), std::string::npos);
    EXPECT_NE(result.find("My Bot Description"), std::string::npos);
    EXPECT_NE(result.find("Bot\x01"), std::string::npos);
    EXPECT_NE(result.find("$0$"), std::string::npos);
}

TEST(NMDCProtocolTest, MakeBotMyINFO_WithEmail) {
    std::string result = MakeBotMyINFO("Bot", "Desc", "bot@hub.com");
    EXPECT_NE(result.find("bot@hub.com"), std::string::npos);
}

TEST(NMDCProtocolTest, MakeChat_Format) {
    std::string result = MakeChat("UserA", "Hello everyone!");
    EXPECT_EQ(result, "<UserA> Hello everyone!");
}

TEST(NMDCProtocolTest, MakeChat_EmptyMessage) {
    EXPECT_EQ(MakeChat("User", ""), "<User> ");
}

TEST(NMDCProtocolTest, MakePrivateMessage_Format) {
    std::string result = MakePrivateMessage("Alice", "Bob", "Hi Bob!");
    EXPECT_EQ(result, "$To: Bob From: Alice $<Alice> Hi Bob!");
}

TEST(NMDCProtocolTest, MakeHubTopic_Format) {
    EXPECT_EQ(MakeHubTopic("Welcome!"), "$HubTopic Welcome!");
}

TEST(NMDCProtocolTest, MakeUserIP_Format) {
    EXPECT_EQ(MakeUserIP("JohnDoe", "192.168.1.100"),
              "$UserIP JohnDoe 192.168.1.100$$");
}

// =============================================================================
// ParseMyINFO Tests
// =============================================================================

TEST(NMDCProtocolTest, ParseMyINFO_ValidFullMessage) {
    std::string msg = "$MyINFO $ALL TestUser Test Description<TestClient V:1.0,M:A,H:1/0/0,S:5>$ $DSL\x01$test@email.com$1073741824$";
    MyINFOData data = ParseMyINFO(msg);

    EXPECT_TRUE(data.valid);
    EXPECT_EQ(data.nick, "TestUser");
    EXPECT_EQ(data.description, "Test Description");
    EXPECT_EQ(data.tag, "<TestClient V:1.0,M:A,H:1/0/0,S:5>");
    EXPECT_EQ(data.email, "test@email.com");
    EXPECT_EQ(data.share_size, 1073741824u);
}

TEST(NMDCProtocolTest, ParseMyINFO_NoTag) {
    std::string msg = "$MyINFO $ALL SimpleUser Just a simple description$ $LAN(T3)\x01$$0$";
    MyINFOData data = ParseMyINFO(msg);

    EXPECT_TRUE(data.valid);
    EXPECT_EQ(data.nick, "SimpleUser");
    EXPECT_EQ(data.description, "Just a simple description");
    EXPECT_TRUE(data.tag.empty());
}

TEST(NMDCProtocolTest, ParseMyINFO_ZeroShare) {
    std::string msg = "$MyINFO $ALL NewUser Test<Tag>$ $DSL\x01$$0$";
    MyINFOData data = ParseMyINFO(msg);

    EXPECT_TRUE(data.valid);
    EXPECT_EQ(data.share_size, 0u);
}

TEST(NMDCProtocolTest, ParseMyINFO_LargeShare) {
    // 10 TB = 10995116277760 bytes
    std::string msg = "$MyINFO $ALL BigSharer Files<DC V:1.0>$ $100\x01$$10995116277760$";
    MyINFOData data = ParseMyINFO(msg);

    EXPECT_TRUE(data.valid);
    EXPECT_EQ(data.share_size, 10995116277760u);
}

TEST(NMDCProtocolTest, ParseMyINFO_InvalidPrefix) {
    MyINFOData data = ParseMyINFO("$Hello TestUser");
    EXPECT_FALSE(data.valid);
}

TEST(NMDCProtocolTest, ParseMyINFO_EmptyString) {
    MyINFOData data = ParseMyINFO("");
    EXPECT_FALSE(data.valid);
}

TEST(NMDCProtocolTest, ParseMyINFO_MissingSeparator) {
    // Missing "$ $" separator
    MyINFOData data = ParseMyINFO("$MyINFO $ALL User DescOnly");
    EXPECT_FALSE(data.valid);
}

// =============================================================================
// ParsePrivateMessage Tests
// =============================================================================

TEST(NMDCProtocolTest, ParsePrivateMessage_Valid) {
    std::string msg = "$To: Bob From: Alice $<Alice> Hello Bob!";
    PrivateMessageData data = ParsePrivateMessage(msg);

    EXPECT_TRUE(data.valid);
    EXPECT_EQ(data.to, "Bob");
    EXPECT_EQ(data.from, "Alice");
    EXPECT_EQ(data.message, "Hello Bob!");
}

TEST(NMDCProtocolTest, ParsePrivateMessage_EmptyMessage) {
    // With empty message (after "> ")
    std::string msg = "$To: Bob From: Alice $<Alice> ";
    PrivateMessageData data = ParsePrivateMessage(msg);

    EXPECT_TRUE(data.valid);
    EXPECT_EQ(data.to, "Bob");
    EXPECT_EQ(data.from, "Alice");
    EXPECT_TRUE(data.message.empty());
}

TEST(NMDCProtocolTest, ParsePrivateMessage_InvalidPrefix) {
    PrivateMessageData data = ParsePrivateMessage("$Hello Bob");
    EXPECT_FALSE(data.valid);
}

TEST(NMDCProtocolTest, ParsePrivateMessage_MissingFrom) {
    PrivateMessageData data = ParsePrivateMessage("$To: Bob");
    EXPECT_FALSE(data.valid);
}

TEST(NMDCProtocolTest, ParsePrivateMessage_MissingSeparator) {
    PrivateMessageData data = ParsePrivateMessage("$To: Bob From: Alice");
    EXPECT_FALSE(data.valid);
}

// =============================================================================
// ParseChat Tests
// =============================================================================

TEST(NMDCProtocolTest, ParseChat_Valid) {
    ChatMessageData data = ParseChat("<JohnDoe> Hello world!");
    EXPECT_TRUE(data.valid);
    EXPECT_EQ(data.nick, "JohnDoe");
    EXPECT_EQ(data.message, "Hello world!");
}

TEST(NMDCProtocolTest, ParseChat_MultiWordMessage) {
    ChatMessageData data = ParseChat("<User> This is a longer message with > symbols");
    EXPECT_TRUE(data.valid);
    EXPECT_EQ(data.nick, "User");
    EXPECT_EQ(data.message, "This is a longer message with > symbols");
}

TEST(NMDCProtocolTest, ParseChat_EmptyMessage) {
    ChatMessageData data = ParseChat("<Nick> ");
    EXPECT_TRUE(data.valid);
    EXPECT_EQ(data.nick, "Nick");
    EXPECT_TRUE(data.message.empty());
}

TEST(NMDCProtocolTest, ParseChat_NotChat) {
    ChatMessageData data = ParseChat("$Hello User");
    EXPECT_FALSE(data.valid);
}

TEST(NMDCProtocolTest, ParseChat_EmptyString) {
    ChatMessageData data = ParseChat("");
    EXPECT_FALSE(data.valid);
}

TEST(NMDCProtocolTest, ParseChat_MissingClosingBracket) {
    ChatMessageData data = ParseChat("<UserNoClose some message");
    EXPECT_FALSE(data.valid);
}

// =============================================================================
// IsCommand Tests
// =============================================================================

TEST(NMDCProtocolTest, IsCommand_ExactMatch) {
    EXPECT_TRUE(IsCommand("$Lock", "$Lock"));
}

TEST(NMDCProtocolTest, IsCommand_WithParam) {
    EXPECT_TRUE(IsCommand("$Lock MYLOCK123", "$Lock"));
}

TEST(NMDCProtocolTest, IsCommand_WithPipe) {
    EXPECT_TRUE(IsCommand("$GetPass|", "$GetPass"));
}

TEST(NMDCProtocolTest, IsCommand_NoMatch) {
    EXPECT_FALSE(IsCommand("$Hello User", "$Lock"));
}

TEST(NMDCProtocolTest, IsCommand_PartialMatch) {
    // "$LockExtra" should NOT match "$Lock" because next char is not space/pipe
    EXPECT_FALSE(IsCommand("$LockExtra", "$Lock"));
}

TEST(NMDCProtocolTest, IsCommand_ShorterMsg) {
    EXPECT_FALSE(IsCommand("$Lo", "$Lock"));
}

TEST(NMDCProtocolTest, IsCommand_EmptyMsg) {
    EXPECT_FALSE(IsCommand("", "$Lock"));
}

TEST(NMDCProtocolTest, IsCommand_ChatMessage) {
    EXPECT_FALSE(IsCommand("<User> Hello", "$Lock"));
}

TEST(NMDCProtocolTest, IsCommand_VariousCommands) {
    EXPECT_TRUE(IsCommand("$ValidateNick TestUser", "$ValidateNick"));
    EXPECT_TRUE(IsCommand("$MyINFO $ALL User stuff", "$MyINFO"));
    EXPECT_TRUE(IsCommand("$To: Bob From: Alice $<Alice> hi", "$To:"));
    EXPECT_TRUE(IsCommand("$Search Hub:TTH:HASH", "$Search"));
    EXPECT_TRUE(IsCommand("$Quit", "$Quit"));
}

// =============================================================================
// GetCommandParam Tests
// =============================================================================

TEST(NMDCProtocolTest, GetCommandParam_WithParam) {
    std::string param = GetCommandParam("$Lock MYLOCK123 Pk=test", "$Lock");
    EXPECT_EQ(param, "MYLOCK123 Pk=test");
}

TEST(NMDCProtocolTest, GetCommandParam_NoParam) {
    std::string param = GetCommandParam("$GetPass", "$GetPass");
    EXPECT_TRUE(param.empty());
}

TEST(NMDCProtocolTest, GetCommandParam_WrongCommand) {
    std::string param = GetCommandParam("$Hello User", "$Lock");
    EXPECT_TRUE(param.empty());
}

TEST(NMDCProtocolTest, GetCommandParam_ValidateNick) {
    std::string param = GetCommandParam("$ValidateNick TestUser", "$ValidateNick");
    EXPECT_EQ(param, "TestUser");
}

TEST(NMDCProtocolTest, GetCommandParam_MyPass) {
    std::string param = GetCommandParam("$MyPass secret123", "$MyPass");
    EXPECT_EQ(param, "secret123");
}

TEST(NMDCProtocolTest, GetCommandParam_EmptyParamAfterSpace) {
    // "$Lock " has a space but nothing after
    std::string param = GetCommandParam("$Lock ", "$Lock");
    EXPECT_TRUE(param.empty());
}

// =============================================================================
// MakeChat / ParseChat Round-trip
// =============================================================================

TEST(NMDCProtocolTest, ChatRoundtrip) {
    std::string original_nick = "TestUser";
    std::string original_msg = "Hello, this is a test!";

    std::string chat = MakeChat(original_nick, original_msg);
    ChatMessageData parsed = ParseChat(chat);

    EXPECT_TRUE(parsed.valid);
    EXPECT_EQ(parsed.nick, original_nick);
    EXPECT_EQ(parsed.message, original_msg);
}

// =============================================================================
// MakePrivateMessage / ParsePrivateMessage Round-trip
// =============================================================================

TEST(NMDCProtocolTest, PrivateMessageRoundtrip) {
    std::string from = "Alice";
    std::string to = "Bob";
    std::string msg_text = "Hey Bob, how are you?";

    std::string pm = MakePrivateMessage(from, to, msg_text);
    PrivateMessageData parsed = ParsePrivateMessage(pm);

    EXPECT_TRUE(parsed.valid);
    EXPECT_EQ(parsed.from, from);
    EXPECT_EQ(parsed.to, to);
    EXPECT_EQ(parsed.message, msg_text);
}

// ============================================================
// vh::fmt() compatibility shim tests
// ============================================================

TEST(CompatFormat, SingleStringPlaceholder) {
    EXPECT_EQ(vh::fmt("Hello {}!", "World"), "Hello World!");
}

TEST(CompatFormat, MultipleStringPlaceholders) {
    EXPECT_EQ(vh::fmt("{} and {}", "Alice", "Bob"), "Alice and Bob");
}

TEST(CompatFormat, IntegerPlaceholder) {
    EXPECT_EQ(vh::fmt("port={}", 411), "port=411");
}

TEST(CompatFormat, MixedTypes) {
    EXPECT_EQ(vh::fmt("{}:{}", "hub.example.com", 411), "hub.example.com:411");
}

TEST(CompatFormat, NoPlaceholders) {
    EXPECT_EQ(vh::fmt("no placeholders here"), "no placeholders here");
}

TEST(CompatFormat, ThreePlaceholders) {
    EXPECT_EQ(vh::fmt("{} + {} = {}", 1, 2, 3), "1 + 2 = 3");
}

TEST(CompatFormat, EmptyString) {
    EXPECT_EQ(vh::fmt(""), "");
}

TEST(CompatFormat, ConsecutivePlaceholders) {
    EXPECT_EQ(vh::fmt("{}{}{}", "a", "b", "c"), "abc");
}

TEST(CompatFormat, PlaceholderAtStart) {
    EXPECT_EQ(vh::fmt("{} is here", "Value"), "Value is here");
}

TEST(CompatFormat, PlaceholderAtEnd) {
    EXPECT_EQ(vh::fmt("value is {}", 42), "value is 42");
}

TEST(CompatFormat, BooleanValue) {
    // boolalpha behavior: std::format uses "true"/"false",
    // ostringstream also uses "true"/"false" with boolalpha
    std::string result = vh::fmt("flag={}", true);
    // Accept either "1" or "true" depending on implementation
    EXPECT_TRUE(result == "flag=true" || result == "flag=1");
}

TEST(CompatFormat, FloatingPoint) {
    std::string result = vh::fmt("pi={}", 3.14);
    EXPECT_TRUE(result.find("pi=3.14") == 0);
}

TEST(CompatFormat, CStringArg) {
    const char* name = "verlihub";
    EXPECT_EQ(vh::fmt("hub={}", name), "hub=verlihub");
}
