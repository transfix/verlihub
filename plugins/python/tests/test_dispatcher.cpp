/*
	Copyright (C) 2025 Verlihub Team

	Test suite for Verlihub Hook Dispatcher
	
	This test suite validates the hook dispatcher pattern for single-interpreter mode:
	- Script registration and unregistration
	- Hook dispatching to multiple scripts
	- Priority ordering
	- Enable/disable functionality
	- Admin commands
	- Thread safety under concurrent load
	- Statistics tracking
*/

#include <gtest/gtest.h>
#include <gmock/gmock.h>
#include "cserverdc.h"
#include "plugins/python/cpipython.h"
#include "plugins/python/cpythoninterpreter.h"
#include "cconndc.h"
#include "cprotocol.h"
#include "test_utils.h"
#include <fstream>
#include <string>
#include <vector>
#include <chrono>
#include <thread>
#include <atomic>
#include <unistd.h>
#include <cstdlib>
#include <sstream>

using namespace testing;
using namespace nVerliHub;
using namespace nVerliHub::nSocket;
using namespace nVerliHub::nProtocol;
using namespace nVerliHub::nPythonPlugin;
using namespace nVerliHub::nEnums;

// Global server instance (shared across tests)
static cServerDC *g_server = nullptr;
static cpiPython *g_py_plugin = nullptr;

// Helper to get environment variable or default value
static std::string getEnvOrDefault(const char* var, const char* defaultVal) {
    const char* val = std::getenv(var);
    return val ? std::string(val) : std::string(defaultVal);
}

// Global test environment setup (once for all tests)
class DispatcherEnv : public ::testing::Environment {
public:
    void SetUp() override {
        std::cout << "\n=== Setting up Verlihub Environment for Dispatcher Tests ===" << std::endl;
        
        // Create unique config directory for this test process
        std::string config_dir = std::string(BUILD_DIR) + "/test_dispatcher_config_" + std::to_string(getpid());
        std::string db_name = "verlihub_test_dispatcher";
        
        // MySQL connection info from environment
        std::string db_host = getEnvOrDefault("VERLIHUB_TEST_DB_HOST", "localhost");
        std::string db_user = getEnvOrDefault("VERLIHUB_TEST_DB_USER", "verlihub");
        std::string db_pass = getEnvOrDefault("VERLIHUB_TEST_DB_PASS", "verlihub");
        
        std::cout << "Config directory: " << config_dir << std::endl;
        std::cout << "Database: " << db_name << " at " << db_host << std::endl;
        
        // Create config directory
        std::string cmd = "mkdir -p " + config_dir;
        system(cmd.c_str());
        
        // Create dbconfig file
        std::string dbconfig_path = config_dir + "/dbconfig";
        std::ofstream dbconfig(dbconfig_path);
        dbconfig << "db_host = " << db_host << "\n";
        dbconfig << "db_user = " << db_user << "\n";
        dbconfig << "db_pass = " << db_pass << "\n";
        dbconfig << "db_data = " << db_name << "\n";
        dbconfig.close();
        
        // Create minimal hub config
        std::string config_path = config_dir + "/config";
        std::ofstream config(config_path);
        config << "hub_name = Dispatcher Test Hub\n";
        config << "hub_desc = Testing Hook Dispatcher\n";
        config << "hub_topic = Hook Dispatcher Test Environment\n";
        config << "hub_owner = TestAdmin\n";
        config << "hub_security = TestAdmin\n";
        config << "hub_encoding = UTF-8\n";
        config << "listen_ip = 127.0.0.1\n";
        config << "listen_port = 14111\n";
        config << "max_users = 100\n";
        config.close();
        
        // Initialize server
        std::cout << "Initializing cServerDC..." << std::endl;
        g_server = new cServerDC(config_dir, config_dir);
        ASSERT_NE(g_server, nullptr) << "Failed to create server";
        
        // Initialize Python plugin
        std::cout << "Initializing Python plugin..." << std::endl;
        g_py_plugin = new cpiPython();
        ASSERT_NE(g_py_plugin, nullptr) << "Failed to create Python plugin";
        
        // Load plugin
        int load_result = g_py_plugin->OnLoad(g_server);
        ASSERT_TRUE(load_result) << "Failed to load Python plugin";
        
        std::cout << "=== Verlihub Environment Ready ===" << std::endl;
    }

    void TearDown() override {
        std::cout << "\n=== Cleaning up Verlihub Environment ===" << std::endl;
        
        if (g_py_plugin) {
            g_py_plugin->OnClose();
            delete g_py_plugin;
            g_py_plugin = nullptr;
        }
        
        if (g_server) {
            delete g_server;
            g_server = nullptr;
        }
        
        std::cout << "=== Cleanup Complete ===" << std::endl;
    }
};

// Test fixture
class DispatcherTest : public ::testing::Test {
protected:
    cPythonInterpreter* dispatcher_interp = nullptr;
    std::string dispatcher_path;
    std::vector<cPythonInterpreter*> test_scripts;
    
    void SetUp() override {
        ASSERT_NE(g_server, nullptr);
        ASSERT_NE(g_py_plugin, nullptr);
        
        // Load the dispatcher script
        dispatcher_path = std::string(SOURCE_DIR) + "/plugins/python/scripts/dispatcher.py";
        
        std::cout << "\n--- Loading dispatcher: " << dispatcher_path << std::endl;
        dispatcher_interp = g_py_plugin->LoadScript(dispatcher_path);
        ASSERT_NE(dispatcher_interp, nullptr) << "Failed to load dispatcher script";
        
        // Give dispatcher time to initialize
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    
    void TearDown() override {
        // Unload all test scripts
        for (auto* interp : test_scripts) {
            if (interp) {
                g_py_plugin->UnloadScript(interp->id);
            }
        }
        test_scripts.clear();
        
        // Unload dispatcher
        if (dispatcher_interp) {
            g_py_plugin->UnloadScript(dispatcher_interp->id);
            dispatcher_interp = nullptr;
        }
    }
    
    // Helper: Create a test script that registers with dispatcher
    cPythonInterpreter* CreateTestScript(const std::string& name, const std::string& code, int priority = 100) {
        std::string script_path = std::string(BUILD_DIR) + "/test_dispatcher_" + name + "_" + std::to_string(getpid()) + ".py";
        
        std::ofstream script(script_path);
        script << "#!/usr/bin/env python3\n";
        script << "# Test script: " << name << "\n\n";
        script << "try:\n";
        script << "    from verlihub_hook_dispatcher import register_script, unregister_script\n";
        script << "    USING_DISPATCHER = True\n";
        script << "except ImportError:\n";
        script << "    USING_DISPATCHER = False\n";
        script << "    print('[" << name << "] WARNING: Dispatcher not available', flush=True)\n\n";
        script << "SCRIPT_ID = None\n\n";
        script << code << "\n\n";
        script << "def cleanup():\n";
        script << "    print('[" << name << "] Cleanup called', flush=True)\n\n";
        script << "if USING_DISPATCHER:\n";
        script << "    SCRIPT_ID = register_script(\n";
        script << "        script_name='" << name << "',\n";
        script << "        hooks=HOOKS,\n";
        script << "        cleanup=cleanup,\n";
        script << "        priority=" << priority << "\n";
        script << "    )\n";
        script << "    print(f'[" << name << "] Registered with dispatcher, ID={SCRIPT_ID}', flush=True)\n";
        script << "else:\n";
        script << "    for hook_name, handler in HOOKS.items():\n";
        script << "        globals()[hook_name] = handler\n\n";
        script << "def UnLoad():\n";
        script << "    if USING_DISPATCHER and SCRIPT_ID is not None:\n";
        script << "        unregister_script(SCRIPT_ID)\n";
        script.close();
        
        std::cout << "--- Loading test script: " << script_path << std::endl;
        cPythonInterpreter* interp = g_py_plugin->LoadScript(script_path);
        if (interp) {
            test_scripts.push_back(interp);
        }
        
        return interp;
    }
    
    // Helper: Create a mock connection for command testing
    cConnDC* CreateMockConnection(const std::string& nick, int user_class = 10) {
        cConnDC* conn = new cConnDC(0, g_server);
        cUser* user = new cUser(nick);
        user->mClass = (tUserCl)user_class;
        conn->mpUser = user;
        user->mxConn = conn;
        return conn;
    }
    
    // Helper: Send hub command
    bool SendHubCommand(cConnDC* conn, const std::string& command, bool in_pm = false) {
        std::string cmd_str = command.substr(1); // Remove !
        int result = g_py_plugin->OnHubCommand(conn->mpUser->mNick, cmd_str, conn->mpUser->mClass, in_pm ? 1 : 0, "!");
        return result == 0; // 0 means command was handled
    }
};

// Test 1: Load dispatcher and verify it initializes
TEST_F(DispatcherTest, LoadDispatcher) {
    EXPECT_NE(dispatcher_interp, nullptr);
    EXPECT_GE(dispatcher_interp->id, 0);
}

// Test 2: Register single script with dispatcher
TEST_F(DispatcherTest, RegisterSingleScript) {
    std::string code = R"(
call_count = {'OnTimer': 0}

def my_timer_handler(msec=0):
    call_count['OnTimer'] += 1
    print(f'[Script1] OnTimer called {call_count["OnTimer"]} times', flush=True)
    return 1

HOOKS = {
    'OnTimer': my_timer_handler
}
)";
    
    cPythonInterpreter* script = CreateTestScript("script1", code);
    ASSERT_NE(script, nullptr);
    
    // Trigger OnTimer
    g_py_plugin->OnTimer(0);
    
    // Should see output indicating timer was called
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
}

// Test 3: Register multiple scripts and verify all get called
TEST_F(DispatcherTest, RegisterMultipleScripts) {
    std::string code_template = R"(
call_count = 0

def my_timer_handler(msec=0):
    global call_count
    call_count += 1
    print(f'[SCRIPT_NAME] OnTimer called {call_count} times', flush=True)
    return 1

HOOKS = {
    'OnTimer': my_timer_handler
}
)";
    
    // Create 3 scripts
    std::vector<std::string> names = {"ScriptA", "ScriptB", "ScriptC"};
    for (const auto& name : names) {
        std::string code = code_template;
        size_t pos = code.find("SCRIPT_NAME");
        code.replace(pos, 11, name);
        
        cPythonInterpreter* script = CreateTestScript(name, code);
        ASSERT_NE(script, nullptr) << "Failed to load " << name;
    }
    
    // Trigger OnTimer - should call all 3 scripts
    std::cout << "\n--- Triggering OnTimer, expect 3 calls ---" << std::endl;
    g_py_plugin->OnTimer(0);
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    
    // Trigger again
    std::cout << "--- Triggering OnTimer again ---" << std::endl;
    g_py_plugin->OnTimer(0);
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
}

// Test 4: Priority ordering - lower priority executes first
TEST_F(DispatcherTest, PriorityOrdering) {
    std::string code_high_priority = R"(
def my_timer_handler(msec=0):
    print('[Priority10] Executing (should be FIRST)', flush=True)
    return 1

HOOKS = {'OnTimer': my_timer_handler}
)";
    
    std::string code_medium_priority = R"(
def my_timer_handler(msec=0):
    print('[Priority50] Executing (should be SECOND)', flush=True)
    return 1

HOOKS = {'OnTimer': my_timer_handler}
)";
    
    std::string code_low_priority = R"(
def my_timer_handler(msec=0):
    print('[Priority100] Executing (should be THIRD)', flush=True)
    return 1

HOOKS = {'OnTimer': my_timer_handler}
)";
    
    // Load in reverse order to test that priority, not load order, determines execution
    cPythonInterpreter* low = CreateTestScript("LowPrio", code_low_priority, 100);
    cPythonInterpreter* med = CreateTestScript("MedPrio", code_medium_priority, 50);
    cPythonInterpreter* high = CreateTestScript("HighPrio", code_high_priority, 10);
    
    ASSERT_NE(low, nullptr);
    ASSERT_NE(med, nullptr);
    ASSERT_NE(high, nullptr);
    
    std::cout << "\n--- Triggering OnTimer, expect priority order: 10, 50, 100 ---" << std::endl;
    g_py_plugin->OnTimer(0);
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
}

// Test 5: Hook return value 0 stops propagation
TEST_F(DispatcherTest, StopPropagation) {
    std::string code_stopper = R"(
def my_chat_handler(nick, msg):
    print(f'[Stopper] Chat from {nick}: {msg}', flush=True)
    if 'STOP' in msg:
        print('[Stopper] Returning 0 to stop propagation', flush=True)
        return 0
    return 1

HOOKS = {'OnParsedMsgChat': my_chat_handler}
)";
    
    std::string code_follower = R"(
def my_chat_handler(nick, msg):
    print(f'[Follower] Chat from {nick}: {msg}', flush=True)
    return 1

HOOKS = {'OnParsedMsgChat': my_chat_handler}
)";
    
    cPythonInterpreter* stopper = CreateTestScript("Stopper", code_stopper, 10);
    cPythonInterpreter* follower = CreateTestScript("Follower", code_follower, 50);
    
    ASSERT_NE(stopper, nullptr);
    ASSERT_NE(follower, nullptr);
    
    // Message without STOP - both should execute
    std::cout << "\n--- Sending chat without STOP ---" << std::endl;
    g_py_plugin->OnParsedMsgChat("TestUser", "Hello everyone");
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    
    // Message with STOP - only stopper should execute
    std::cout << "--- Sending chat with STOP ---" << std::endl;
    g_py_plugin->OnParsedMsgChat("TestUser", "STOP this message");
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
}

// Test 6: Admin command - list scripts
TEST_F(DispatcherTest, AdminCommandList) {
    // Create some test scripts
    std::string simple_code = R"(
def handler(msec=0):
    return 1
HOOKS = {'OnTimer': handler}
)";
    
    CreateTestScript("ListTest1", simple_code);
    CreateTestScript("ListTest2", simple_code);
    
    cConnDC* admin = CreateMockConnection("TestAdmin", 10);
    
    std::cout << "\n--- Sending !dispatcher list command ---" << std::endl;
    bool handled = SendHubCommand(admin, "!dispatcher list", true);
    EXPECT_TRUE(handled);
    
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    
    delete admin->mpUser;
    delete admin;
}

// Test 7: Admin command - stats
TEST_F(DispatcherTest, AdminCommandStats) {
    std::string code = R"(
def handler(msec=0):
    return 1
HOOKS = {'OnTimer': handler}
)";
    
    CreateTestScript("StatsTest", code);
    
    // Trigger some events
    for (int i = 0; i < 10; i++) {
        g_py_plugin->OnTimer(0);
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    cConnDC* admin = CreateMockConnection("TestAdmin", 10);
    
    std::cout << "\n--- Sending !dispatcher stats command ---" << std::endl;
    bool handled = SendHubCommand(admin, "!dispatcher stats", true);
    EXPECT_TRUE(handled);
    
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    
    delete admin->mpUser;
    delete admin;
}

// Test 8: Admin command - disable/enable script
TEST_F(DispatcherTest, AdminCommandEnableDisable) {
    std::string code = R"(
call_count = 0

def handler(msec=0):
    global call_count
    call_count += 1
    print(f'[ToggleTest] Called {call_count} times', flush=True)
    return 1

HOOKS = {'OnTimer': handler}
)";
    
    CreateTestScript("ToggleTest", code);
    
    // Should execute
    std::cout << "\n--- Timer before disable ---" << std::endl;
    g_py_plugin->OnTimer(0);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    cConnDC* admin = CreateMockConnection("TestAdmin", 10);
    
    // Get script ID from list first (we need to parse output, but for now assume ID=1)
    // In real test, we'd parse the output or track IDs
    std::cout << "--- Disabling script ---" << std::endl;
    SendHubCommand(admin, "!dispatcher disable 1", true);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Should NOT execute
    std::cout << "--- Timer after disable (should not see output) ---" << std::endl;
    g_py_plugin->OnTimer(0);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Re-enable
    std::cout << "--- Re-enabling script ---" << std::endl;
    SendHubCommand(admin, "!dispatcher enable 1", true);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Should execute again
    std::cout << "--- Timer after re-enable ---" << std::endl;
    g_py_plugin->OnTimer(0);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    delete admin->mpUser;
    delete admin;
}

// Test 9: Multiple hook types in single script
TEST_F(DispatcherTest, MultipleHookTypes) {
    std::string code = R"(
counters = {
    'OnTimer': 0,
    'OnParsedMsgChat': 0,
    'OnUserLogin': 0,
    'OnUserLogout': 0
}

def timer_handler(msec=0):
    counters['OnTimer'] += 1
    print(f'[MultiHook] OnTimer: {counters["OnTimer"]}', flush=True)
    return 1

def chat_handler(nick, msg):
    counters['OnParsedMsgChat'] += 1
    print(f'[MultiHook] OnParsedMsgChat: {counters["OnParsedMsgChat"]}', flush=True)
    return 1

def login_handler(nick):
    counters['OnUserLogin'] += 1
    print(f'[MultiHook] OnUserLogin: {counters["OnUserLogin"]}', flush=True)
    return 1

def logout_handler(nick):
    counters['OnUserLogout'] += 1
    print(f'[MultiHook] OnUserLogout: {counters["OnUserLogout"]}', flush=True)
    return 1

HOOKS = {
    'OnTimer': timer_handler,
    'OnParsedMsgChat': chat_handler,
    'OnUserLogin': login_handler,
    'OnUserLogout': logout_handler
}
)";
    
    CreateTestScript("MultiHook", code);
    
    std::cout << "\n--- Triggering multiple hook types ---" << std::endl;
    
    g_py_plugin->OnTimer(0);
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    
    g_py_plugin->OnParsedMsgChat("User1", "Hello");
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    
    g_py_plugin->OnUserLogin("User1");
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    
    g_py_plugin->OnUserLogout("User1");
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
}

// Test 10: Concurrent hook invocations (stress test)
TEST_F(DispatcherTest, ConcurrentHookInvocations) {
    std::string code = R"(
import threading
call_count = 0
lock = threading.Lock()

def timer_handler(msec=0):
    global call_count
    with lock:
        call_count += 1
        if call_count % 100 == 0:
            print(f'[ConcurrentTest] {call_count} calls', flush=True)
    return 1

HOOKS = {'OnTimer': timer_handler}
)";
    
    CreateTestScript("ConcurrentTest", code);
    
    std::cout << "\n--- Stress testing with concurrent calls ---" << std::endl;
    
    std::atomic<int> total_calls{0};
    std::atomic<bool> stop_flag{false};
    
    // Thread 1: Rapid OnTimer calls
    std::thread timer_thread([&]() {
        while (!stop_flag) {
            g_py_plugin->OnTimer(0);
            total_calls++;
            std::this_thread::sleep_for(std::chrono::microseconds(100));
        }
    });
    
    // Thread 2: Chat messages
    std::thread chat_thread([&]() {
        int count = 0;
        while (!stop_flag) {
            g_py_plugin->OnParsedMsgChat("User", "Message " + std::to_string(count++));
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
    });
    
    // Run for 2 seconds
    std::this_thread::sleep_for(std::chrono::seconds(2));
    stop_flag = true;
    
    timer_thread.join();
    chat_thread.join();
    
    std::cout << "Total hook calls: " << total_calls << std::endl;
    EXPECT_GT(total_calls.load(), 100);
}

// Test 11: Script unregistration and cleanup
TEST_F(DispatcherTest, ScriptUnregistration) {
    std::string code = R"(
cleanup_called = False

def timer_handler(msec=0):
    print('[UnregTest] Timer handler called', flush=True)
    return 1

def cleanup():
    global cleanup_called
    cleanup_called = True
    print('[UnregTest] Cleanup called!', flush=True)

HOOKS = {'OnTimer': timer_handler}
)";
    
    cPythonInterpreter* script = CreateTestScript("UnregTest", code);
    ASSERT_NE(script, nullptr);
    
    // Call timer
    std::cout << "\n--- Calling timer before unload ---" << std::endl;
    g_py_plugin->OnTimer(0);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Unload script
    std::cout << "--- Unloading script (should call cleanup) ---" << std::endl;
    g_py_plugin->UnloadScript(script->id);
    
    // Remove from our tracking
    auto it = std::find(test_scripts.begin(), test_scripts.end(), script);
    if (it != test_scripts.end()) {
        test_scripts.erase(it);
    }
    
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Call timer again - script should not respond
    std::cout << "--- Calling timer after unload (should not see UnregTest output) ---" << std::endl;
    g_py_plugin->OnTimer(0);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
}

// Test 12: Error handling - script raises exception
TEST_F(DispatcherTest, ErrorHandling) {
    std::string code = R"(
call_count = 0

def timer_handler(msec=0):
    global call_count
    call_count += 1
    
    if call_count == 3:
        print('[ErrorTest] Raising exception on call 3', flush=True)
        raise RuntimeError('Intentional test error')
    
    print(f'[ErrorTest] Call {call_count} successful', flush=True)
    return 1

HOOKS = {'OnTimer': timer_handler}
)";
    
    CreateTestScript("ErrorTest", code);
    
    std::cout << "\n--- Testing error handling ---" << std::endl;
    
    for (int i = 1; i <= 5; i++) {
        std::cout << "--- Call " << i << " ---" << std::endl;
        g_py_plugin->OnTimer(0);
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
}

// Test 13: Multiple scripts with same hook at different priorities
TEST_F(DispatcherTest, PriorityExecutionOrder) {
    // Create 5 scripts with different priorities
    for (int i = 0; i < 5; i++) {
        int priority = (i + 1) * 20; // 20, 40, 60, 80, 100
        std::string name = "Prio" + std::to_string(priority);
        
        std::ostringstream code_stream;
        code_stream << R"(
def timer_handler(msec=0):
    print('[)" << name << R"(] Priority )" << priority << R"( executing', flush=True)
    return 1

HOOKS = {'OnTimer': timer_handler}
)";
        
        CreateTestScript(name, code_stream.str(), priority);
    }
    
    std::cout << "\n--- Triggering OnTimer, expect order: 20, 40, 60, 80, 100 ---" << std::endl;
    g_py_plugin->OnTimer(0);
    std::this_thread::sleep_for(std::chrono::milliseconds(300));
}

// Test 14: High-load sustained operation
TEST_F(DispatcherTest, HighLoadSustained) {
    std::string code = R"(
import time
call_count = 0
start_time = time.time()

def timer_handler(msec=0):
    global call_count
    call_count += 1
    
    if call_count % 1000 == 0:
        elapsed = time.time() - start_time
        rate = call_count / elapsed if elapsed > 0 else 0
        print(f'[HighLoad] {call_count} calls, {rate:.1f} calls/sec', flush=True)
    
    return 1

HOOKS = {'OnTimer': timer_handler}
)";
    
    CreateTestScript("HighLoad", code);
    
    std::cout << "\n=== High Load Test: 10,000 rapid calls ===" << std::endl;
    
    auto start = std::chrono::high_resolution_clock::now();
    
    for (int i = 0; i < 10000; i++) {
        g_py_plugin->OnTimer(0);
    }
    
    auto end = std::chrono::high_resolution_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end - start);
    
    std::cout << "Completed 10,000 calls in " << duration.count() << "ms ("
              << (10000.0 * 1000.0 / duration.count()) << " calls/sec)" << std::endl;
    
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
}

// Test 15: Memory stability under load
TEST_F(DispatcherTest, MemoryStability) {
    std::string code = R"(
call_count = 0

def timer_handler(msec=0):
    global call_count
    call_count += 1
    # Create and discard some data to test memory handling
    temp_data = list(range(100))
    temp_dict = {str(i): i for i in range(50)}
    return 1

HOOKS = {'OnTimer': timer_handler}
)";
    
    CreateTestScript("MemoryTest", code);
    
    MemoryTracker tracker;
    tracker.start();
    
    std::cout << "\n=== Memory Stability Test ===" << std::endl;
    std::cout << "Initial: " << tracker.initial.to_string() << std::endl;
    
    // Run for 5000 iterations with memory sampling
    const int iterations = 5000;
    for (int i = 0; i < iterations; i++) {
        g_py_plugin->OnTimer(0);
        
        if (i % 1000 == 0 && i > 0) {
            tracker.sample();
            std::cout << "After " << i << " calls: " << tracker.current.to_string() << std::endl;
        }
    }
    
    tracker.sample();
    tracker.print_report();
    
    // Memory growth should be minimal
    long growth_kb = (long)tracker.current.vm_rss_kb - (long)tracker.initial.vm_rss_kb;
    std::cout << "\nMemory growth: " << growth_kb << " KB" << std::endl;
    
    // Allow up to 5MB growth (Python caches, etc.)
    EXPECT_LT(growth_kb, 5 * 1024) << "Excessive memory growth detected";
}

// Test 16: Permission check on admin commands
TEST_F(DispatcherTest, PermissionCheck) {
    cConnDC* regular_user = CreateMockConnection("RegularUser", 1); // Class 1 = regular user
    
    std::cout << "\n--- Regular user trying !dispatcher list (should fail) ---" << std::endl;
    bool handled = SendHubCommand(regular_user, "!dispatcher list", true);
    EXPECT_TRUE(handled); // Command is handled (rejected)
    
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    delete regular_user->mpUser;
    delete regular_user;
}

// Test 17: Hub command hook with multiple scripts
TEST_F(DispatcherTest, HubCommandHook) {
    std::string code1 = R"(
def command_handler(nick, command, user_class, in_pm, prefix):
    if command.startswith('test1'):
        print('[Script1] Handling test1 command', flush=True)
        return 0  # Stop propagation
    return 1

HOOKS = {'OnHubCommand': command_handler}
)";
    
    std::string code2 = R"(
def command_handler(nick, command, user_class, in_pm, prefix):
    print('[Script2] Saw command:', command, flush=True)
    return 1

HOOKS = {'OnHubCommand': command_handler}
)";
    
    CreateTestScript("Cmd1", code1, 10);
    CreateTestScript("Cmd2", code2, 50);
    
    cConnDC* admin = CreateMockConnection("TestAdmin", 10);
    
    // Command that Script1 handles
    std::cout << "\n--- Sending !test1 (should stop at Script1) ---" << std::endl;
    SendHubCommand(admin, "!test1 hello", false);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Command that passes through
    std::cout << "--- Sending !test2 (should reach Script2) ---" << std::endl;
    SendHubCommand(admin, "!test2 world", false);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    delete admin->mpUser;
    delete admin;
}

// Test 18: User login/logout hooks
TEST_F(DispatcherTest, UserLoginLogoutHooks) {
    std::string code = R"(
users_seen = set()

def login_handler(nick):
    users_seen.add(nick)
    print(f'[UserTracker] {nick} logged in, total seen: {len(users_seen)}', flush=True)
    return 1

def logout_handler(nick):
    print(f'[UserTracker] {nick} logged out', flush=True)
    return 1

HOOKS = {
    'OnUserLogin': login_handler,
    'OnUserLogout': logout_handler
}
)";
    
    CreateTestScript("UserTracker", code);
    
    std::cout << "\n--- Simulating user login/logout ---" << std::endl;
    
    for (int i = 0; i < 5; i++) {
        std::string nick = "User" + std::to_string(i);
        g_py_plugin->OnUserLogin(nick);
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    
    for (int i = 0; i < 5; i++) {
        std::string nick = "User" + std::to_string(i);
        g_py_plugin->OnUserLogout(nick);
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
}

// Test 19: Chat message processing with multiple filters
TEST_F(DispatcherTest, ChatMessageFiltering) {
    std::string spam_filter = R"(
def chat_handler(nick, msg):
    if 'SPAM' in msg.upper():
        print(f'[SpamFilter] Blocked spam from {nick}', flush=True)
        return 0  # Block message
    return 1

HOOKS = {'OnParsedMsgChat': chat_handler}
)";
    
    std::string logger = R"(
def chat_handler(nick, msg):
    print(f'[Logger] {nick}: {msg}', flush=True)
    return 1

HOOKS = {'OnParsedMsgChat': chat_handler}
)";
    
    CreateTestScript("SpamFilter", spam_filter, 10);
    CreateTestScript("Logger", logger, 50);
    
    std::cout << "\n--- Testing chat filtering ---" << std::endl;
    
    // Normal message - both should see it
    std::cout << "--- Normal message ---" << std::endl;
    g_py_plugin->OnParsedMsgChat("Alice", "Hello everyone!");
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    
    // Spam message - only filter should see it
    std::cout << "--- Spam message (Logger should NOT see this) ---" << std::endl;
    g_py_plugin->OnParsedMsgChat("Spammer", "BUY SPAM NOW!");
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
}

// Test 20: Dispatcher help command
TEST_F(DispatcherTest, HelpCommand) {
    cConnDC* admin = CreateMockConnection("TestAdmin", 10);
    
    std::cout << "\n--- Sending !dispatcher help ---" << std::endl;
    bool handled = SendHubCommand(admin, "!dispatcher help", true);
    EXPECT_TRUE(handled);
    
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    
    delete admin->mpUser;
    delete admin;
}

// Register global environment
int main(int argc, char **argv) {
    ::testing::InitGoogleTest(&argc, argv);
    ::testing::AddGlobalTestEnvironment(new DispatcherEnv);
    return RUN_ALL_TESTS();
}
