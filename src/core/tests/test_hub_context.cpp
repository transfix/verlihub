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
#include <thread>
#include <chrono>
#include <filesystem>

#include "../hub_context.h"

using namespace nVerliHub;
using namespace std::chrono_literals;
namespace fs = std::filesystem;

// =============================================================================
// HubContext Factory Tests
// =============================================================================

class HubContextFactoryTest : public ::testing::Test {
protected:
    std::string testConfigDir;
    
    void SetUp() override {
        // Create a temporary config directory for testing
        testConfigDir = std::string(BUILD_DIR) + "/test_config_" + 
                        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
        fs::create_directories(testConfigDir);
    }
    
    void TearDown() override {
        fs::remove_all(testConfigDir);
    }
};

TEST_F(HubContextFactoryTest, CreateWithValidPath) {
    auto ctx = HubContext::Create(testConfigDir);
    ASSERT_NE(nullptr, ctx);
    
    EXPECT_EQ(testConfigDir, ctx->GetConfigDir());
    EXPECT_FALSE(ctx->IsRunning());
}

TEST_F(HubContextFactoryTest, CreateWithEmptyPath) {
    auto ctx = HubContext::Create("");
    EXPECT_EQ(nullptr, ctx);
}

TEST_F(HubContextFactoryTest, UniqueContexts) {
    auto ctx1 = HubContext::Create(testConfigDir);
    auto ctx2 = HubContext::Create(testConfigDir);
    
    ASSERT_NE(nullptr, ctx1);
    ASSERT_NE(nullptr, ctx2);
    
    // Each call should create a new, independent context
    EXPECT_NE(ctx1.get(), ctx2.get());
}

// =============================================================================
// HubContext Lifecycle Tests
// =============================================================================

class HubContextLifecycleTest : public ::testing::Test {
protected:
    std::unique_ptr<HubContext> ctx;
    std::string testConfigDir;
    
    void SetUp() override {
        testConfigDir = std::string(BUILD_DIR) + "/test_lifecycle_" +
                        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
        fs::create_directories(testConfigDir);
        ctx = HubContext::Create(testConfigDir);
        ASSERT_NE(nullptr, ctx);
    }
    
    void TearDown() override {
        ctx.reset();
        fs::remove_all(testConfigDir);
    }
};

TEST_F(HubContextLifecycleTest, Initialize) {
    EXPECT_FALSE(ctx->IsRunning());
    
    bool result = ctx->Initialize();
    EXPECT_TRUE(result);
    
    // Initialize again should succeed (idempotent)
    result = ctx->Initialize();
    EXPECT_TRUE(result);
}

TEST_F(HubContextLifecycleTest, StartRequiresInitialize) {
    // Start without Initialize should fail
    EXPECT_FALSE(ctx->Start(14117, "127.0.0.1"));
    EXPECT_FALSE(ctx->IsRunning());
}

TEST_F(HubContextLifecycleTest, StartAndStop) {
    ASSERT_TRUE(ctx->Initialize());
    
    ASSERT_TRUE(ctx->Start(14111, "127.0.0.1"));  // Use high port for CI
    EXPECT_TRUE(ctx->IsRunning());
    
    ctx->Stop();
    EXPECT_FALSE(ctx->IsRunning());
}

TEST_F(HubContextLifecycleTest, DoubleStart) {
    ASSERT_TRUE(ctx->Initialize());
    ASSERT_TRUE(ctx->Start(14112, "127.0.0.1"));
    
    // Second start should also succeed (no-op)
    EXPECT_TRUE(ctx->Start(14112, "127.0.0.1"));
    EXPECT_TRUE(ctx->IsRunning());
    
    ctx->Stop();
}

TEST_F(HubContextLifecycleTest, StopWithoutStart) {
    ctx->Initialize();
    
    // Stop without start should be a no-op (not crash)
    ctx->Stop();
    EXPECT_FALSE(ctx->IsRunning());
}

// =============================================================================
// HubContext Signal Handling Tests
// =============================================================================

class HubContextSignalTest : public ::testing::Test {
protected:
    std::unique_ptr<HubContext> ctx;
    std::string testConfigDir;
    
    void SetUp() override {
        testConfigDir = std::string(BUILD_DIR) + "/test_signal_" +
                        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
        fs::create_directories(testConfigDir);
        ctx = HubContext::Create(testConfigDir);
        ASSERT_NE(nullptr, ctx);
    }
    
    void TearDown() override {
        if (ctx && ctx->IsRunning()) {
            ctx->Stop();
        }
        ctx.reset();
        fs::remove_all(testConfigDir);
    }
};

TEST_F(HubContextSignalTest, PendingShutdownInitiallyFalse) {
    EXPECT_FALSE(ctx->HasPendingShutdown());
    EXPECT_EQ(0, ctx->GetShutdownSignal());
}

TEST_F(HubContextSignalTest, RequestShutdown) {
    ctx->RequestShutdown(15);  // SIGTERM
    
    EXPECT_TRUE(ctx->HasPendingShutdown());
    EXPECT_EQ(15, ctx->GetShutdownSignal());
}

TEST_F(HubContextSignalTest, PendingReload) {
    EXPECT_FALSE(ctx->HasPendingReload());
    
    ctx->RequestReload();
    EXPECT_TRUE(ctx->HasPendingReload());
    
    ctx->ClearPendingReload();
    EXPECT_FALSE(ctx->HasPendingReload());
}

TEST_F(HubContextSignalTest, RequestShutdownFromThread) {
    ASSERT_TRUE(ctx->Initialize());
    ASSERT_TRUE(ctx->Start(14113, "127.0.0.1"));
    
    std::thread signaler([&]() {
        std::this_thread::sleep_for(50ms);
        ctx->RequestShutdown(2);  // SIGINT
    });
    
    // Wait for shutdown signal
    while (!ctx->HasPendingShutdown()) {
        std::this_thread::sleep_for(10ms);
    }
    
    signaler.join();
    
    EXPECT_TRUE(ctx->HasPendingShutdown());
    ctx->Stop();
}

// =============================================================================
// HubContext Configuration Tests
// =============================================================================

class HubContextConfigTest : public ::testing::Test {
protected:
    std::unique_ptr<HubContext> ctx;
    std::string testConfigDir;
    
    void SetUp() override {
        testConfigDir = std::string(BUILD_DIR) + "/test_config_ctx_" +
                        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
        fs::create_directories(testConfigDir);
        ctx = HubContext::Create(testConfigDir);
        ASSERT_NE(nullptr, ctx);
        ASSERT_TRUE(ctx->Initialize());
    }
    
    void TearDown() override {
        ctx.reset();
        fs::remove_all(testConfigDir);
    }
};

TEST_F(HubContextConfigTest, GetDefaultConfig) {
    auto config = ctx->GetHubConfig();
    
    // Check defaults are set
    EXPECT_FALSE(config.hub_name.empty());
    EXPECT_GT(config.listen_port, 0);
    EXPECT_FALSE(config.listen_ip.empty());
}

TEST_F(HubContextConfigTest, SetAndGetHubTopic) {
    ctx->SetHubTopic("Test Topic 123");
    EXPECT_EQ("Test Topic 123", ctx->GetHubTopic());
    
    ctx->SetHubTopic("");
    EXPECT_EQ("", ctx->GetHubTopic());
}

TEST_F(HubContextConfigTest, GetHubName) {
    std::string name = ctx->GetHubName();
    EXPECT_FALSE(name.empty());
}

TEST_F(HubContextConfigTest, GetConfigValue) {
    // Test with known config values
    std::string encoding = ctx->GetConfig("config", "hub_encoding", "UTF-8");
    EXPECT_FALSE(encoding.empty());
    
    // Test default value for unknown key
    std::string unknown = ctx->GetConfig("config", "nonexistent_key", "DEFAULT");
    EXPECT_EQ("DEFAULT", unknown);
}

TEST_F(HubContextConfigTest, SetConfigValue) {
    EXPECT_TRUE(ctx->SetConfig("config", "hub_topic", "New Topic"));
    EXPECT_EQ("New Topic", ctx->GetConfig("config", "hub_topic"));
    
    // Unknown key should still work (return false or handle gracefully)
    bool result = ctx->SetConfig("unknown_section", "unknown_key", "value");
    // We accept either behavior as long as it doesn't crash
    SUCCEED();
}

TEST_F(HubContextConfigTest, ConfigThreadSafety) {
    constexpr int NUM_THREADS = 4;
    constexpr int OPS_PER_THREAD = 100;
    
    std::vector<std::thread> threads;
    
    for (int t = 0; t < NUM_THREADS; ++t) {
        threads.emplace_back([&, t]() {
            for (int i = 0; i < OPS_PER_THREAD; ++i) {
                // Mix of reads and writes
                if (i % 3 == 0) {
                    ctx->SetHubTopic("Thread" + std::to_string(t) + "_Topic" + std::to_string(i));
                } else {
                    std::string topic = ctx->GetHubTopic();
                    std::string name = ctx->GetHubName();
                    auto config = ctx->GetHubConfig();
                }
            }
        });
    }
    
    for (auto& t : threads) t.join();
    
    // No crashes = success
    SUCCEED();
}

// =============================================================================
// HubContext Event Callback Tests
// =============================================================================

class TestEventCallback : public IHubEventCallback {
public:
    std::atomic<int> hubStartedCount{0};
    std::atomic<int> hubStoppingCount{0};
    std::atomic<int> timerCount{0};
    std::atomic<int64_t> lastTimestamp{0};
    
    void OnHubStarted() override {
        hubStartedCount++;
    }
    
    void OnHubStopping() override {
        hubStoppingCount++;
    }
    
    void OnTimer(std::int64_t timestamp) override {
        timerCount++;
        lastTimestamp.store(timestamp);
    }
};

class HubContextEventTest : public ::testing::Test {
protected:
    std::unique_ptr<HubContext> ctx;
    std::unique_ptr<TestEventCallback> callback;
    std::string testConfigDir;
    
    void SetUp() override {
        testConfigDir = std::string(BUILD_DIR) + "/test_event_" +
                        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
        fs::create_directories(testConfigDir);
        ctx = HubContext::Create(testConfigDir);
        callback = std::make_unique<TestEventCallback>();
        ASSERT_NE(nullptr, ctx);
    }
    
    void TearDown() override {
        if (ctx && ctx->IsRunning()) {
            ctx->Stop();
        }
        ctx.reset();
        callback.reset();
        fs::remove_all(testConfigDir);
    }
};

TEST_F(HubContextEventTest, SetAndRemoveCallback) {
    ctx->SetEventCallback(callback.get());
    
    // Setting to null should work
    ctx->SetEventCallback(nullptr);
}

TEST_F(HubContextEventTest, HubStartedCallback) {
    ctx->SetEventCallback(callback.get());
    ASSERT_TRUE(ctx->Initialize());
    ASSERT_TRUE(ctx->Start(14114, "127.0.0.1"));
    
    EXPECT_EQ(1, callback->hubStartedCount.load());
    
    ctx->Stop();
}

TEST_F(HubContextEventTest, HubStoppingCallback) {
    ctx->SetEventCallback(callback.get());
    ASSERT_TRUE(ctx->Initialize());
    ASSERT_TRUE(ctx->Start(14115, "127.0.0.1"));
    
    ctx->Stop();
    
    EXPECT_EQ(1, callback->hubStoppingCount.load());
}

TEST_F(HubContextEventTest, TimerCallback) {
    ctx->SetEventCallback(callback.get());
    ASSERT_TRUE(ctx->Initialize());
    ASSERT_TRUE(ctx->Start(14116, "127.0.0.1"));
    
    // Wait for at least one timer tick
    std::this_thread::sleep_for(1500ms);
    
    EXPECT_GE(callback->timerCount.load(), 1);
    EXPECT_GT(callback->lastTimestamp.load(), 0);
    
    ctx->Stop();
}

// =============================================================================
// HubContext User Collection Tests
// =============================================================================

class HubContextUserTest : public ::testing::Test {
protected:
    std::unique_ptr<HubContext> ctx;
    std::string testConfigDir;
    
    void SetUp() override {
        testConfigDir = std::string(BUILD_DIR) + "/test_user_" +
                        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
        fs::create_directories(testConfigDir);
        ctx = HubContext::Create(testConfigDir);
        ASSERT_NE(nullptr, ctx);
        ASSERT_TRUE(ctx->Initialize());
    }
    
    void TearDown() override {
        ctx.reset();
        fs::remove_all(testConfigDir);
    }
};

TEST_F(HubContextUserTest, InitialUserCount) {
    EXPECT_EQ(0u, ctx->GetUserCount());
}

TEST_F(HubContextUserTest, InitialUserNicks) {
    auto nicks = ctx->GetUserNicks();
    EXPECT_TRUE(nicks.empty());
}

TEST_F(HubContextUserTest, FindNonexistentUser) {
    auto* user = ctx->FindUser("NonExistent");
    EXPECT_EQ(nullptr, user);
}

TEST_F(HubContextUserTest, InitialTotalShare) {
    EXPECT_EQ(0u, ctx->GetTotalShare());
}

// =============================================================================
// HubContext Messaging Tests (stub - functionality depends on cServerDC)
// =============================================================================

class HubContextMessagingTest : public ::testing::Test {
protected:
    std::unique_ptr<HubContext> ctx;
    std::string testConfigDir;
    
    void SetUp() override {
        testConfigDir = std::string(BUILD_DIR) + "/test_msg_" +
                        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
        fs::create_directories(testConfigDir);
        ctx = HubContext::Create(testConfigDir);
        ASSERT_NE(nullptr, ctx);
        ASSERT_TRUE(ctx->Initialize());
    }
    
    void TearDown() override {
        ctx.reset();
        fs::remove_all(testConfigDir);
    }
};

TEST_F(HubContextMessagingTest, SendToNonexistentUser) {
    // Should return false when user doesn't exist
    EXPECT_FALSE(ctx->SendToUser("NonExistent", "Hello"));
}

TEST_F(HubContextMessagingTest, SendToAllEmpty) {
    // Should succeed even with no users
    EXPECT_TRUE(ctx->SendToAll("Broadcast message"));
}

TEST_F(HubContextMessagingTest, SendToClassEmpty) {
    // Should succeed even with no users in class range
    EXPECT_TRUE(ctx->SendToClass("Operator message", 3, 10));
}

TEST_F(HubContextMessagingTest, KickNonexistentUser) {
    // Should return false when user doesn't exist
    EXPECT_FALSE(ctx->KickUser("Admin", "NonExistent", "Test kick"));
}
