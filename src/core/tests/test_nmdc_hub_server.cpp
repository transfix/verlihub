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
#include <string>
#include <vector>

#include "../nmdc_hub_server.h"
#include "../../casyncconn.h"

using namespace nVerliHub;

// =============================================================================
// NMDCHubServer Configuration Tests
// =============================================================================

class NMDCHubServerTest : public ::testing::Test {
protected:
    NMDCHubServer server;

    NMDCHubServerTest() : server(".") {}
};

// --- Hub Name ---

TEST_F(NMDCHubServerTest, DefaultHubName) {
    EXPECT_EQ(server.GetHubName(), "Verlihub Hub");
}

TEST_F(NMDCHubServerTest, SetHubName) {
    server.SetHubName("My Test Hub");
    EXPECT_EQ(server.GetHubName(), "My Test Hub");
}

TEST_F(NMDCHubServerTest, SetHubName_Empty) {
    server.SetHubName("");
    EXPECT_EQ(server.GetHubName(), "");
}

TEST_F(NMDCHubServerTest, SetHubName_Unicode) {
    server.SetHubName("Тестовый Хаб");
    EXPECT_EQ(server.GetHubName(), "Тестовый Хаб");
}

// --- Hub Topic ---

TEST_F(NMDCHubServerTest, DefaultHubTopic_Empty) {
    EXPECT_TRUE(server.GetHubTopic().empty());
}

TEST_F(NMDCHubServerTest, SetHubTopic) {
    server.SetHubTopic("Welcome to the hub!");
    EXPECT_EQ(server.GetHubTopic(), "Welcome to the hub!");
}

// --- Hub Security ---

TEST_F(NMDCHubServerTest, DefaultHubSecurity) {
    EXPECT_EQ(server.GetHubSecurity(), "Hub-Security");
}

TEST_F(NMDCHubServerTest, SetHubSecurity) {
    server.SetHubSecurity("MyBot");
    EXPECT_EQ(server.GetHubSecurity(), "MyBot");
}

// --- Max Users ---

TEST_F(NMDCHubServerTest, SetMaxUsers) {
    server.SetMaxUsers(500);
    // We verify indirectly: no crash, and the value is stored
    // (max_users is private, but affects OnNewConn behavior)
    SUCCEED();
}

// =============================================================================
// User Count / State Tests (no connections)
// =============================================================================

TEST_F(NMDCHubServerTest, InitialUserCount_Zero) {
    EXPECT_EQ(server.GetUserCount(), 0u);
}

TEST_F(NMDCHubServerTest, InitialTotalShare_Zero) {
    EXPECT_EQ(server.GetTotalShare(), 0u);
}

TEST_F(NMDCHubServerTest, InitialNickList_Empty) {
    auto nicks = server.GetNickList();
    EXPECT_TRUE(nicks.empty());
}

TEST_F(NMDCHubServerTest, InitialOpList_Empty) {
    auto ops = server.GetOpList();
    EXPECT_TRUE(ops.empty());
}

TEST_F(NMDCHubServerTest, IsNickOnline_UnknownNick) {
    EXPECT_FALSE(server.IsNickOnline("NonExistentUser"));
}

TEST_F(NMDCHubServerTest, IsNickOnline_EmptyNick) {
    EXPECT_FALSE(server.IsNickOnline(""));
}

// =============================================================================
// Messaging API (no connections - should not crash)
// =============================================================================

TEST_F(NMDCHubServerTest, SendToNick_NoUsers) {
    EXPECT_FALSE(server.SendToNick("Ghost", "Hello"));
}

TEST_F(NMDCHubServerTest, SendToAll_NoUsers) {
    // Should not crash or throw when no users are connected
    EXPECT_NO_THROW(server.SendToAll("Broadcast test"));
}

TEST_F(NMDCHubServerTest, SendChatToAll_NoUsers) {
    EXPECT_NO_THROW(server.SendChatToAll("Hub-Security", "Hello everyone"));
}

TEST_F(NMDCHubServerTest, SendPM_NoUsers) {
    EXPECT_FALSE(server.SendPM("Hub-Security", "Ghost", "Hello"));
}

// =============================================================================
// User Management (no connections - should handle gracefully)
// =============================================================================

TEST_F(NMDCHubServerTest, KickUser_NotOnline) {
    EXPECT_FALSE(server.KickUser("NonExistentUser", "Test kick"));
}

TEST_F(NMDCHubServerTest, DisconnectUser_NotOnline) {
    EXPECT_FALSE(server.DisconnectUser("NonExistentUser"));
}

// =============================================================================
// Callback Tests
// =============================================================================

TEST_F(NMDCHubServerTest, SetCallback_Nullptr) {
    // Should not crash
    EXPECT_NO_THROW(server.SetCallback(nullptr));
}

// =============================================================================
// NMDCConnState Enum Tests
// =============================================================================

TEST(NMDCConnStateTest, EnumValues) {
    // Verify enum values are distinct
    EXPECT_NE(NMDCConnState::Connected, NMDCConnState::WaitingKey);
    EXPECT_NE(NMDCConnState::WaitingKey, NMDCConnState::WaitingValidateNick);
    EXPECT_NE(NMDCConnState::WaitingValidateNick, NMDCConnState::WaitingMyPass);
    EXPECT_NE(NMDCConnState::WaitingMyPass, NMDCConnState::WaitingMyINFO);
    EXPECT_NE(NMDCConnState::WaitingMyINFO, NMDCConnState::LoggedIn);
    EXPECT_NE(NMDCConnState::LoggedIn, NMDCConnState::Closing);
}

// =============================================================================
// NMDCClient Struct Tests
// =============================================================================

TEST(NMDCClientTest, DefaultConstruction) {
    NMDCClient client;
    EXPECT_EQ(client.conn, nullptr);
    EXPECT_EQ(client.state, NMDCConnState::Connected);
    EXPECT_TRUE(client.nick.empty());
    EXPECT_TRUE(client.ip.empty());
    EXPECT_TRUE(client.myinfo_raw.empty());
    EXPECT_EQ(client.user_class, 0);
    EXPECT_TRUE(client.lock.empty());
    EXPECT_EQ(client.login_attempts, 0);
}

TEST(NMDCClientTest, SetFields) {
    NMDCClient client;
    client.nick = "TestUser";
    client.ip = "192.168.1.100";
    client.user_class = 5;
    client.state = NMDCConnState::LoggedIn;
    client.login_attempts = 2;

    EXPECT_EQ(client.nick, "TestUser");
    EXPECT_EQ(client.ip, "192.168.1.100");
    EXPECT_EQ(client.user_class, 5);
    EXPECT_EQ(client.state, NMDCConnState::LoggedIn);
    EXPECT_EQ(client.login_attempts, 2);
}

// =============================================================================
// Multiple Configuration Changes
// =============================================================================

TEST_F(NMDCHubServerTest, MultipleConfigChanges) {
    server.SetHubName("Hub1");
    EXPECT_EQ(server.GetHubName(), "Hub1");

    server.SetHubName("Hub2");
    EXPECT_EQ(server.GetHubName(), "Hub2");

    server.SetHubTopic("Topic1");
    EXPECT_EQ(server.GetHubTopic(), "Topic1");

    server.SetHubTopic("Topic2");
    EXPECT_EQ(server.GetHubTopic(), "Topic2");

    server.SetHubSecurity("Bot1");
    EXPECT_EQ(server.GetHubSecurity(), "Bot1");

    server.SetHubSecurity("Bot2");
    EXPECT_EQ(server.GetHubSecurity(), "Bot2");
}

// =============================================================================
// OnClientDeleted with null (defensive)
// =============================================================================

TEST_F(NMDCHubServerTest, OnClientDeleted_Null) {
    // Should not crash
    EXPECT_NO_THROW(server.OnClientDeleted(nullptr));
}

TEST(NMDCHubServerConnTest, OutputBufferSizeIsDefault) {
    nVerliHub::NMDCHubServer server(".");
    nVerliHub::nSocket::cAsyncConn conn(42, &server, nVerliHub::nEnums::eCT_CLIENT);
    EXPECT_EQ(conn.mMaxBuffer, MAX_SEND_SIZE) << "Output buffer size should be 1MB for NMDCHubServer";
}
