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
#include <vector>
#include <atomic>
#include <chrono>
#include <algorithm>

#include "../thread_safe_collections.h"

using namespace nVerliHub;
using namespace std::chrono_literals;

// =============================================================================
// ThreadSafeMap Tests
// =============================================================================

class ThreadSafeMapTest : public ::testing::Test {
protected:
    ThreadSafeMap<std::string, int> map;
};

TEST_F(ThreadSafeMapTest, EmplaceAndGet) {
    EXPECT_TRUE(map.Emplace("key1", 100));
    EXPECT_TRUE(map.Emplace("key2", 200));
    
    // Emplace duplicate should fail
    EXPECT_FALSE(map.Emplace("key1", 999));
    
    EXPECT_EQ(2u, map.Size());
}

TEST_F(ThreadSafeMapTest, PutUpdates) {
    map.Put("key1", 100);
    EXPECT_EQ(1u, map.Size());
    
    // Put should update existing value
    map.Put("key1", 200);
    EXPECT_EQ(1u, map.Size());
    
    // Verify value was updated via Get
    auto result = map.Get("key1");
    EXPECT_TRUE(result.has_value());
    EXPECT_EQ(200, *result);
}

TEST_F(ThreadSafeMapTest, GetReturnsOptional) {
    map.Put("test", 42);
    
    auto found = map.Get("test");
    EXPECT_TRUE(found.has_value());
    EXPECT_EQ(42, *found);
    
    auto notFound = map.Get("nonexistent");
    EXPECT_FALSE(notFound.has_value());
}

TEST_F(ThreadSafeMapTest, GetOrDefault) {
    map.Put("key", 42);
    
    EXPECT_EQ(42, map.GetOr("key", 0));
    EXPECT_EQ(999, map.GetOr("nonexistent", 999));
}

TEST_F(ThreadSafeMapTest, Remove) {
    map.Put("key1", 100);
    map.Put("key2", 200);
    
    EXPECT_TRUE(map.Remove("key1"));
    EXPECT_FALSE(map.Remove("key1")); // Already removed
    EXPECT_EQ(1u, map.Size());
    
    EXPECT_FALSE(map.Remove("nonexistent"));
}

TEST_F(ThreadSafeMapTest, Contains) {
    map.Put("exists", 1);
    
    EXPECT_TRUE(map.Contains("exists"));
    EXPECT_FALSE(map.Contains("does_not_exist"));
}

TEST_F(ThreadSafeMapTest, Clear) {
    map.Put("key1", 100);
    map.Put("key2", 200);
    map.Put("key3", 300);
    
    EXPECT_FALSE(map.Empty());
    
    map.Clear();
    
    EXPECT_TRUE(map.Empty());
    EXPECT_EQ(0u, map.Size());
}

TEST_F(ThreadSafeMapTest, ForEach) {
    map.Put("a", 1);
    map.Put("b", 2);
    map.Put("c", 3);
    
    int sum = 0;
    map.ForEach([&sum](const std::string&, const int& val) {
        sum += val;
    });
    
    EXPECT_EQ(6, sum);
}

TEST_F(ThreadSafeMapTest, Keys) {
    map.Put("zebra", 1);
    map.Put("apple", 2);
    map.Put("mango", 3);
    
    auto keys = map.Keys();
    EXPECT_EQ(3u, keys.size());
    
    // All keys should be present
    EXPECT_NE(keys.end(), std::find(keys.begin(), keys.end(), "zebra"));
    EXPECT_NE(keys.end(), std::find(keys.begin(), keys.end(), "apple"));
    EXPECT_NE(keys.end(), std::find(keys.begin(), keys.end(), "mango"));
}

TEST_F(ThreadSafeMapTest, ConcurrentAccess) {
    constexpr int NUM_THREADS = 10;
    constexpr int OPERATIONS_PER_THREAD = 1000;
    
    std::vector<std::thread> threads;
    std::atomic<int> emplaceSuccesses{0};
    std::atomic<int> removeSuccesses{0};
    
    // Concurrent Emplace operations
    for (int t = 0; t < NUM_THREADS; ++t) {
        threads.emplace_back([&, t]() {
            for (int i = 0; i < OPERATIONS_PER_THREAD; ++i) {
                std::string key = "thread" + std::to_string(t) + "_key" + std::to_string(i);
                if (map.Emplace(key, i)) {
                    emplaceSuccesses++;
                }
            }
        });
    }
    
    for (auto& t : threads) t.join();
    threads.clear();
    
    EXPECT_EQ(NUM_THREADS * OPERATIONS_PER_THREAD, emplaceSuccesses.load());
    EXPECT_EQ(NUM_THREADS * OPERATIONS_PER_THREAD, static_cast<int>(map.Size()));
    
    // Concurrent removals
    for (int t = 0; t < NUM_THREADS; ++t) {
        threads.emplace_back([&, t]() {
            for (int i = 0; i < OPERATIONS_PER_THREAD; ++i) {
                std::string key = "thread" + std::to_string(t) + "_key" + std::to_string(i);
                if (map.Remove(key)) {
                    removeSuccesses++;
                }
            }
        });
    }
    
    for (auto& t : threads) t.join();
    
    EXPECT_EQ(NUM_THREADS * OPERATIONS_PER_THREAD, removeSuccesses.load());
    EXPECT_TRUE(map.Empty());
}

TEST_F(ThreadSafeMapTest, ConcurrentReadWrite) {
    constexpr int NUM_READERS = 5;
    constexpr int NUM_WRITERS = 3;
    constexpr int OPERATIONS = 500;
    
    std::atomic<bool> running{true};
    std::atomic<int> readCount{0};
    std::atomic<int> writeCount{0};
    
    // Pre-populate
    for (int i = 0; i < 100; ++i) {
        map.Put("key" + std::to_string(i), i);
    }
    
    std::vector<std::thread> threads;
    
    // Reader threads
    for (int t = 0; t < NUM_READERS; ++t) {
        threads.emplace_back([&]() {
            while (running.load()) {
                for (int i = 0; i < 100 && running.load(); ++i) {
                    auto val = map.Get("key" + std::to_string(i));
                    (void)val;  // Suppress unused warning
                    readCount++;
                }
            }
        });
    }
    
    // Writer threads
    for (int t = 0; t < NUM_WRITERS; ++t) {
        threads.emplace_back([&, t]() {
            for (int i = 0; i < OPERATIONS; ++i) {
                map.Put("writer" + std::to_string(t) + "_" + std::to_string(i), i);
                writeCount++;
            }
        });
    }
    
    // Wait for writers to finish  
    std::this_thread::sleep_for(100ms);
    running.store(false);
    
    for (auto& t : threads) t.join();
    
    EXPECT_EQ(NUM_WRITERS * OPERATIONS, writeCount.load());
    EXPECT_GT(readCount.load(), 0);
}

// =============================================================================
// LockFreeCounter Tests
// =============================================================================

class LockFreeCounterTest : public ::testing::Test {
protected:
    LockFreeCounter<int64_t> counter{0};
};

TEST_F(LockFreeCounterTest, InitialValue) {
    LockFreeCounter<int> c1;
    EXPECT_EQ(0, c1.Get());
    
    LockFreeCounter<int> c2{100};
    EXPECT_EQ(100, c2.Get());
}

TEST_F(LockFreeCounterTest, SetAndGet) {
    counter.Set(42);
    EXPECT_EQ(42, counter.Get());
    
    counter.Set(-100);
    EXPECT_EQ(-100, counter.Get());
}

TEST_F(LockFreeCounterTest, Increment) {
    EXPECT_EQ(1, counter.Increment());
    EXPECT_EQ(1, counter.Get());
    
    EXPECT_EQ(11, counter.Increment(10));
    EXPECT_EQ(11, counter.Get());
}

TEST_F(LockFreeCounterTest, Decrement) {
    counter.Set(100);
    
    EXPECT_EQ(99, counter.Decrement());
    EXPECT_EQ(99, counter.Get());
    
    EXPECT_EQ(89, counter.Decrement(10));
    EXPECT_EQ(89, counter.Get());
}

TEST_F(LockFreeCounterTest, Operators) {
    // Pre-increment
    EXPECT_EQ(1, ++counter);
    EXPECT_EQ(1, counter.Get());
    
    // Pre-decrement
    EXPECT_EQ(0, --counter);
    EXPECT_EQ(0, counter.Get());
    
    // Implicit conversion
    counter.Set(42);
    int64_t val = counter;
    EXPECT_EQ(42, val);
    
    // Compound assignment
    counter += 10;
    EXPECT_EQ(52, counter.Get());
    
    counter -= 2;
    EXPECT_EQ(50, counter.Get());
}

TEST_F(LockFreeCounterTest, ConcurrentIncrements) {
    constexpr int NUM_THREADS = 10;
    constexpr int INCREMENTS_PER_THREAD = 10000;
    
    std::vector<std::thread> threads;
    
    for (int t = 0; t < NUM_THREADS; ++t) {
        threads.emplace_back([&]() {
            for (int i = 0; i < INCREMENTS_PER_THREAD; ++i) {
                counter.Increment();
            }
        });
    }
    
    for (auto& t : threads) t.join();
    
    EXPECT_EQ(NUM_THREADS * INCREMENTS_PER_THREAD, counter.Get());
}

TEST_F(LockFreeCounterTest, WaitUntilNot) {
    std::thread waiter([&]() {
        counter.WaitUntilNot(0);  // Wait until counter is no longer 0
        EXPECT_NE(0, counter.Get());
    });
    
    // Small delay to ensure thread is waiting
    std::this_thread::sleep_for(10ms);
    counter.Set(42);
    
    waiter.join();
}

TEST_F(LockFreeCounterTest, WaitUntil) {
    counter.Set(0);
    
    std::thread waiter([&]() {
        counter.WaitUntil(100);  // Wait until counter equals 100
        EXPECT_EQ(100, counter.Get());
    });
    
    // Increment gradually
    std::this_thread::sleep_for(10ms);
    for (int i = 0; i < 100; ++i) {
        counter.Increment();
    }
    
    waiter.join();
}

// =============================================================================
// EventFlag Tests
// =============================================================================

class EventFlagTest : public ::testing::Test {
protected:
    EventFlag flag;
};

TEST_F(EventFlagTest, InitialState) {
    EXPECT_FALSE(flag.IsSignaled());
}

TEST_F(EventFlagTest, SignalAndReset) {
    flag.Signal();
    EXPECT_TRUE(flag.IsSignaled());
    
    flag.Reset();
    EXPECT_FALSE(flag.IsSignaled());
}

TEST_F(EventFlagTest, WaitForSignal) {
    std::atomic<bool> waiterReady{false};
    std::atomic<bool> waiterDone{false};
    
    std::thread waiter([&]() {
        waiterReady.store(true);
        flag.Wait();
        waiterDone.store(true);
        EXPECT_TRUE(flag.IsSignaled());
    });
    
    // Wait for thread to start waiting
    while (!waiterReady.load()) {
        std::this_thread::yield();
    }
    std::this_thread::sleep_for(10ms);
    
    EXPECT_FALSE(waiterDone.load());
    
    flag.Signal();
    waiter.join();
    
    EXPECT_TRUE(waiterDone.load());
}

TEST_F(EventFlagTest, MultipleWaiters) {
    constexpr int NUM_WAITERS = 5;
    std::atomic<int> wakeCount{0};
    
    std::vector<std::thread> waiters;
    for (int i = 0; i < NUM_WAITERS; ++i) {
        waiters.emplace_back([&]() {
            flag.Wait();
            wakeCount++;
        });
    }
    
    std::this_thread::sleep_for(20ms);
    EXPECT_EQ(0, wakeCount.load());
    
    flag.Signal();
    
    for (auto& t : waiters) t.join();
    
    EXPECT_EQ(NUM_WAITERS, wakeCount.load());
}

// =============================================================================
// ThreadSafeUserCollection Tests (with mock cUser)
// =============================================================================

// Mock cUser for testing (since we can't include actual cUser)
namespace nVerliHub {
class cUser {
public:
    std::string mNick;
    int mClass{0};
    
    cUser(const std::string& nick, int cls = 0) : mNick(nick), mClass(cls) {}
    virtual ~cUser() = default;
};
}

class ThreadSafeUserCollectionTest : public ::testing::Test {
protected:
    ThreadSafeUserCollection collection;
    
    cUser* createUser(const std::string& nick, int cls = 0) {
        return new cUser(nick, cls);
    }
    
    void TearDown() override {
        collection.Clear();
    }
};

TEST_F(ThreadSafeUserCollectionTest, AddAndFind) {
    auto* user1 = createUser("TestUser1");
    auto* user2 = createUser("TestUser2");
    
    EXPECT_TRUE(collection.AddUser("TestUser1", user1));
    EXPECT_TRUE(collection.AddUser("TestUser2", user2));
    
    // Duplicate add should fail
    EXPECT_FALSE(collection.AddUser("TestUser1", createUser("TestUser1")));
    
    EXPECT_EQ(2u, collection.Size());
    
    auto* found = collection.FindUser("TestUser1");
    EXPECT_EQ(user1, found);
    
    found = collection.FindUser("NonExistent");
    EXPECT_EQ(nullptr, found);
}

TEST_F(ThreadSafeUserCollectionTest, Contains) {
    collection.AddUser("ExistingUser", createUser("ExistingUser"));
    
    EXPECT_TRUE(collection.Contains("ExistingUser"));
    EXPECT_FALSE(collection.Contains("MissingUser"));
}

TEST_F(ThreadSafeUserCollectionTest, Remove) {
    auto* user = createUser("RemoveMe");
    collection.AddUser("RemoveMe", user);
    
    EXPECT_EQ(1u, collection.Size());
    
    auto* removed = collection.RemoveUser("RemoveMe");
    EXPECT_EQ(user, removed);
    EXPECT_EQ(0u, collection.Size());
    EXPECT_FALSE(collection.Contains("RemoveMe"));
    
    delete removed;  // Caller owns removed user
    
    // Remove non-existent
    EXPECT_EQ(nullptr, collection.RemoveUser("NonExistent"));
}

TEST_F(ThreadSafeUserCollectionTest, DeleteUser) {
    collection.AddUser("DeleteMe", createUser("DeleteMe"));
    
    EXPECT_TRUE(collection.DeleteUser("DeleteMe"));
    EXPECT_EQ(0u, collection.Size());
    
    // Delete non-existent
    EXPECT_FALSE(collection.DeleteUser("NonExistent"));
}

TEST_F(ThreadSafeUserCollectionTest, GetNicks) {
    collection.AddUser("Alice", createUser("Alice"));
    collection.AddUser("Bob", createUser("Bob"));
    collection.AddUser("Charlie", createUser("Charlie"));
    
    auto nicks = collection.GetNicks();
    EXPECT_EQ(3u, nicks.size());
    
    EXPECT_NE(nicks.end(), std::find(nicks.begin(), nicks.end(), "Alice"));
    EXPECT_NE(nicks.end(), std::find(nicks.begin(), nicks.end(), "Bob"));
    EXPECT_NE(nicks.end(), std::find(nicks.begin(), nicks.end(), "Charlie"));
}

TEST_F(ThreadSafeUserCollectionTest, ForEach) {
    collection.AddUser("User1", createUser("User1", 1));
    collection.AddUser("User2", createUser("User2", 2));
    collection.AddUser("User3", createUser("User3", 3));
    
    int count = 0;
    collection.ForEach([&count](cUser* user) {
        count++;
    });
    
    EXPECT_EQ(3, count);
}

TEST_F(ThreadSafeUserCollectionTest, Clear) {
    for (int i = 0; i < 10; ++i) {
        collection.AddUser("User" + std::to_string(i), createUser("User" + std::to_string(i)));
    }
    
    EXPECT_EQ(10u, collection.Size());
    
    collection.Clear();
    
    EXPECT_EQ(0u, collection.Size());
    EXPECT_TRUE(collection.GetNicks().empty());
}

TEST_F(ThreadSafeUserCollectionTest, ConcurrentAccess) {
    constexpr int NUM_THREADS = 8;
    constexpr int OPS_PER_THREAD = 500;
    
    std::atomic<int> addCount{0};
    std::atomic<int> removeCount{0};
    std::vector<std::thread> threads;
    
    // Concurrent adds
    for (int t = 0; t < NUM_THREADS; ++t) {
        threads.emplace_back([&, t]() {
            for (int i = 0; i < OPS_PER_THREAD; ++i) {
                std::string nick = "T" + std::to_string(t) + "_U" + std::to_string(i);
                if (collection.AddUser(nick, createUser(nick))) {
                    addCount++;
                }
            }
        });
    }
    
    for (auto& th : threads) th.join();
    threads.clear();
    
    EXPECT_EQ(NUM_THREADS * OPS_PER_THREAD, addCount.load());
    EXPECT_EQ(NUM_THREADS * OPS_PER_THREAD, static_cast<int>(collection.Size()));
    
    // Concurrent removes
    for (int t = 0; t < NUM_THREADS; ++t) {
        threads.emplace_back([&, t]() {
            for (int i = 0; i < OPS_PER_THREAD; ++i) {
                std::string nick = "T" + std::to_string(t) + "_U" + std::to_string(i);
                if (collection.DeleteUser(nick)) {
                    removeCount++;
                }
            }
        });
    }
    
    for (auto& th : threads) th.join();
    
    EXPECT_EQ(NUM_THREADS * OPS_PER_THREAD, removeCount.load());
    EXPECT_EQ(0u, collection.Size());
}
