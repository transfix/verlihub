# Thin Verlihub Architecture Plan

## Executive Summary

This document outlines a major refactoring of Verlihub to create a "thin" architecture where:

- **Python (FastAPI + SQLModel)** handles: Web API, Dashboard, Database operations, Configuration management
- **C++ Core Library** handles: NMDC protocol, Connection management, Plugin system, Real-time message processing
- **SWIG Bindings** connect Python to the C++ core

**Critical Requirements:**
1. The C++ core must be **fully thread-safe**
2. **Zero singletons or global variables** in the C++ core
3. All state passed explicitly through function parameters or object members
4. Clear ownership semantics for all objects

---

## Table of Contents

1. [Current State Analysis](#1-current-state-analysis)
2. [Target Architecture](#2-target-architecture)
3. [C++20 Requirements](#3-c20-requirements)
4. [C++ Core Refactoring](#4-c-core-refactoring)
5. [Thread Safety Design](#5-thread-safety-design)
6. [SWIG Integration](#6-swig-integration)
7. [Python Application Layer](#7-python-application-layer)
8. [SQLModel Database Layer](#8-sqlmodel-database-layer)
9. [Migration Strategy](#9-migration-strategy)
10. [Implementation Phases](#10-implementation-phases)
11. [Testing Strategy](#11-testing-strategy)

---

## 1. Current State Analysis

### 1.1 Identified Global State (Must Be Eliminated)

#### Singletons and Static Pointers

| Location | Variable | Current Usage | Refactoring Approach |
|----------|----------|---------------|---------------------|
| `cserverdc.h:637` | `static cServerDC *sCurrentServer` | Global server access | Pass `cServerDC*` explicitly |
| `cpipython.h:195` | `static cpiPython *me` | Plugin self-reference | Use instance pointer from plugin manager |
| `cpipython.h:194` | `static nSocket::cServerDC *server` | Server access in plugin | Pass through constructor |
| `cpilua.cpp:40-41` | `static cServerDC *server; static cpiLua *me` | Same pattern as Python | Same fix |
| `script_api.cpp:42` | `GetCurrentVerlihub()` uses `sCurrentServer` | Global server accessor | Remove, pass server explicitly |

#### Global Signal Handlers

| Location | Variable | Purpose | Refactoring Approach |
|----------|----------|---------|---------------------|
| `cserverdc.cpp:57` | `volatile sig_atomic_t pending_signal_quit` | Signal handling | Move to server instance member |
| `cserverdc.cpp:58` | `volatile sig_atomic_t pending_signal_hup` | Reload signal | Move to server instance member |
| `cserverdc.cpp:59` | `volatile sig_atomic_t pending_signal_crash` | Crash handling | Move to server instance member |

#### Static Buffers

| Location | Variable | Purpose | Refactoring Approach |
|----------|----------|---------|---------------------|
| `i18n.cpp:30` | `static char my_autosprintf_buffer[...]` | String formatting | Use thread-local or pass buffer |

### 1.2 Current Threading Model

The current codebase has:
- `cMutex` class wrapping `pthread_mutex_t`
- `cThread` base class for worker threads
- `cWorkerThread` for background tasks
- **No consistent locking strategy** for shared data structures

### 1.3 Thread-Unsafe Patterns Found

1. **User collection access** without locks (mUserList, mOpList, etc.)
2. **Configuration reads/writes** without synchronization
3. **Plugin callbacks** from multiple threads without protection
4. **Message routing** with shared state

---

## 2. Target Architecture

### 2.1 High-Level Design

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           Thin Verlihub Stack                                │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │                    Python Application (FastAPI)                        │  │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │  │
│  │  │   Web Dashboard │  │    REST API     │  │    SQLModel ORM         │ │  │
│  │  │   (Vue/React)   │  │   Endpoints     │  │   Database Layer        │ │  │
│  │  └────────┬────────┘  └────────┬────────┘  └────────────┬────────────┘ │  │
│  │           │                    │                        │              │  │
│  │           └────────────────────┼────────────────────────┘              │  │
│  │                                │                                       │  │
│  │                    ┌───────────┴───────────┐                           │  │
│  │                    │     hub_bridge.py     │                           │  │
│  │                    │  (Python ↔ C++ Bridge)│                           │  │
│  │                    └───────────┬───────────┘                           │  │
│  └────────────────────────────────┼───────────────────────────────────────┘  │
│                                   │                                          │
│                    ┌──────────────┴──────────────┐                           │
│                    │      SWIG Bindings          │                           │
│                    │  (_verlihub_core.so)        │                           │
│                    └──────────────┬──────────────┘                           │
│                                   │                                          │
│  ┌────────────────────────────────┴────────────────────────────────────────┐ │
│  │                     C++ Core Library (libverlihub_core.so)              │ │
│  │                                                                          │ │
│  │  ┌─────────────────────────────────────────────────────────────────┐    │ │
│  │  │                    HubContext (replaces globals)                │    │ │
│  │  │  - Owns all components                                          │    │ │
│  │  │  - Passed to all functions needing hub access                   │    │ │
│  │  │  - Thread-safe through internal locking                         │    │ │
│  │  └─────────────────────────────────────────────────────────────────┘    │ │
│  │                                                                          │ │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐ │ │
│  │  │ NMDC Proto  │  │ Connection  │  │ Plugin Mgr  │  │ Message Router  │ │ │
│  │  │  Handler    │  │   Manager   │  │ (Lua,Py,C++)│  │  (Lock-free)    │ │ │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────┘ │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │                    Database (MySQL/PostgreSQL)                          │ │
│  │  Accessed by: Python (SQLModel) for all reads/writes                    │ │
│  │               C++ Core (read-only cache via Python callbacks)           │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Directory Structure

```
verlihub/
├── pyproject.toml                 # Python project config (Poetry/PDM)
├── alembic.ini                    # Database migrations config
│
├── verlihub_py/                   # Python package (main application)
│   ├── __init__.py
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Pydantic settings
│   │
│   ├── api/                       # FastAPI routers
│   │   ├── __init__.py
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   ├── hub.py             # Hub stats, info
│   │   │   ├── users.py           # User management
│   │   │   ├── bans.py            # Ban management
│   │   │   ├── config.py          # Configuration CRUD
│   │   │   ├── plugins.py         # Plugin management
│   │   │   ├── triggers.py        # Trigger management
│   │   │   ├── clients.py         # DC++ client management
│   │   │   └── websocket.py       # Real-time WebSocket events
│   │   └── deps.py                # Dependency injection
│   │
│   ├── models/                    # SQLModel models
│   │   ├── __init__.py
│   │   ├── base.py                # Base model class
│   │   ├── user.py                # RegUser model
│   │   ├── ban.py                 # Ban, Unban models
│   │   ├── config.py              # SetupList model
│   │   ├── plugin.py              # Plugin registry model
│   │   ├── trigger.py             # Trigger model
│   │   ├── client.py              # DC++ client model
│   │   ├── kick.py                # Kick history model
│   │   ├── conn_type.py           # Connection types
│   │   ├── redirect.py            # Custom redirects
│   │   └── temp_rights.py         # Temporary rights
│   │
│   ├── schemas/                   # Pydantic schemas (API request/response)
│   │   └── ...
│   │
│   ├── services/                  # Business logic layer
│   │   └── ...
│   │
│   ├── core/                      # Core Python integration
│   │   ├── __init__.py
│   │   ├── hub_bridge.py          # Bridge to C++ core via SWIG
│   │   ├── events.py              # Event system (pub/sub)
│   │   └── cache.py               # Caching layer
│   │
│   ├── client/                    # Remote HubContext client library
│   │   ├── __init__.py
│   │   ├── hub_client.py          # Sync client (mirrors HubBridge API)
│   │   └── async_hub_client.py    # Async client for asyncio/FastAPI
│   │
│   ├── db/                        # Database utilities
│   │   ├── __init__.py
│   │   ├── session.py             # SQLModel session factory
│   │   └── migrations/            # Alembic migrations
│   │
│   └── dashboard/                 # Web dashboard
│       ├── templates/
│       └── static/
│
├── src/                           # C++ core (refactored)
│   ├── CMakeLists.txt
│   │
│   ├── core/                      # Core library sources
│   │   ├── hub_context.h/.cpp     # NEW: Central context object
│   │   ├── thread_safe_collections.h  # NEW: Thread-safe containers
│   │   │
│   │   ├── protocol/              # NMDC protocol handling
│   │   │   ├── cdcproto.cpp/h     # (refactored - no globals)
│   │   │   ├── cmessagedc.cpp/h
│   │   │   └── ...
│   │   │
│   │   ├── connection/            # Connection management
│   │   │   ├── casyncconn.cpp/h   # (refactored - no globals)
│   │   │   ├── cconndc.cpp/h
│   │   │   └── ...
│   │   │
│   │   ├── user/                  # User session (in-memory)
│   │   │   ├── cuser.cpp/h        # (refactored - no globals)
│   │   │   ├── cusercollection.cpp/h  # (thread-safe)
│   │   │   └── ...
│   │   │
│   │   ├── plugin/                # Plugin system
│   │   │   ├── cvhpluginmgr.cpp/h # (refactored - no statics)
│   │   │   └── ...
│   │   │
│   │   └── server/                # Server core
│   │       ├── cserverdc.cpp/h    # (refactored - no sCurrentServer)
│   │       └── ...
│   │
│   └── swig/                      # SWIG interface files
│       ├── verlihub_core.i        # Main SWIG interface
│       ├── hub_context.i          # Context object bindings
│       └── callbacks.i            # Callback directors
│
├── plugins/                       # C++ plugins (refactored)
│   ├── lua/                       # (refactored - no statics)
│   └── python/                    # Legacy (deprecated, replaced by native Python)
│
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
│
└── tests/
    ├── cpp/                       # C++ unit tests (Google Test)
    │   ├── test_hub_context.cpp
    │   ├── test_thread_safety.cpp
    │   └── ...
    ├── test_api/                  # Python API tests
    ├── test_models/               # SQLModel tests
    └── test_integration/          # Full integration tests
```

---

## 3. C++20 Requirements

### 3.1 Compiler and Standard

The refactored C++ core **requires C++20** as the minimum standard. This enables:

| Feature | Usage | Benefit |
|---------|-------|---------|
| `std::jthread` | Worker threads | Automatic RAII join, stop tokens |
| `std::stop_token` | Thread cancellation | Cooperative, safe shutdown |
| `std::atomic<T>::wait/notify` | Lock-free sync | Efficient thread coordination |
| `std::span<T>` | Buffer views | Safe, zero-copy buffer passing |
| `std::format` | String formatting | Type-safe printf replacement |
| `std::ranges` | Collection operations | Cleaner iteration, filtering |
| Concepts | Template constraints | Better error messages, self-documenting |
| Designated initializers | Struct init | Clearer struct construction |
| `[[likely]]`/`[[unlikely]]` | Branch hints | Performance optimization |
| `constexpr` containers | Compile-time strings | Static initialization |
| Three-way comparison `<=>` | Ordering | Simplified comparisons |
| `std::source_location` | Error reporting | Replace `__FILE__`/`__LINE__` |

### 3.2 CMake Configuration

```cmake
# Require C++20
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Compiler-specific flags
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "11.0")
        message(FATAL_ERROR "GCC 11+ required for C++20 support")
    endif()
    add_compile_options(-fconcepts-diagnostics-depth=2)
elseif(CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
    if(CMAKE_CXX_COMPILER_VERSION VERSION_LESS "14.0")
        message(FATAL_ERROR "Clang 14+ required for C++20 support")
    endif()
endif()
```

### 3.3 Key C++20 Patterns Used

#### Concepts for Type Safety

```cpp
#include <concepts>

// Concept for types that can be used as collection keys
template<typename T>
concept Hashable = requires(T a) {
    { std::hash<T>{}(a) } -> std::convertible_to<std::size_t>;
};

// Concept for callback handlers
template<typename F, typename... Args>
concept Invocable = std::invocable<F, Args...>;

// Thread-safe map only accepts hashable keys
template<Hashable K, typename V>
class ThreadSafeMap { /* ... */ };
```

#### std::jthread for Automatic Thread Management

```cpp
#include <thread>
#include <stop_token>

class WorkerPool {
public:
    void Start() {
        // jthread automatically joins on destruction
        // stop_token enables cooperative cancellation
        m_workers.emplace_back([this](std::stop_token st) {
            while (!st.stop_requested()) {
                ProcessWork();
            }
        });
    }
    
    void Stop() {
        // Request stop on all threads (they check stop_token)
        for (auto& worker : m_workers) {
            worker.request_stop();
        }
        // jthread destructor will join automatically
    }
    
private:
    std::vector<std::jthread> m_workers;
};
```

#### std::format for Safe String Formatting

```cpp
#include <format>
#include <source_location>

void Log(std::string_view msg, 
         std::source_location loc = std::source_location::current()) {
    auto formatted = std::format("[{}:{}] {}", 
        loc.file_name(), loc.line(), msg);
    // ...
}

// Usage:
Log(std::format("User {} connected from {}", nick, ip));
```

#### std::span for Safe Buffer Handling

```cpp
#include <span>

// Instead of raw pointers + length:
// void ProcessData(const char* data, size_t len);

// Use span for safety:
void ProcessData(std::span<const char> data) {
    for (char c : data) {  // Safe iteration
        // ...
    }
}
```

#### Ranges for Cleaner Collection Operations

```cpp
#include <ranges>
#include <algorithm>

std::vector<std::string> GetOperatorNicks() const {
    return m_users 
        | std::views::filter([](const auto& u) { return u.IsOperator(); })
        | std::views::transform([](const auto& u) { return u.GetNick(); })
        | std::ranges::to<std::vector>();
}
```

#### Designated Initializers

```cpp
struct HubConfig {
    std::string name;
    std::string topic;
    int port = 411;
    bool tls_enabled = false;
};

// Clear, self-documenting initialization
auto config = HubConfig{
    .name = "My Hub",
    .topic = "Welcome!",
    .port = 4111,
    .tls_enabled = true,
};
```

#### Atomic Wait/Notify for Efficient Synchronization

```cpp
#include <atomic>

class EventFlag {
public:
    void Signal() {
        m_flag.store(true);
        m_flag.notify_all();
    }
    
    void Wait() {
        m_flag.wait(false);  // Block until flag is true
    }
    
    void Reset() {
        m_flag.store(false);
    }
    
private:
    std::atomic<bool> m_flag{false};
};
```

### 3.4 Deprecated Patterns to Replace

| Old Pattern | C++20 Replacement |
|-------------|-------------------|
| `pthread_*` functions | `std::jthread`, `std::mutex` |
| `sprintf`/`snprintf` | `std::format` |
| Raw pointer + length | `std::span` |
| SFINAE template tricks | Concepts |
| `__FILE__`, `__LINE__` | `std::source_location` |
| Manual thread join | `std::jthread` (auto-join) |
| `volatile` for threading | `std::atomic` |
| Signal-based thread stop | `std::stop_token` |

---

## 4. C++ Core Refactoring

### 3.1 The HubContext Pattern

Replace all global state with a single context object that is explicitly passed:

```cpp
// src/core/hub_context.h
#ifndef HUB_CONTEXT_H
#define HUB_CONTEXT_H

#include <memory>
#include <shared_mutex>
#include <atomic>
#include <functional>

namespace nVerliHub {

// Forward declarations
class cServerDC;
class cUserCollection;
class cVHPluginMgr;
class cDCProto;
class cICUConvert;
class cMaxMindDB;
class cMySQL;

/**
 * HubContext - Central context object replacing all global state
 * 
 * This object:
 * - Owns all major hub components
 * - Is passed explicitly to all functions needing hub access
 * - Provides thread-safe access to shared state
 * - Has a well-defined lifetime (created once, destroyed on shutdown)
 */
class HubContext {
public:
    // Factory method - only way to create a HubContext
    static std::unique_ptr<HubContext> Create(const std::string& config_dir);
    
    // No copying or moving
    HubContext(const HubContext&) = delete;
    HubContext& operator=(const HubContext&) = delete;
    HubContext(HubContext&&) = delete;
    HubContext& operator=(HubContext&&) = delete;
    
    ~HubContext();

    // =====================================================================
    // Lifecycle Management
    // =====================================================================
    
    bool Initialize();
    bool Start(int port, const std::string& listen_ip = "0.0.0.0");
    void Stop();
    bool IsRunning() const { return m_running.load(); }
    
    // =====================================================================
    // Signal Handling (instance-based, not global)
    // =====================================================================
    
    void RequestShutdown(int signal_code);
    void RequestReload();
    bool HasPendingShutdown() const { return m_pending_shutdown.load(); }
    bool HasPendingReload() const { return m_pending_reload.load(); }
    int GetShutdownSignal() const { return m_shutdown_signal.load(); }
    void ClearPendingReload() { m_pending_reload.store(false); }
    
    // =====================================================================
    // Component Access (no ownership transfer)
    // =====================================================================
    
    cServerDC* GetServer() const { return m_server.get(); }
    cVHPluginMgr* GetPluginManager() const { return m_plugin_mgr.get(); }
    cDCProto* GetProtocol() const { return m_protocol.get(); }
    cICUConvert* GetICUConverter() const { return m_icu_convert.get(); }
    cMaxMindDB* GetGeoIP() const { return m_geoip.get(); }
    
    // =====================================================================
    // Thread-Safe User Operations
    // =====================================================================
    
    // Get user count (lock-free atomic read)
    size_t GetUserCount() const;
    
    // Get copy of user list (thread-safe snapshot)
    std::vector<std::string> GetUserNicks() const;
    
    // Find user by nick (returns nullptr if not found)
    // NOTE: The returned pointer is borrowed, caller must not store it
    cUser* FindUser(const std::string& nick) const;
    
    // Execute callback for each user (thread-safe iteration)
    void ForEachUser(std::function<void(cUser*)> callback) const;
    
    // =====================================================================
    // Thread-Safe Configuration Access
    // =====================================================================
    
    std::string GetConfig(const std::string& section, const std::string& key,
                          const std::string& default_val = "") const;
    bool SetConfig(const std::string& section, const std::string& key,
                   const std::string& value);
    
    // =====================================================================
    // Thread-Safe Messaging
    // =====================================================================
    
    bool SendToUser(const std::string& nick, const std::string& message);
    bool SendToAll(const std::string& message);
    bool SendToClass(const std::string& message, int min_class, int max_class);
    bool SendToOpChat(const std::string& message, const std::string& from = "");
    
    // =====================================================================
    // Thread-Safe User Management
    // =====================================================================
    
    bool KickUser(const std::string& op_nick, const std::string& nick,
                  const std::string& reason);
    bool AddRobot(const std::string& nick, const std::string& description,
                  int user_class);
    bool RemoveRobot(const std::string& nick);
    
    // =====================================================================
    // Hub Information (thread-safe reads)
    // =====================================================================
    
    std::string GetHubName() const;
    std::string GetHubTopic() const;
    uint64_t GetTotalShare() const;
    std::string GetHubEncoding() const;
    
    // =====================================================================
    // Callback Registration for Python Bridge
    // =====================================================================
    
    using EventCallback = std::function<bool(const std::string& event_type,
                                              const std::string& json_data)>;
    
    void SetEventCallback(EventCallback callback);
    
private:
    explicit HubContext(const std::string& config_dir);
    
    // Fire event to Python (thread-safe)
    void FireEvent(const std::string& event_type, const std::string& json_data);
    
    // Configuration directory
    std::string m_config_dir;
    
    // Atomic flags for signal handling
    std::atomic<bool> m_running{false};
    std::atomic<bool> m_pending_shutdown{false};
    std::atomic<bool> m_pending_reload{false};
    std::atomic<int> m_shutdown_signal{0};
    
    // Owned components (unique_ptr for clear ownership)
    std::unique_ptr<cServerDC> m_server;
    std::unique_ptr<cVHPluginMgr> m_plugin_mgr;
    std::unique_ptr<cDCProto> m_protocol;
    std::unique_ptr<cICUConvert> m_icu_convert;
    std::unique_ptr<cMaxMindDB> m_geoip;
    
    // Mutex for configuration access
    mutable std::shared_mutex m_config_mutex;
    
    // Mutex for event callback
    mutable std::mutex m_callback_mutex;
    EventCallback m_event_callback;
};

}  // namespace nVerliHub

#endif  // HUB_CONTEXT_H
```

### 3.2 Thread-Safe Collections

```cpp
// src/core/thread_safe_collections.h
#ifndef THREAD_SAFE_COLLECTIONS_H
#define THREAD_SAFE_COLLECTIONS_H

#include <shared_mutex>
#include <unordered_map>
#include <vector>
#include <functional>
#include <optional>

namespace nVerliHub {

/**
 * Thread-safe hash map with reader-writer locking
 */
template<typename K, typename V>
class ThreadSafeMap {
public:
    // Insert or update
    void Put(const K& key, const V& value) {
        std::unique_lock lock(m_mutex);
        m_map[key] = value;
    }
    
    // Get value (returns nullopt if not found)
    std::optional<V> Get(const K& key) const {
        std::shared_lock lock(m_mutex);
        auto it = m_map.find(key);
        if (it != m_map.end()) {
            return it->second;
        }
        return std::nullopt;
    }
    
    // Remove by key
    bool Remove(const K& key) {
        std::unique_lock lock(m_mutex);
        return m_map.erase(key) > 0;
    }
    
    // Check existence
    bool Contains(const K& key) const {
        std::shared_lock lock(m_mutex);
        return m_map.count(key) > 0;
    }
    
    // Get size
    size_t Size() const {
        std::shared_lock lock(m_mutex);
        return m_map.size();
    }
    
    // Get all keys (snapshot)
    std::vector<K> Keys() const {
        std::shared_lock lock(m_mutex);
        std::vector<K> keys;
        keys.reserve(m_map.size());
        for (const auto& [key, _] : m_map) {
            keys.push_back(key);
        }
        return keys;
    }
    
    // Execute callback for each entry (with read lock)
    void ForEach(std::function<void(const K&, const V&)> callback) const {
        std::shared_lock lock(m_mutex);
        for (const auto& [key, value] : m_map) {
            callback(key, value);
        }
    }
    
    // Execute callback for each entry (with write lock, allows modification)
    void ForEachMut(std::function<void(const K&, V&)> callback) {
        std::unique_lock lock(m_mutex);
        for (auto& [key, value] : m_map) {
            callback(key, value);
        }
    }
    
    // Clear all entries
    void Clear() {
        std::unique_lock lock(m_mutex);
        m_map.clear();
    }

private:
    mutable std::shared_mutex m_mutex;
    std::unordered_map<K, V> m_map;
};

/**
 * Thread-safe user collection (specialized for cUser*)
 * 
 * Provides lock-free reads where possible using atomic reference counting
 * and copy-on-write for the user list.
 */
class ThreadSafeUserCollection {
public:
    // Add user (takes ownership)
    void AddUser(const std::string& nick, cUser* user);
    
    // Remove user (returns owned pointer, caller must delete)
    cUser* RemoveUser(const std::string& nick);
    
    // Find user (borrowed pointer, may become invalid)
    cUser* FindUser(const std::string& nick) const;
    
    // Get user count (lock-free)
    size_t Size() const { return m_count.load(); }
    
    // Get snapshot of all nicks
    std::vector<std::string> GetNicks() const;
    
    // Iterate with callback (holds lock during iteration)
    void ForEach(std::function<void(cUser*)> callback) const;
    
    // Iterate with filter
    void ForEachWithClass(std::function<void(cUser*)> callback,
                          int min_class, int max_class) const;

private:
    mutable std::shared_mutex m_mutex;
    std::unordered_map<std::string, cUser*> m_users;
    std::atomic<size_t> m_count{0};
};

}  // namespace nVerliHub

#endif  // THREAD_SAFE_COLLECTIONS_H
```

### 3.3 Refactored cServerDC

Key changes to `cServerDC`:

```cpp
// Changes to src/cserverdc.h

class cServerDC : public cAsyncSocketServer {
public:
    // NEW: Constructor takes HubContext reference (not owning)
    explicit cServerDC(HubContext& context);
    
    // REMOVED: static cServerDC *sCurrentServer;
    
    // NEW: Access to parent context
    HubContext& GetContext() { return m_context; }
    const HubContext& GetContext() const { return m_context; }
    
    // CHANGED: Signal flags moved to HubContext
    // REMOVED: These are now in HubContext
    // - pending_signal_quit
    // - pending_signal_hup
    // - pending_signal_crash
    
    // ... rest of interface unchanged but implementations updated
    // to use m_context instead of sCurrentServer
    
private:
    HubContext& m_context;  // Reference to parent context
    
    // Thread-safe user collections (replacing old mUserList etc.)
    ThreadSafeUserCollection m_users;
    ThreadSafeUserCollection m_operators;
    ThreadSafeUserCollection m_bots;
    // ...
};
```

### 3.4 Refactored Plugin System

```cpp
// Changes to plugins - example for Python plugin

class cpiPython : public nPlugin::cVHPlugin {
public:
    // NEW: Constructor takes context reference
    explicit cpiPython(HubContext& context);
    
    // REMOVED: static cpiPython *me;
    // REMOVED: static nSocket::cServerDC *server;
    // REMOVED: static string botname;
    // REMOVED: static string opchatname;
    // REMOVED: static int log_level;
    
    // NEW: Instance members instead of statics
    HubContext& GetContext() { return m_context; }
    const std::string& GetBotName() const { return m_botname; }
    int GetLogLevel() const { return m_log_level; }
    
private:
    HubContext& m_context;
    std::string m_botname;
    std::string m_opchatname;
    int m_log_level;
};
```

### 3.5 Refactored Script API

```cpp
// src/script_api.h - Refactored to not use globals

namespace nVerliHub {

// REMOVED: cServerDC* GetCurrentVerlihub()
// All functions now take HubContext as first parameter

bool SendDataToUser(HubContext& ctx, const char *data, const char *nick, bool delay = false);
bool SendToClass(HubContext& ctx, const char *data, int min_class, int max_class, bool delay = false);
bool SendToAll(HubContext& ctx, const char *data, bool delay = false);
bool KickUser(HubContext& ctx, const char *oper, const char *nick, const char *reason);
// ... all other functions follow same pattern

}  // namespace nVerliHub
```

---

## 4. Thread Safety Design

### 4.1 Locking Hierarchy

To prevent deadlocks, we define a strict locking order:

```
Level 1 (highest): HubContext main lock
Level 2: User collection locks
Level 3: Individual user locks
Level 4: Connection locks
Level 5: Plugin manager lock
Level 6: Configuration locks
Level 7 (lowest): Logging locks
```

**Rule:** Never acquire a higher-level lock while holding a lower-level lock.

### 4.2 Lock Types by Operation

| Operation | Lock Type | Scope |
|-----------|-----------|-------|
| Read user count | Atomic (lock-free) | N/A |
| Read user list | Shared (reader) | User collection |
| Add/remove user | Exclusive (writer) | User collection |
| Read config | Shared (reader) | Config section |
| Write config | Exclusive (writer) | Config section |
| Send message | Shared on user lookup, then connection lock | User + Connection |
| Plugin callback | Reader lock on plugin list | Plugin manager |

### 4.3 Thread-Safe Patterns

#### Pattern 1: Atomic Counters for Statistics

```cpp
// Use std::atomic for frequently-read counters
std::atomic<uint64_t> m_total_share{0};
std::atomic<size_t> m_user_count{0};
std::atomic<size_t> m_op_count{0};
```

#### Pattern 2: Copy-on-Write for Lists

```cpp
// For frequently-read, rarely-modified lists
std::shared_ptr<const std::vector<std::string>> GetUserNicks() const {
    std::shared_lock lock(m_mutex);
    return m_user_nicks_snapshot;
}

void OnUserListChanged() {
    std::unique_lock lock(m_mutex);
    auto new_list = std::make_shared<std::vector<std::string>>();
    // ... build new list
    m_user_nicks_snapshot = new_list;  // Atomic pointer swap
}
```

#### Pattern 3: Scoped Locks with RAII

```cpp
// Always use RAII lock guards
class UserCollectionReadLock {
public:
    explicit UserCollectionReadLock(const ThreadSafeUserCollection& coll)
        : m_lock(coll.m_mutex) {}
private:
    std::shared_lock<std::shared_mutex> m_lock;
};
```

#### Pattern 4: Lock-Free Message Queues

```cpp
// For high-throughput message passing between threads
#include <concurrentqueue.h>  // moodycamel's lock-free queue

struct OutgoingMessage {
    std::string target_nick;
    std::string data;
    bool delay;
};

moodycamel::ConcurrentQueue<OutgoingMessage> m_outgoing_queue;
```

### 4.4 Thread Roles

| Thread | Responsibility | Locks Typically Held |
|--------|----------------|---------------------|
| Main Event Loop | Accept connections, dispatch messages | User collection (brief) |
| Worker Threads | Process parsed messages | Connection locks |
| Timer Thread | Periodic tasks | Various (short duration) |
| Python Thread(s) | FastAPI handlers | Callback mutex |

---

### 4.5 SSL/TLS Support via FearTLS

The refactored architecture maintains full SSL/TLS support using the FearTLS proxy library. TLS termination happens at the proxy layer before traffic reaches the hub core.

#### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        External Network                              │
└─────────────────────────┬───────────────────────────────────────────┘
                          │ TLS-encrypted connections (port 411)
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    FearTLS Proxy (libFearTLS.so)                    │
│  - TLS 1.2/1.3 termination                                          │
│  - Certificate management                                           │
│  - NMDC protocol passthrough                                        │
└─────────────────────────┬───────────────────────────────────────────┘
                          │ Unencrypted local connections (127.0.0.1)
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       Hub Core (HubContext)                         │
│  - Receives plain NMDC protocol                                     │
│  - No TLS handling required in core logic                           │
└─────────────────────────────────────────────────────────────────────┘
```

#### FearTLS Library Interface

```cpp
// feartls/feartls.h - External TLS proxy library

struct VH_FearConf {
    const char *FAddr;    // External listening address
    int FPort;            // External TLS port
    const char *FHost;    // Hub address (usually 127.0.0.1)
    const char *FCert;    // Path to certificate file
    const char *FKey;     // Path to private key file
    bool FLog;            // Enable logging
    int FWait;            // Connection timeout
    int FVer;             // Minimum TLS version (0=1.0, 1=1.1, 2=1.2, 3=1.3)
    bool FSend;           // Send TLS info to hub
};

extern "C" {
    extern bool VH_FearStart(VH_FearConf *);  // Start TLS proxy
    extern void VH_FearStop(int);              // Stop TLS proxy
}
```

#### TLS Configuration in HubContext

```cpp
// src/core/hub_config.h

struct TlsConfig {
    bool enabled = false;                    // Enable TLS proxy
    std::string listen_ip = "0.0.0.0";      // External IP for proxy
    int listen_port = 411;                   // External TLS port
    std::string local_ip = "127.0.0.1";     // Local hub address
    int local_port = 4111;                   // Local hub port
    std::string cert_path;                   // Certificate file path
    std::string key_path;                    // Private key file path
    int min_version = 2;                     // Minimum TLS version (1.2 default)
    bool tls_only = false;                   // Require TLS for all connections
    std::string extra_ports;                 // Additional listening ports
};

struct HubConfig {
    // ... other config
    TlsConfig tls;
};
```

#### TLS Lifecycle Management

```cpp
// src/core/tls_manager.h

class TlsManager {
public:
    explicit TlsManager(const TlsConfig& config);
    ~TlsManager();
    
    // Lifecycle
    [[nodiscard]] bool Start();
    void Stop();
    [[nodiscard]] bool IsRunning() const { return m_running.load(); }
    
    // Status
    struct TlsStatus {
        bool enabled;
        bool running;
        std::string cert_subject;
        std::string cert_expiry;
        std::string tls_version;
        uint64_t connections_accepted;
        uint64_t connections_rejected;
    };
    TlsStatus GetStatus() const;
    
    // Certificate management
    [[nodiscard]] bool ReloadCertificates();
    [[nodiscard]] bool ValidateCertificate() const;
    
private:
    TlsConfig m_config;
    std::atomic<bool> m_running{false};
    VH_FearConf m_fear_config{};
    
    void BuildFearConfig();
};
```

#### Python API for TLS Management

```python
# verlihub/tls.py - Python TLS management

from dataclasses import dataclass
from verlihub import verlihub_core

@dataclass
class TlsStatus:
    enabled: bool
    running: bool
    cert_subject: str
    cert_expiry: str
    tls_version: str
    connections_accepted: int
    connections_rejected: int

class TlsManager:
    """High-level TLS management for the hub."""
    
    def __init__(self, hub_context: verlihub_core.HubContext):
        self._ctx = hub_context
    
    def enable(self, cert_path: str, key_path: str, 
               min_version: int = 2) -> bool:
        """Enable TLS with the specified certificate."""
        return self._ctx.EnableTls(cert_path, key_path, min_version)
    
    def disable(self) -> bool:
        """Disable TLS proxy."""
        return self._ctx.DisableTls()
    
    def get_status(self) -> TlsStatus:
        """Get current TLS status."""
        status = self._ctx.GetTlsStatus()
        return TlsStatus(
            enabled=status.enabled,
            running=status.running,
            cert_subject=status.cert_subject,
            cert_expiry=status.cert_expiry,
            tls_version=status.tls_version,
            connections_accepted=status.connections_accepted,
            connections_rejected=status.connections_rejected,
        )
    
    def reload_certificates(self) -> bool:
        """Reload certificates without restart."""
        return self._ctx.ReloadTlsCertificates()
```

#### FastAPI TLS Endpoints

| Endpoint | Method | Permission | Description |
|----------|--------|------------|-------------|
| `/api/v1/tls/status` | GET | 10 | Get TLS proxy status |
| `/api/v1/tls/enable` | POST | 11 | Enable TLS with cert/key |
| `/api/v1/tls/disable` | POST | 11 | Disable TLS proxy |
| `/api/v1/tls/reload` | POST | 11 | Reload certificates |
| `/api/v1/tls/certificate` | GET | 10 | Get certificate info |

#### Certificate Generation Helper

```bash
# Generate self-signed certificate (development)
openssl req -new -newkey rsa:4096 -x509 -sha256 \
    -days 1800 -nodes \
    -out "hub.crt" -keyout "hub.key" \
    -subj "/CN=myhub.example.com"

# Generate with Let's Encrypt (production)
certbot certonly --standalone -d myhub.example.com
```

#### Configuration Variables (Database)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `tls_listen_ip` | string | `127.0.0.1` | Local address when TLS enabled |
| `tls_listen_port` | int | `0` | Local port (0 = TLS disabled) |
| `tls_min_ver` | int | `2` | Minimum TLS version |
| `tls_only_mode` | bool | `false` | Require TLS for all users |
| `tls_cert_path` | string | `""` | Path to certificate file |
| `tls_key_path` | string | `""` | Path to private key file |

---

## 5. SWIG Integration

### 5.1 Main SWIG Interface

```swig
// src/swig/verlihub_core.i
%module(directors="1") verlihub_core

%{
#include "core/hub_context.h"
#include "core/thread_safe_collections.h"
%}

// Enable directors for callbacks from C++ to Python
%feature("director") IHubEventHandler;

// Standard library support
%include <std_string.i>
%include <std_vector.i>
%include <std_shared_ptr.i>
%include <stdint.i>

// Templates for vector types
%template(StringVector) std::vector<std::string>;

// Exception handling - convert C++ exceptions to Python
%exception {
    try {
        $action
    } catch (const std::exception& e) {
        PyErr_SetString(PyExc_RuntimeError, e.what());
        SWIG_fail;
    } catch (...) {
        PyErr_SetString(PyExc_RuntimeError, "Unknown C++ exception");
        SWIG_fail;
    }
}

// Thread safety - release GIL during C++ calls
%exception {
    Py_BEGIN_ALLOW_THREADS
    try {
        $action
    } catch (const std::exception& e) {
        Py_BLOCK_THREADS
        PyErr_SetString(PyExc_RuntimeError, e.what());
        SWIG_fail;
    }
    Py_END_ALLOW_THREADS
}

// Callback interface (implemented in Python)
%feature("director") IHubEventHandler;

%inline %{
class IHubEventHandler {
public:
    virtual ~IHubEventHandler() {}
    
    // Return false to block the action
    virtual bool OnUserConnect(const char* nick, const char* ip) { return true; }
    virtual void OnUserDisconnect(const char* nick) {}
    virtual bool OnUserLogin(const char* nick, int user_class) { return true; }
    virtual void OnUserLogout(const char* nick) {}
    virtual bool OnChatMessage(const char* nick, const char* message) { return true; }
    virtual bool OnPrivateMessage(const char* from, const char* to, const char* msg) { return true; }
    virtual bool OnSearch(const char* nick, const char* query) { return true; }
    virtual void OnTimer(long timestamp) {}
    virtual void OnHubStarted() {}
    virtual void OnHubStopping() {}
};
%}

// Include the main context interface
%include "hub_context.i"
```

### 5.2 Hub Context SWIG Interface

```swig
// src/swig/hub_context.i

// Prevent copying
%ignore nVerliHub::HubContext::HubContext(const HubContext&);
%ignore nVerliHub::HubContext::operator=;

// Use shared_ptr for proper Python garbage collection
%shared_ptr(nVerliHub::HubContext)

namespace nVerliHub {

class HubContext {
public:
    // Factory (returns unique_ptr, SWIG converts to shared_ptr for Python)
    static std::unique_ptr<HubContext> Create(const std::string& config_dir);
    
    // Lifecycle
    bool Initialize();
    bool Start(int port, const std::string& listen_ip = "0.0.0.0");
    void Stop();
    bool IsRunning() const;
    
    // Signal handling
    void RequestShutdown(int signal_code);
    void RequestReload();
    bool HasPendingShutdown() const;
    bool HasPendingReload() const;
    
    // User operations
    size_t GetUserCount() const;
    std::vector<std::string> GetUserNicks() const;
    
    // Messaging
    bool SendToUser(const std::string& nick, const std::string& message);
    bool SendToAll(const std::string& message);
    bool SendToClass(const std::string& message, int min_class, int max_class);
    bool SendToOpChat(const std::string& message, const std::string& from = "");
    
    // User management
    bool KickUser(const std::string& op_nick, const std::string& nick,
                  const std::string& reason);
    bool AddRobot(const std::string& nick, const std::string& description,
                  int user_class);
    bool RemoveRobot(const std::string& nick);
    
    // Hub info
    std::string GetHubName() const;
    std::string GetHubTopic() const;
    uint64_t GetTotalShare() const;
    std::string GetHubEncoding() const;
    
    // Config
    std::string GetConfig(const std::string& section, const std::string& key,
                          const std::string& default_val = "") const;
    bool SetConfig(const std::string& section, const std::string& key,
                   const std::string& value);
    
    // Event handler registration
    // %extend below to handle Python callback properly
};

// Extend to add Python-friendly event handler
%extend HubContext {
    void SetEventHandler(IHubEventHandler* handler) {
        // The handler is implemented in Python via director
        // Store reference and wire up callbacks
        $self->SetEventCallback([handler](const std::string& event,
                                           const std::string& data) -> bool {
            // Dispatch to appropriate handler method based on event type
            // This is called from C++ threads, directors handle GIL
            return true;
        });
    }
}

}  // namespace nVerliHub
```

### 5.3 CMake Build for SWIG

```cmake
# src/swig/CMakeLists.txt

find_package(SWIG 4.0 REQUIRED)
find_package(Python3 COMPONENTS Interpreter Development REQUIRED)

include(UseSWIG)

# SWIG configuration
set(CMAKE_SWIG_FLAGS "-py3" "-threads")
set_property(SOURCE verlihub_core.i PROPERTY CPLUSPLUS ON)
set_property(SOURCE verlihub_core.i PROPERTY SWIG_MODULE_NAME verlihub_core)

# Add SWIG module
swig_add_library(verlihub_core
    TYPE SHARED
    LANGUAGE python
    SOURCES verlihub_core.i
)

# Link against core library and Python
target_link_libraries(verlihub_core
    PRIVATE
    verlihub_core_lib
    Python3::Python
)

# Include directories
target_include_directories(verlihub_core
    PRIVATE
    ${CMAKE_SOURCE_DIR}/src
    ${CMAKE_SOURCE_DIR}/src/core
)

# Install to Python site-packages
install(
    TARGETS verlihub_core
    LIBRARY DESTINATION ${Python3_SITELIB}/verlihub_py
)
install(
    FILES ${CMAKE_CURRENT_BINARY_DIR}/verlihub_core.py
    DESTINATION ${Python3_SITELIB}/verlihub_py
)
```

---

## 6. Python Application Layer

### 6.1 Hub Bridge Module

```python
# verlihub_py/core/hub_bridge.py
"""
Thread-safe bridge between Python FastAPI application and C++ hub core.

This module provides:
- Lifecycle management for the C++ hub
- Event handling from C++ to Python
- Thread-safe access to hub operations
"""

from typing import Optional, List, Callable, Any
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import logging

# Import SWIG-generated module
from verlihub_py import verlihub_core

logger = logging.getLogger(__name__)


class HubEventHandler(verlihub_core.IHubEventHandler):
    """
    Python implementation of hub event callbacks.
    
    This class is instantiated once and registered with the C++ core.
    Methods are called from C++ threads - SWIG handles GIL acquisition.
    """
    
    def __init__(self, event_bus: "EventBus"):
        super().__init__()
        self._event_bus = event_bus
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the asyncio event loop for dispatching events."""
        self._loop = loop
    
    def _emit(self, event_type: str, **kwargs):
        """Emit event to asyncio event bus (thread-safe)."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._event_bus.emit(event_type, **kwargs),
                self._loop
            )
    
    def OnUserConnect(self, nick: str, ip: str) -> bool:
        self._emit("user_connect", nick=nick, ip=ip)
        return True  # Return False to reject connection
    
    def OnUserDisconnect(self, nick: str):
        self._emit("user_disconnect", nick=nick)
    
    def OnUserLogin(self, nick: str, user_class: int) -> bool:
        self._emit("user_login", nick=nick, user_class=user_class)
        return True
    
    def OnUserLogout(self, nick: str):
        self._emit("user_logout", nick=nick)
    
    def OnChatMessage(self, nick: str, message: str) -> bool:
        self._emit("chat_message", nick=nick, message=message)
        return True  # Return False to block message
    
    def OnPrivateMessage(self, from_nick: str, to_nick: str, message: str) -> bool:
        self._emit("private_message", from_nick=from_nick,
                   to_nick=to_nick, message=message)
        return True
    
    def OnSearch(self, nick: str, query: str) -> bool:
        self._emit("search", nick=nick, query=query)
        return True
    
    def OnTimer(self, timestamp: int):
        self._emit("timer", timestamp=timestamp)
    
    def OnHubStarted(self):
        self._emit("hub_started")
    
    def OnHubStopping(self):
        self._emit("hub_stopping")


class HubBridge:
    """
    Singleton bridge to the C++ hub core.
    
    All methods are thread-safe and can be called from any thread
    (FastAPI request handlers, background tasks, etc.)
    """
    
    _instance: Optional["HubBridge"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        # Only initialize once
        if self._initialized:
            return
        
        self._context: Optional[verlihub_core.HubContext] = None
        self._event_handler: Optional[HubEventHandler] = None
        self._event_bus: Optional["EventBus"] = None
        self._initialized = True
    
    def initialize(self, config_dir: str, event_bus: "EventBus") -> bool:
        """
        Initialize the C++ hub core.
        
        Args:
            config_dir: Path to verlihub configuration directory
            event_bus: Event bus for dispatching hub events
        
        Returns:
            True if initialization succeeded
        """
        if self._context is not None:
            logger.warning("HubBridge already initialized")
            return True
        
        self._event_bus = event_bus
        
        # Create the C++ context
        self._context = verlihub_core.HubContext.Create(config_dir)
        if not self._context:
            logger.error("Failed to create HubContext")
            return False
        
        # Create and register event handler
        self._event_handler = HubEventHandler(event_bus)
        self._context.SetEventHandler(self._event_handler)
        
        # Initialize the core
        if not self._context.Initialize():
            logger.error("Failed to initialize HubContext")
            self._context = None
            return False
        
        logger.info("HubBridge initialized successfully")
        return True
    
    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the asyncio event loop for event dispatching."""
        if self._event_handler:
            self._event_handler.set_event_loop(loop)
    
    def start(self, port: int, listen_ip: str = "0.0.0.0") -> bool:
        """Start the hub server."""
        if not self._context:
            raise RuntimeError("HubBridge not initialized")
        return self._context.Start(port, listen_ip)
    
    def stop(self):
        """Stop the hub server."""
        if self._context:
            self._context.Stop()
    
    @property
    def is_running(self) -> bool:
        """Check if hub is running."""
        return self._context is not None and self._context.IsRunning()
    
    # =========================================================================
    # User Operations (thread-safe, delegates to C++ with GIL released)
    # =========================================================================
    
    def get_user_count(self) -> int:
        """Get number of online users."""
        if not self._context:
            return 0
        return self._context.GetUserCount()
    
    def get_user_list(self) -> List[str]:
        """Get list of online user nicknames."""
        if not self._context:
            return []
        return list(self._context.GetUserNicks())
    
    def kick_user(self, op: str, nick: str, reason: str) -> bool:
        """Kick a user from the hub."""
        if not self._context:
            return False
        return self._context.KickUser(op, nick, reason)
    
    # =========================================================================
    # Messaging (thread-safe)
    # =========================================================================
    
    def send_to_user(self, nick: str, message: str) -> bool:
        """Send a message to a specific user."""
        if not self._context:
            return False
        return self._context.SendToUser(nick, message)
    
    def send_to_all(self, message: str) -> bool:
        """Broadcast a message to all users."""
        if not self._context:
            return False
        return self._context.SendToAll(message)
    
    def send_to_class(self, message: str, min_class: int, max_class: int) -> bool:
        """Send a message to users in a class range."""
        if not self._context:
            return False
        return self._context.SendToClass(message, min_class, max_class)
    
    def send_to_opchat(self, message: str, from_nick: str = "") -> bool:
        """Send a message to operator chat."""
        if not self._context:
            return False
        return self._context.SendToOpChat(message, from_nick)
    
    # =========================================================================
    # Bot/Robot Management (thread-safe)
    # =========================================================================
    
    def add_robot(self, nick: str, description: str, user_class: int) -> bool:
        """Add a bot/robot to the hub."""
        if not self._context:
            return False
        return self._context.AddRobot(nick, description, user_class)
    
    def remove_robot(self, nick: str) -> bool:
        """Remove a bot/robot from the hub."""
        if not self._context:
            return False
        return self._context.RemoveRobot(nick)
    
    # =========================================================================
    # Hub Information (thread-safe)
    # =========================================================================
    
    def get_hub_name(self) -> str:
        """Get hub name."""
        if not self._context:
            return ""
        return self._context.GetHubName()
    
    def get_hub_topic(self) -> str:
        """Get hub topic."""
        if not self._context:
            return ""
        return self._context.GetHubTopic()
    
    def get_total_share(self) -> int:
        """Get total share size in bytes."""
        if not self._context:
            return 0
        return self._context.GetTotalShare()
    
    def get_hub_encoding(self) -> str:
        """Get hub character encoding."""
        if not self._context:
            return "UTF-8"
        return self._context.GetHubEncoding()
    
    # =========================================================================
    # Configuration (thread-safe)
    # =========================================================================
    
    def get_config(self, section: str, key: str, default: str = "") -> str:
        """Get a configuration value."""
        if not self._context:
            return default
        return self._context.GetConfig(section, key, default)
    
    def set_config(self, section: str, key: str, value: str) -> bool:
        """Set a configuration value."""
        if not self._context:
            return False
        return self._context.SetConfig(section, key, value)


# Global singleton instance
hub = HubBridge()
```

### 6.2 FastAPI Main Application

```python
# verlihub_py/main.py
"""
Verlihub FastAPI Application

Main entry point for the Python web application that wraps the C++ hub core.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import asyncio
import logging

from verlihub_py.config import settings
from verlihub_py.api.v1 import hub, users, bans, config, plugins, websocket
from verlihub_py.core.hub_bridge import hub as hub_bridge
from verlihub_py.core.events import event_bus
from verlihub_py.db.session import init_db, close_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle management.
    
    - Startup: Initialize database, C++ core, start hub
    - Shutdown: Stop hub, cleanup resources
    """
    logger.info("Starting Verlihub application...")
    
    # Initialize database (SQLModel/SQLAlchemy)
    await init_db()
    
    # Initialize C++ hub core via SWIG bridge
    if not hub_bridge.initialize(settings.config_dir, event_bus):
        raise RuntimeError("Failed to initialize hub core")
    
    # Set event loop for async event dispatching
    loop = asyncio.get_running_loop()
    hub_bridge.set_event_loop(loop)
    
    # Auto-start hub if configured
    if settings.auto_start_hub:
        if hub_bridge.start(settings.hub_port, settings.hub_listen_ip):
            logger.info(f"Hub started on {settings.hub_listen_ip}:{settings.hub_port}")
        else:
            logger.error("Failed to start hub")
    
    yield  # Application runs here
    
    # Shutdown
    logger.info("Shutting down Verlihub...")
    hub_bridge.stop()
    await close_db()
    logger.info("Shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="Verlihub API",
    description="REST API and Dashboard for Verlihub DC++ Hub",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers (v1)
app.include_router(hub.router, prefix="/api/v1/hub", tags=["Hub"])
app.include_router(users.router, prefix="/api/v1/users", tags=["Users"])
app.include_router(bans.router, prefix="/api/v1/bans", tags=["Bans"])
app.include_router(config.router, prefix="/api/v1/config", tags=["Configuration"])
app.include_router(plugins.router, prefix="/api/v1/plugins", tags=["Plugins"])
app.include_router(websocket.router, prefix="/api/v1/ws", tags=["WebSocket"])

# Serve dashboard SPA (if static files exist)
try:
    app.mount("/", StaticFiles(directory="verlihub_py/dashboard/static", html=True))
except RuntimeError:
    logger.warning("Dashboard static files not found")

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "hub_running": hub_bridge.is_running,
        "user_count": hub_bridge.get_user_count() if hub_bridge.is_running else 0,
    }
```

### 6.3 Full HubContext API Exposure

The REST API exposes **all HubContext operations** to the dashboard with permission-based access control.

#### Permission Levels

| Level | Class | Description | Typical Operations |
|-------|-------|-------------|-------------------|
| 0 | Guest | Read-only public info | Hub stats, MOTD |
| 1 | Registered | Basic user operations | Own profile, search |
| 3 | VIP | Extended read access | User list, share stats |
| 4 | Operator | User management | Kick, temp-ban, chat moderation |
| 5 | Cheef | Extended moderation | Longer bans, user registration |
| 10 | Admin | Full admin access | All operations, config changes |
| 11 | Master | Root access | Server control, plugin management |

#### Complete API Endpoint Matrix

| Endpoint | Method | Permission | HubContext Method | Description |
|----------|--------|------------|-------------------|-------------|
| **Hub Lifecycle** |
| `/api/v1/hub/start` | POST | Master (11) | `Start()` | Start the hub server |
| `/api/v1/hub/stop` | POST | Master (11) | `Stop()` | Stop the hub server |
| `/api/v1/hub/restart` | POST | Master (11) | `Stop()` + `Start()` | Restart hub |
| `/api/v1/hub/reload` | POST | Admin (10) | `SignalReload()` | Reload configuration |
| `/api/v1/hub/status` | GET | Guest (0) | `IsRunning()` | Hub running status |
| **Hub Information** |
| `/api/v1/hub/info` | GET | Guest (0) | Multiple | Hub name, topic, encoding |
| `/api/v1/hub/stats` | GET | Guest (0) | `GetUserCount()`, `GetTotalShare()` | Basic statistics |
| `/api/v1/hub/stats/detailed` | GET | VIP (3) | Multiple | Detailed stats with graphs |
| `/api/v1/hub/motd` | GET | Guest (0) | `GetConfig("hub", "motd")` | Message of the day |
| `/api/v1/hub/motd` | PUT | Admin (10) | `SetConfig("hub", "motd")` | Update MOTD |
| **User Management (Online)** |
| `/api/v1/users/online` | GET | VIP (3) | `GetUserNicks()` | List online users |
| `/api/v1/users/online/{nick}` | GET | Operator (4) | `GetUserInfo()` | User details (IP, share, etc.) |
| `/api/v1/users/online/count` | GET | Guest (0) | `GetUserCount()` | Online user count |
| `/api/v1/users/{nick}/kick` | POST | Operator (4) | `KickUser()` | Kick user |
| `/api/v1/users/{nick}/drop` | POST | Operator (4) | `DropUser()` | Drop connection |
| `/api/v1/users/{nick}/redirect` | POST | Operator (4) | `RedirectUser()` | Redirect to another hub |
| `/api/v1/users/{nick}/forceclass` | POST | Cheef (5) | `SetUserClass()` | Temporarily change class |
| **User Management (Registered)** |
| `/api/v1/users/registered` | GET | Operator (4) | Database | List registered users |
| `/api/v1/users/registered` | POST | Cheef (5) | Database | Register new user |
| `/api/v1/users/registered/{nick}` | GET | Operator (4) | Database | Get registration info |
| `/api/v1/users/registered/{nick}` | PUT | Cheef (5) | Database | Update registration |
| `/api/v1/users/registered/{nick}` | DELETE | Admin (10) | Database | Delete registration |
| `/api/v1/users/registered/{nick}/class` | PUT | Admin (10) | Database | Change user class |
| **Messaging** |
| `/api/v1/messages/broadcast` | POST | Operator (4) | `SendToAll()` | Broadcast to all |
| `/api/v1/messages/class` | POST | Operator (4) | `SendToClass()` | Send to class range |
| `/api/v1/messages/user/{nick}` | POST | Operator (4) | `SendToUser()` | PM to specific user |
| `/api/v1/messages/opchat` | POST | Operator (4) | `SendToOpChat()` | OpChat message |
| `/api/v1/messages/mainchat` | POST | Operator (4) | `SendPMToAll()` (as bot) | Main chat as bot |
| **Ban Management** |
| `/api/v1/bans` | GET | Operator (4) | Database | List active bans |
| `/api/v1/bans` | POST | Operator (4) | `BanUser()` + Database | Create ban |
| `/api/v1/bans/{id}` | GET | Operator (4) | Database | Ban details |
| `/api/v1/bans/{id}` | DELETE | Cheef (5) | `UnbanUser()` + Database | Remove ban |
| `/api/v1/bans/ip/{ip}` | POST | Operator (4) | `BanIP()` | Ban by IP |
| `/api/v1/bans/range/{range}` | POST | Cheef (5) | `BanIPRange()` | Ban IP range |
| `/api/v1/bans/nick/{nick}` | POST | Operator (4) | `BanNick()` | Ban by nick |
| `/api/v1/bans/check` | POST | Operator (4) | `CheckBan()` | Check if IP/nick is banned |
| **Configuration** |
| `/api/v1/config` | GET | Admin (10) | `GetAllConfig()` | All configuration sections |
| `/api/v1/config/{section}` | GET | Admin (10) | `GetConfigSection()` | Section config |
| `/api/v1/config/{section}/{key}` | GET | Admin (10) | `GetConfig()` | Single value |
| `/api/v1/config/{section}/{key}` | PUT | Admin (10) | `SetConfig()` | Update value |
| `/api/v1/config/reload` | POST | Admin (10) | `ReloadConfig()` | Reload from DB |
| **Bot/Robot Management** |
| `/api/v1/bots` | GET | VIP (3) | `GetRobotList()` | List bots |
| `/api/v1/bots` | POST | Admin (10) | `AddRobot()` | Add bot |
| `/api/v1/bots/{nick}` | DELETE | Admin (10) | `RemoveRobot()` | Remove bot |
| `/api/v1/bots/{nick}/say` | POST | Operator (4) | `RobotSay()` | Bot sends message |
| **Plugin Management** |
| `/api/v1/plugins` | GET | Admin (10) | `GetPluginList()` | List plugins |
| `/api/v1/plugins/{name}/load` | POST | Master (11) | `LoadPlugin()` | Load plugin |
| `/api/v1/plugins/{name}/unload` | POST | Master (11) | `UnloadPlugin()` | Unload plugin |
| `/api/v1/plugins/{name}/reload` | POST | Admin (10) | `ReloadPlugin()` | Reload plugin |
| `/api/v1/plugins/{name}/config` | GET | Admin (10) | Plugin-specific | Plugin config |
| `/api/v1/plugins/python/scripts` | GET | Admin (10) | `GetPythonScripts()` | Python scripts |
| `/api/v1/plugins/python/scripts` | POST | Admin (10) | `LoadPythonScript()` | Load script |
| `/api/v1/plugins/lua/scripts` | GET | Admin (10) | `GetLuaScripts()` | Lua scripts |
| **Triggers** |
| `/api/v1/triggers` | GET | Operator (4) | Database | List triggers |
| `/api/v1/triggers` | POST | Admin (10) | Database | Create trigger |
| `/api/v1/triggers/{id}` | GET | Operator (4) | Database | Trigger details |
| `/api/v1/triggers/{id}` | PUT | Admin (10) | Database | Update trigger |
| `/api/v1/triggers/{id}` | DELETE | Admin (10) | Database | Delete trigger |
| **Custom Redirects** |
| `/api/v1/redirects` | GET | Admin (10) | Database | List redirects |
| `/api/v1/redirects` | POST | Admin (10) | Database | Create redirect |
| `/api/v1/redirects/{id}` | DELETE | Admin (10) | Database | Delete redirect |
| **DC Client Rules** |
| `/api/v1/clients` | GET | Operator (4) | Database | Client tag rules |
| `/api/v1/clients` | POST | Admin (10) | Database | Add client rule |
| `/api/v1/clients/{id}` | DELETE | Admin (10) | Database | Remove rule |
| **WebSocket (Real-time)** |
| `/api/v1/ws/events` | WS | VIP (3) | Event stream | Real-time hub events |
| `/api/v1/ws/chat` | WS | Operator (4) | Chat stream | Live chat feed |
| `/api/v1/ws/logs` | WS | Admin (10) | Log stream | Server logs |

#### API Authentication

```python
# verlihub_py/api/deps.py
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlmodel import Session, select

from verlihub_py.config import settings
from verlihub_py.db.session import get_session
from verlihub_py.models.user import RegUser

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> RegUser:
    """Decode JWT and return current user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        nick: str = payload.get("sub")
        if nick is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = session.exec(select(RegUser).where(RegUser.nick == nick)).first()
    if user is None:
        raise credentials_exception
    return user


def require_class(min_class: int):
    """Dependency that requires minimum user class."""
    async def _require_class(user: RegUser = Depends(get_current_user)):
        if user.class_ < min_class:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires class {min_class} or higher",
            )
        return user
    return _require_class


# Convenience dependencies
require_operator = require_class(4)
require_cheef = require_class(5)
require_admin = require_class(10)
require_master = require_class(11)
```

#### Example API Router

```python
# verlihub_py/api/v1/hub.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from verlihub_py.core.hub_bridge import hub
from verlihub_py.api.deps import require_admin, require_master, get_current_user
from verlihub_py.models.user import RegUser

router = APIRouter()


class HubStats(BaseModel):
    hub_name: str
    hub_topic: str
    user_count: int
    total_share: int
    is_running: bool


class HubStartRequest(BaseModel):
    port: int = 4111
    listen_ip: str = "0.0.0.0"


@router.get("/stats", response_model=HubStats)
async def get_hub_stats():
    """Get hub statistics (public)."""
    return HubStats(
        hub_name=hub.get_hub_name(),
        hub_topic=hub.get_hub_topic(),
        user_count=hub.get_user_count(),
        total_share=hub.get_total_share(),
        is_running=hub.is_running,
    )


@router.post("/start")
async def start_hub(
    request: HubStartRequest,
    user: RegUser = Depends(require_master),
):
    """Start the hub server (requires Master class)."""
    if hub.is_running:
        raise HTTPException(status_code=400, detail="Hub already running")
    
    if not hub.start(request.port, request.listen_ip):
        raise HTTPException(status_code=500, detail="Failed to start hub")
    
    return {"status": "started", "port": request.port}


@router.post("/stop")
async def stop_hub(user: RegUser = Depends(require_master)):
    """Stop the hub server (requires Master class)."""
    if not hub.is_running:
        raise HTTPException(status_code=400, detail="Hub not running")
    
    hub.stop()
    return {"status": "stopped"}


@router.post("/reload")
async def reload_config(user: RegUser = Depends(require_admin)):
    """Reload hub configuration (requires Admin class)."""
    # Signal the C++ core to reload
    hub._context.SignalReload()
    return {"status": "reload_signaled"}
```

### 6.4 Remote HubContext Client Module

The `HubClient` provides a Python interface that mirrors the local `HubBridge` but communicates with a remote Verlihub API. This enables:

- Remote administration tools
- Multi-hub management dashboards
- Scripting against remote hubs
- Testing without local hub instance

#### Client Module Design

```python
# verlihub_py/client/__init__.py
"""
Verlihub Remote Client Library

Provides a HubClient class that works like a local HubBridge but 
communicates with a remote Verlihub REST API.

Usage:
    from verlihub_py.client import HubClient
    
    # Connect to remote hub
    client = HubClient("https://myhub.example.com/api/v1")
    client.login("admin", "password")
    
    # Use like local HubBridge
    print(f"Users online: {client.get_user_count()}")
    client.send_to_all("Hello from remote!")
    client.kick_user("admin", "baduser", "Spamming")
"""

from verlihub_py.client.hub_client import HubClient
from verlihub_py.client.async_hub_client import AsyncHubClient

__all__ = ["HubClient", "AsyncHubClient"]
```

```python
# verlihub_py/client/hub_client.py
"""
Synchronous remote HubContext client.

Mirrors the HubBridge interface for seamless local/remote switching.
"""

from typing import Optional, List, Dict, Any
import httpx
from dataclasses import dataclass
from datetime import datetime, timedelta
import threading


@dataclass
class HubClientConfig:
    """Configuration for HubClient connection."""
    base_url: str
    timeout: float = 30.0
    verify_ssl: bool = True
    max_retries: int = 3
    retry_delay: float = 1.0


class HubClientError(Exception):
    """Base exception for HubClient errors."""
    pass


class AuthenticationError(HubClientError):
    """Authentication failed."""
    pass


class PermissionError(HubClientError):
    """Insufficient permissions."""
    pass


class HubClient:
    """
    Remote Verlihub hub client.
    
    Provides the same interface as HubBridge but communicates via REST API.
    All methods are thread-safe.
    
    Example:
        client = HubClient("https://myhub.example.com/api/v1")
        client.login("admin", "password")
        
        # Now use exactly like local HubBridge
        users = client.get_user_list()
        client.kick_user("admin", "spammer", "Flooding")
        client.send_to_all("Server maintenance in 5 minutes")
    """
    
    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ):
        """
        Initialize HubClient.
        
        Args:
            base_url: API base URL (e.g., "https://myhub.com/api/v1")
            timeout: Request timeout in seconds
            verify_ssl: Whether to verify SSL certificates
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None
        self._user_class: int = 0
        self._lock = threading.Lock()
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            verify=verify_ssl,
        )
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def close(self):
        """Close the HTTP client."""
        self._client.close()
    
    # =========================================================================
    # Authentication
    # =========================================================================
    
    def login(self, username: str, password: str) -> bool:
        """
        Authenticate with the hub API.
        
        Args:
            username: Hub username (nick)
            password: User password
        
        Returns:
            True if login succeeded
        
        Raises:
            AuthenticationError: If credentials are invalid
        """
        try:
            response = self._client.post(
                "/auth/token",
                data={"username": username, "password": password},
            )
            response.raise_for_status()
            data = response.json()
            
            with self._lock:
                self._token = data["access_token"]
                # Parse token expiry (default 24h if not specified)
                expires_in = data.get("expires_in", 86400)
                self._token_expires = datetime.utcnow() + timedelta(seconds=expires_in)
                self._user_class = data.get("user_class", 0)
            
            return True
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise AuthenticationError("Invalid credentials")
            raise HubClientError(f"Login failed: {e}")
    
    def logout(self):
        """Clear authentication state."""
        with self._lock:
            self._token = None
            self._token_expires = None
            self._user_class = 0
    
    @property
    def is_authenticated(self) -> bool:
        """Check if client is authenticated with valid token."""
        with self._lock:
            if not self._token:
                return False
            if self._token_expires and datetime.utcnow() > self._token_expires:
                return False
            return True
    
    @property
    def user_class(self) -> int:
        """Get current user's class level."""
        with self._lock:
            return self._user_class
    
    def _headers(self) -> Dict[str, str]:
        """Get authorization headers."""
        with self._lock:
            if self._token:
                return {"Authorization": f"Bearer {self._token}"}
            return {}
    
    def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs,
    ) -> Any:
        """Make authenticated API request."""
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        
        try:
            response = self._client.request(
                method,
                endpoint,
                headers=headers,
                **kwargs,
            )
            response.raise_for_status()
            
            if response.headers.get("content-type", "").startswith("application/json"):
                return response.json()
            return response.text
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise AuthenticationError("Not authenticated or token expired")
            if e.response.status_code == 403:
                raise PermissionError("Insufficient permissions")
            raise HubClientError(f"API error: {e.response.text}")
    
    # =========================================================================
    # Hub Lifecycle (mirrors HubBridge)
    # =========================================================================
    
    def start(self, port: int = 4111, listen_ip: str = "0.0.0.0") -> bool:
        """Start the hub server (requires Master class)."""
        result = self._request("POST", "/hub/start", json={
            "port": port,
            "listen_ip": listen_ip,
        })
        return result.get("status") == "started"
    
    def stop(self) -> bool:
        """Stop the hub server (requires Master class)."""
        result = self._request("POST", "/hub/stop")
        return result.get("status") == "stopped"
    
    def restart(self, port: int = 4111, listen_ip: str = "0.0.0.0") -> bool:
        """Restart the hub server."""
        self.stop()
        return self.start(port, listen_ip)
    
    @property
    def is_running(self) -> bool:
        """Check if hub is running."""
        try:
            result = self._request("GET", "/hub/status")
            return result.get("is_running", False)
        except HubClientError:
            return False
    
    # =========================================================================
    # Hub Information (mirrors HubBridge)
    # =========================================================================
    
    def get_hub_name(self) -> str:
        """Get hub name."""
        result = self._request("GET", "/hub/info")
        return result.get("hub_name", "")
    
    def get_hub_topic(self) -> str:
        """Get hub topic."""
        result = self._request("GET", "/hub/info")
        return result.get("hub_topic", "")
    
    def get_total_share(self) -> int:
        """Get total share size in bytes."""
        result = self._request("GET", "/hub/stats")
        return result.get("total_share", 0)
    
    def get_hub_encoding(self) -> str:
        """Get hub character encoding."""
        result = self._request("GET", "/hub/info")
        return result.get("encoding", "UTF-8")
    
    # =========================================================================
    # User Operations (mirrors HubBridge)
    # =========================================================================
    
    def get_user_count(self) -> int:
        """Get number of online users."""
        result = self._request("GET", "/hub/stats")
        return result.get("user_count", 0)
    
    def get_user_list(self) -> List[str]:
        """Get list of online user nicknames."""
        result = self._request("GET", "/users/online")
        return result.get("users", [])
    
    def get_user_info(self, nick: str) -> Dict[str, Any]:
        """Get detailed info for an online user."""
        return self._request("GET", f"/users/online/{nick}")
    
    def kick_user(self, op: str, nick: str, reason: str) -> bool:
        """Kick a user from the hub."""
        result = self._request("POST", f"/users/{nick}/kick", json={
            "operator": op,
            "reason": reason,
        })
        return result.get("status") == "kicked"
    
    def drop_user(self, nick: str) -> bool:
        """Drop a user's connection."""
        result = self._request("POST", f"/users/{nick}/drop")
        return result.get("status") == "dropped"
    
    def redirect_user(self, nick: str, target_hub: str, reason: str = "") -> bool:
        """Redirect user to another hub."""
        result = self._request("POST", f"/users/{nick}/redirect", json={
            "target": target_hub,
            "reason": reason,
        })
        return result.get("status") == "redirected"
    
    # =========================================================================
    # Messaging (mirrors HubBridge)
    # =========================================================================
    
    def send_to_user(self, nick: str, message: str) -> bool:
        """Send a private message to a user."""
        result = self._request("POST", f"/messages/user/{nick}", json={
            "message": message,
        })
        return result.get("status") == "sent"
    
    def send_to_all(self, message: str) -> bool:
        """Broadcast a message to all users."""
        result = self._request("POST", "/messages/broadcast", json={
            "message": message,
        })
        return result.get("status") == "sent"
    
    def send_to_class(self, message: str, min_class: int, max_class: int) -> bool:
        """Send a message to users in a class range."""
        result = self._request("POST", "/messages/class", json={
            "message": message,
            "min_class": min_class,
            "max_class": max_class,
        })
        return result.get("status") == "sent"
    
    def send_to_opchat(self, message: str, from_nick: str = "") -> bool:
        """Send a message to operator chat."""
        result = self._request("POST", "/messages/opchat", json={
            "message": message,
            "from_nick": from_nick,
        })
        return result.get("status") == "sent"
    
    # =========================================================================
    # Bot Management (mirrors HubBridge)
    # =========================================================================
    
    def add_robot(self, nick: str, description: str, user_class: int) -> bool:
        """Add a bot to the hub."""
        result = self._request("POST", "/bots", json={
            "nick": nick,
            "description": description,
            "user_class": user_class,
        })
        return result.get("status") == "created"
    
    def remove_robot(self, nick: str) -> bool:
        """Remove a bot from the hub."""
        result = self._request("DELETE", f"/bots/{nick}")
        return result.get("status") == "removed"
    
    # =========================================================================
    # Configuration (mirrors HubBridge)
    # =========================================================================
    
    def get_config(self, section: str, key: str, default: str = "") -> str:
        """Get a configuration value."""
        try:
            result = self._request("GET", f"/config/{section}/{key}")
            return result.get("value", default)
        except HubClientError:
            return default
    
    def set_config(self, section: str, key: str, value: str) -> bool:
        """Set a configuration value."""
        result = self._request("PUT", f"/config/{section}/{key}", json={
            "value": value,
        })
        return result.get("status") == "updated"
    
    # =========================================================================
    # Ban Management
    # =========================================================================
    
    def ban_user(
        self,
        nick: str,
        reason: str,
        duration_hours: int = 0,
        ban_ip: bool = True,
    ) -> bool:
        """Ban a user."""
        result = self._request("POST", "/bans", json={
            "nick": nick,
            "reason": reason,
            "duration_hours": duration_hours,
            "ban_ip": ban_ip,
        })
        return result.get("status") == "banned"
    
    def unban(self, ban_id: int) -> bool:
        """Remove a ban by ID."""
        result = self._request("DELETE", f"/bans/{ban_id}")
        return result.get("status") == "removed"
    
    def get_bans(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Get list of active bans."""
        return self._request("GET", "/bans", params={
            "limit": limit,
            "offset": offset,
        })
    
    # =========================================================================
    # Registered Users (Database)
    # =========================================================================
    
    def register_user(
        self,
        nick: str,
        password: str,
        user_class: int = 1,
    ) -> Dict[str, Any]:
        """Register a new user."""
        return self._request("POST", "/users/registered", json={
            "nick": nick,
            "password": password,
            "user_class": user_class,
        })
    
    def get_registered_users(
        self,
        limit: int = 100,
        offset: int = 0,
        class_filter: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get list of registered users."""
        params = {"limit": limit, "offset": offset}
        if class_filter is not None:
            params["class"] = class_filter
        return self._request("GET", "/users/registered", params=params)
    
    def delete_registration(self, nick: str) -> bool:
        """Delete a user registration."""
        result = self._request("DELETE", f"/users/registered/{nick}")
        return result.get("status") == "deleted"
```

```python
# verlihub_py/client/async_hub_client.py
"""
Asynchronous remote HubContext client.

Async version of HubClient for use with asyncio/FastAPI.
"""

from typing import Optional, List, Dict, Any
import httpx
from datetime import datetime, timedelta
import asyncio


class AsyncHubClient:
    """
    Async remote Verlihub hub client.
    
    Same interface as HubClient but with async methods.
    
    Example:
        async with AsyncHubClient("https://myhub.com/api/v1") as client:
            await client.login("admin", "password")
            users = await client.get_user_list()
            await client.send_to_all("Hello!")
    """
    
    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        verify_ssl: bool = True,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None
        self._user_class: int = 0
        self._lock = asyncio.Lock()
        self._client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.aclose()
    
    async def login(self, username: str, password: str) -> bool:
        """Authenticate with the hub API."""
        response = await self._client.post(
            "/auth/token",
            data={"username": username, "password": password},
        )
        response.raise_for_status()
        data = response.json()
        
        async with self._lock:
            self._token = data["access_token"]
            expires_in = data.get("expires_in", 86400)
            self._token_expires = datetime.utcnow() + timedelta(seconds=expires_in)
            self._user_class = data.get("user_class", 0)
        
        return True
    
    async def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        """Make authenticated API request."""
        async with self._lock:
            headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        
        headers.update(kwargs.pop("headers", {}))
        response = await self._client.request(method, endpoint, headers=headers, **kwargs)
        response.raise_for_status()
        
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()
        return response.text
    
    # All methods mirror HubClient but are async
    async def get_user_count(self) -> int:
        result = await self._request("GET", "/hub/stats")
        return result.get("user_count", 0)
    
    async def get_user_list(self) -> List[str]:
        result = await self._request("GET", "/users/online")
        return result.get("users", [])
    
    async def kick_user(self, op: str, nick: str, reason: str) -> bool:
        result = await self._request("POST", f"/users/{nick}/kick", json={
            "operator": op, "reason": reason,
        })
        return result.get("status") == "kicked"
    
    async def send_to_all(self, message: str) -> bool:
        result = await self._request("POST", "/messages/broadcast", json={
            "message": message,
        })
        return result.get("status") == "sent"
    
    async def send_to_user(self, nick: str, message: str) -> bool:
        result = await self._request("POST", f"/messages/user/{nick}", json={
            "message": message,
        })
        return result.get("status") == "sent"
    
    # ... remaining methods follow same async pattern
```

#### Client Usage Examples

```python
# Example 1: Simple administration script
from verlihub_py.client import HubClient

with HubClient("https://myhub.example.com/api/v1") as hub:
    hub.login("admin", "secret_password")
    
    # Check hub status
    print(f"Hub running: {hub.is_running}")
    print(f"Users online: {hub.get_user_count()}")
    
    # Kick a user
    hub.kick_user("admin", "spammer", "Stop flooding the chat")
    
    # Send announcement
    hub.send_to_all("Server maintenance in 5 minutes!")


# Example 2: Multi-hub management
from verlihub_py.client import HubClient

hubs = [
    ("https://hub1.example.com/api/v1", "admin", "pass1"),
    ("https://hub2.example.com/api/v1", "admin", "pass2"),
    ("https://hub3.example.com/api/v1", "admin", "pass3"),
]

for url, user, passwd in hubs:
    with HubClient(url) as hub:
        hub.login(user, passwd)
        hub.send_to_all("Global announcement: New hub network rules!")


# Example 3: Async dashboard backend
import asyncio
from verlihub_py.client import AsyncHubClient

async def aggregate_stats(hub_urls: list[str], credentials: dict):
    """Aggregate stats from multiple hubs."""
    stats = []
    
    async def get_hub_stats(url):
        async with AsyncHubClient(url) as hub:
            await hub.login(credentials["user"], credentials["pass"])
            return {
                "url": url,
                "users": await hub.get_user_count(),
                "running": hub.is_running,
            }
    
    tasks = [get_hub_stats(url) for url in hub_urls]
    return await asyncio.gather(*tasks)


# Example 4: Unified local/remote interface
from verlihub_py.core.hub_bridge import HubBridge
from verlihub_py.client import HubClient
from typing import Protocol


class IHubContext(Protocol):
    """Protocol for hub operations - works with both local and remote."""
    def get_user_count(self) -> int: ...
    def kick_user(self, op: str, nick: str, reason: str) -> bool: ...
    def send_to_all(self, message: str) -> bool: ...


def create_hub_context(url: str = None) -> IHubContext:
    """Factory: returns local HubBridge or remote HubClient."""
    if url:
        return HubClient(url)
    else:
        return HubBridge()  # Singleton for local
```

---

## 7. SQLModel Database Layer

### 7.1 Database Tables to Model

Based on the existing schema (from `wintermute.sql`):

| Table | SQLModel Model | Purpose |
|-------|---------------|---------|
| `reglist` | `RegUser` | Registered users |
| `banlist` | `Ban` | Active bans |
| `unbanlist` | `Unban` | Ban history |
| `kicklist` | `Kick` | Kick history |
| `SetupList` | `ConfigItem` | Hub configuration |
| `pi_plug` | `Plugin` | Plugin registry |
| `file_trigger` | `Trigger` | File triggers |
| `client_list` | `DCClient` | Allowed DC++ clients |
| `conn_types` | `ConnType` | Connection types |
| `custom_redirects` | `Redirect` | Custom redirects |
| `temp_rights` | `TempRights` | Temporary user rights |

### 7.2 Example SQLModel Models

```python
# verlihub_py/models/user.py
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
import hashlib


class RegUserBase(SQLModel):
    """Base registered user fields (shared between DB model and API schemas)."""
    
    nick: str = Field(primary_key=True, max_length=64, index=True)
    user_class: int = Field(default=1, alias="class", ge=-1, le=10)
    class_protect: int = Field(default=0)
    class_hidekick: int = Field(default=0)
    hide_kick: bool = Field(default=False)
    hide_keys: bool = Field(default=False)
    show_keys: bool = Field(default=False)
    hide_share: bool = Field(default=False)
    hide_chat: bool = Field(default=False)
    hide_ctmmsg: bool = Field(default=False)
    pwd_change: bool = Field(default=True)
    enabled: bool = Field(default=True)
    note_op: Optional[str] = Field(default=None, max_length=255)
    note_usr: Optional[str] = Field(default=None, max_length=255)
    auth_ip: Optional[str] = Field(default=None, max_length=15)
    alternate_ip: Optional[str] = Field(default=None, max_length=15)
    fake_ip: Optional[str] = Field(default=None, max_length=15)


class RegUser(RegUserBase, table=True):
    """Registered user database model."""
    
    __tablename__ = "reglist"
    
    # Password fields (not exposed in base)
    pwd_crypt: int = Field(default=2)  # 0=none, 1=crypt, 2=md5
    login_pwd: Optional[str] = Field(default=None, max_length=60)
    
    # Timestamps and counters
    reg_date: Optional[int] = Field(default=None)
    reg_op: Optional[str] = Field(default=None, max_length=64)
    login_last: int = Field(default=0)
    logout_last: int = Field(default=0)
    login_cnt: int = Field(default=0)
    login_ip: Optional[str] = Field(default=None, max_length=15)
    error_last: Optional[int] = Field(default=None)
    error_cnt: int = Field(default=0)
    error_ip: Optional[str] = Field(default=None, max_length=15)
    
    def set_password(self, plain_password: str, method: int = 2):
        """
        Set password with specified encryption method.
        
        Args:
            plain_password: Plain text password
            method: 0=none, 1=crypt (deprecated), 2=md5
        """
        if method == 0:
            self.login_pwd = plain_password
        elif method == 2:
            self.login_pwd = hashlib.md5(plain_password.encode()).hexdigest()
        else:
            raise ValueError(f"Unsupported password method: {method}")
        self.pwd_crypt = method
    
    def verify_password(self, plain_password: str) -> bool:
        """Verify a password against stored hash."""
        if self.pwd_crypt == 0:
            return self.login_pwd == plain_password
        elif self.pwd_crypt == 2:
            return self.login_pwd == hashlib.md5(plain_password.encode()).hexdigest()
        return False


class RegUserCreate(SQLModel):
    """Schema for creating a new user."""
    nick: str = Field(max_length=64)
    password: str = Field(min_length=6)
    user_class: int = Field(default=1, ge=-1, le=10)
    reg_op: Optional[str] = None


class RegUserUpdate(SQLModel):
    """Schema for updating a user."""
    user_class: Optional[int] = None
    enabled: Optional[bool] = None
    note_op: Optional[str] = None
    note_usr: Optional[str] = None
    password: Optional[str] = None  # If provided, will be hashed


class RegUserPublic(RegUserBase):
    """Public user info returned by API (no password fields)."""
    reg_date: Optional[int] = None
    login_last: int = 0
    login_cnt: int = 0
```

### 7.3 Database Session Management

```python
# verlihub_py/db/session.py
from typing import AsyncGenerator
from sqlmodel import SQLModel, create_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy.orm import sessionmaker

from verlihub_py.config import settings

# Create async engine
async_engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

# Session factory
async_session_factory = sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db():
    """Initialize database (create tables if needed)."""
    async with async_engine.begin() as conn:
        # In production, use Alembic migrations instead
        # await conn.run_sync(SQLModel.metadata.create_all)
        pass


async def close_db():
    """Close database connections."""
    await async_engine.dispose()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for database sessions."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

---

## 8. Migration Strategy

### 8.1 Database Access Pattern

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Database Access Flow                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ALL DATABASE ACCESS VIA PYTHON                                        │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                                        │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │  Python (SQLModel/SQLAlchemy)                                  │    │
│  │                                                                 │    │
│  │  WRITES:                                                        │    │
│  │  • User registration/modification                               │    │
│  │  • Ban creation/removal                                         │    │
│  │  • Configuration changes                                        │    │
│  │  • Kick logging                                                 │    │
│  │                                                                 │    │
│  │  READS:                                                         │    │
│  │  • API queries                                                  │    │
│  │  • Dashboard data                                               │    │
│  │  • User authentication (cached in C++)                          │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                               │                                         │
│                               ▼                                         │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │                    MySQL / PostgreSQL                          │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                               ▲                                         │
│                               │ (cache invalidation events)             │
│                               │                                         │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │  C++ Core (read-only in-memory cache)                          │    │
│  │                                                                 │    │
│  │  • Registered user lookup (authentication)                      │    │
│  │  • Ban checking                                                 │    │
│  │  • Configuration values                                         │    │
│  │                                                                 │    │
│  │  Cache is refreshed via Python callbacks when data changes     │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 8.2 Cache Invalidation Protocol

```python
# When Python modifies database, notify C++ to refresh cache

async def create_user(session: AsyncSession, user_data: RegUserCreate) -> RegUser:
    """Create a user and notify C++ core."""
    user = RegUser(**user_data.dict())
    user.set_password(user_data.password)
    user.reg_date = int(time.time())
    
    session.add(user)
    await session.commit()
    
    # Notify C++ core to refresh user cache
    hub_bridge.invalidate_user_cache(user.nick)
    
    return user
```

---

## 9. Implementation Phases

### Phase 1: C++ Core Refactoring (4-6 weeks)

**Goal:** Make C++ core thread-safe with zero globals

| Task | Priority | Effort | Dependencies |
|------|----------|--------|--------------|
| Create `HubContext` class | Critical | 1 week | None |
| Implement thread-safe collections | Critical | 1 week | None |
| Refactor `cServerDC` to use context | Critical | 2 weeks | HubContext |
| Remove all static/global variables | Critical | 1 week | cServerDC refactor |
| Add comprehensive mutex protection | Critical | 1 week | Collections |
| Update plugin system (no statics) | High | 1 week | Context |

**Deliverables:**
- [ ] `HubContext` class with full lifecycle management
- [ ] `ThreadSafeMap` and `ThreadSafeUserCollection`
- [ ] Refactored `cServerDC` without `sCurrentServer`
- [ ] All plugins using context references
- [ ] Unit tests for thread safety

### Phase 2: SWIG Bindings (2-3 weeks)

**Goal:** Create Python bindings for the refactored C++ core

| Task | Priority | Effort | Dependencies |
|------|----------|--------|--------------|
| Write SWIG interface files | Critical | 1 week | Phase 1 |
| Implement director callbacks | Critical | 0.5 weeks | Interface files |
| Build and test module | Critical | 0.5 weeks | Director callbacks |
| Create `HubBridge` Python class | High | 0.5 weeks | SWIG module |
| Integration testing | High | 0.5 weeks | HubBridge |

**Deliverables:**
- [ ] `verlihub_core.i` SWIG interface
- [ ] `_verlihub_core.so` Python extension
- [ ] `hub_bridge.py` with full API
- [ ] Integration tests passing

### Phase 3: Python Application Foundation (2-3 weeks)

**Goal:** Set up FastAPI application and SQLModel layer

| Task | Priority | Effort | Dependencies |
|------|----------|--------|--------------|
| Project setup (Poetry/PDM) | Critical | 0.5 days | None |
| SQLModel models for all tables | Critical | 1 week | None |
| Database session management | Critical | 2 days | Models |
| Alembic migration baseline | High | 2 days | Models |
| FastAPI app skeleton | Critical | 2 days | None |
| Configuration management | High | 1 day | FastAPI |

**Deliverables:**
- [ ] `pyproject.toml` with dependencies
- [ ] All SQLModel models matching schema
- [ ] Alembic migrations working
- [ ] FastAPI app starting

### Phase 4: API Implementation (2-3 weeks)

**Goal:** Implement REST API endpoints with full HubContext exposure

| Task | Priority | Effort | Dependencies |
|------|----------|--------|--------------|
| Authentication (JWT) | Critical | 2 days | User API |
| Permission middleware | Critical | 1 day | Authentication |
| Hub lifecycle API | Critical | 1 day | Phase 2, 3 |
| Hub info/stats API | High | 1 day | Phase 2, 3 |
| User management API (online) | Critical | 2 days | Phase 3 |
| User management API (registered) | Critical | 2 days | Phase 3 |
| Messaging API (broadcast/PM) | High | 1 day | Phase 2 |
| Ban management API | Critical | 2 days | Phase 3 |
| Bot/Robot API | Medium | 1 day | Phase 2 |
| Configuration API | High | 2 days | Phase 3 |
| Plugin management API | Medium | 2 days | Phase 3 |
| Trigger/Redirect API | Medium | 1 day | Phase 3 |
| WebSocket events | High | 3 days | Phase 2, 3 |

**Deliverables:**
- [ ] Full REST API with OpenAPI docs (50+ endpoints)
- [ ] JWT authentication with class-based permissions
- [ ] WebSocket real-time events (chat, user events, logs)
- [ ] API tests passing with >90% coverage

### Phase 5: Dashboard (2-3 weeks) ✓ COMPLETE

**Goal:** Web dashboard for hub administration

**Status:** IMPLEMENTED - `verlihub.dashboard` module

**UI Framework:** Bulma CSS (https://bulma.io/)
- Modern, responsive CSS framework
- No JavaScript dependencies (pure CSS)
- Mobile-first design
- Clean, professional appearance
- Easy customization via Sass variables

| Task | Priority | Effort | Dependencies | Status |
|------|----------|--------|--------------|--------|
| Dashboard framework setup (Bulma) | High | 2 days | None | ✓ Done |
| Authentication UI | Critical | 2 days | Phase 4 auth | ✓ Done |
| User management UI | High | 3 days | Phase 4 API | ✓ Done |
| Real-time user list | High | 2 days | WebSocket | ✓ Done |
| Configuration editor | Medium | 3 days | Config API | ✓ Done |
| Ban management UI | Medium | 2 days | Ban API | ✓ Done |
| Statistics/monitoring | Low | 2 days | Stats API | ✓ Done |

**Dashboard Components (using Bulma):**
- `navbar` - Main navigation with login/logout
- `hero` - Hub status banner
- `card` - User info, ban cards, config sections
- `table` - User lists, ban lists, registrations
- `modal` - Confirmation dialogs, edit forms
- `notification` - Real-time alerts
- `tabs` - Section navigation
- `form` - Configuration forms, user registration

**Implemented Pages:**
- Login page with error handling
- Main dashboard with hub stats and quick actions
- User management with online/registered tabs
- Ban management with search and CRUD
- Configuration editor with tabbed sections
- Logs viewer with real-time WebSocket streaming

**WebSocket Endpoints:**
- `/ws/hub` - Real-time hub events (user join/leave, chat)
- `/ws/logs` - Real-time log streaming

**Deliverables:**
- [x] Working web dashboard with Bulma CSS
- [x] Real-time updates via WebSocket
- [x] Mobile-responsive design
- [x] Dark/light theme support (user preference saved via localStorage)

### Phase 5b: Remote Client Library (1 week) ✓ COMPLETE

**Goal:** Python client library for remote hub management

**Status:** IMPLEMENTED - `verlihub.client` module

**Implemented Clients:**
- `NMDCClient` - Direct NMDC protocol connection for integration testing and bots
- `HubClient` - Synchronous REST API client for remote management
- `AsyncHubClient` - Asynchronous REST API client for async applications

| Task | Priority | Effort | Dependencies | Status |
|------|----------|--------|--------------|--------|
| NMDCClient (protocol) | High | 2 days | None | ✓ Done |
| HubClient (sync) implementation | High | 2 days | Phase 4 API | ✓ Done |
| AsyncHubClient implementation | High | 1 day | HubClient | ✓ Done |
| IHubContext protocol/interface | Medium | 0.5 days | HubClient | ✓ Done |
| Client authentication flow | High | 0.5 days | Phase 4 auth | ✓ Done |
| Client documentation | Medium | 0.5 days | All above | ✓ Done |
| Client unit tests | High | 1 day | All above | ✓ Done |
| PyPI package setup | Low | 0.5 days | All above | Pending |

**Deliverables:**
- [x] `verlihub.client` module with NMDC and REST API clients
- [x] 100% API parity with local HubBridge interface
- [x] Comprehensive client documentation with examples
- [x] Client unit tests
- [ ] Optional: Standalone PyPI package for remote administration

### Phase 6: Integration & Testing (2-3 weeks)

**Goal:** Full integration testing and deployment preparation

| Task | Priority | Effort | Dependencies |
|------|----------|--------|--------------|
| Integration test suite | Critical | 1 week | All phases |
| Performance testing | High | 3 days | Integration |
| Documentation | High | 3 days | All phases |
| Docker Compose setup | High | 2 days | All phases |
| Migration guide | High | 2 days | Documentation |
| Security audit | Critical | 3 days | All phases |

**Deliverables:**
- [ ] Full test coverage
- [ ] Performance benchmarks
- [ ] Production Docker setup
- [ ] Migration documentation

---

## 10. Testing Strategy

### 10.1 C++ Unit Tests (Google Test)

```cpp
// tests/cpp/test_hub_context.cpp
#include <gtest/gtest.h>
#include "core/hub_context.h"
#include <thread>
#include <vector>

class HubContextTest : public ::testing::Test {
protected:
    void SetUp() override {
        context = nVerliHub::HubContext::Create("/tmp/test_config");
        ASSERT_NE(context, nullptr);
        ASSERT_TRUE(context->Initialize());
    }
    
    void TearDown() override {
        if (context) {
            context->Stop();
        }
    }
    
    std::unique_ptr<nVerliHub::HubContext> context;
};

TEST_F(HubContextTest, GetUserCountInitiallyZero) {
    EXPECT_EQ(context->GetUserCount(), 0);
}

TEST_F(HubContextTest, ConcurrentConfigAccess) {
    const int NUM_THREADS = 10;
    const int ITERATIONS = 1000;
    std::vector<std::thread> threads;
    
    // Writers
    for (int t = 0; t < NUM_THREADS; t++) {
        threads.emplace_back([this, t]() {
            for (int i = 0; i < ITERATIONS; i++) {
                context->SetConfig("test", "key" + std::to_string(t),
                                   "value" + std::to_string(i));
            }
        });
    }
    
    // Readers
    for (int t = 0; t < NUM_THREADS; t++) {
        threads.emplace_back([this, t]() {
            for (int i = 0; i < ITERATIONS; i++) {
                auto val = context->GetConfig("test", "key" + std::to_string(t));
                // Value should be consistent (not corrupted)
                EXPECT_TRUE(val.empty() || val.find("value") == 0);
            }
        });
    }
    
    for (auto& t : threads) t.join();
}

TEST_F(HubContextTest, NoGlobalStateLeaks) {
    // Destroy context
    context->Stop();
    context.reset();
    
    // Create new context - should be completely independent
    auto context2 = nVerliHub::HubContext::Create("/tmp/test_config2");
    ASSERT_NE(context2, nullptr);
    EXPECT_TRUE(context2->Initialize());
    
    // No state from first context should leak to second
    EXPECT_EQ(context2->GetUserCount(), 0);
}
```

### 10.2 Python API Tests (pytest)

```python
# tests/test_api/test_users.py
import pytest
from httpx import AsyncClient
from sqlmodel import Session

from verlihub_py.main import app
from verlihub_py.models.user import RegUser


@pytest.mark.asyncio
async def test_list_users(client: AsyncClient, sample_users: list[RegUser]):
    """Test listing registered users."""
    response = await client.get("/api/v1/users/")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == len(sample_users)


@pytest.mark.asyncio
async def test_create_user(client: AsyncClient, db_session: Session):
    """Test user registration."""
    response = await client.post("/api/v1/users/", json={
        "nick": "newuser",
        "password": "password123",
        "user_class": 1,
    })
    assert response.status_code == 201
    data = response.json()
    assert data["nick"] == "newuser"
    assert "login_pwd" not in data  # Password should not be exposed


@pytest.mark.asyncio
async def test_kick_online_user(client: AsyncClient, hub_with_users):
    """Test kicking an online user."""
    response = await client.post("/api/v1/users/testuser/kick", params={
        "reason": "test kick",
        "operator": "admin",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "kicked"
```

### 10.3 Integration Tests

```python
# tests/test_integration/test_full_flow.py
import pytest
import asyncio

from verlihub_py.core.hub_bridge import HubBridge
from verlihub_py.core.events import EventBus


@pytest.mark.integration
async def test_hub_lifecycle():
    """Test full hub startup/shutdown cycle."""
    event_bus = EventBus()
    hub = HubBridge()
    
    # Initialize
    assert hub.initialize("/tmp/test_verlihub", event_bus)
    
    # Start
    assert hub.start(4111, "127.0.0.1")
    assert hub.is_running
    
    # Get info
    assert hub.get_user_count() == 0
    assert hub.get_hub_name() != ""
    
    # Stop
    hub.stop()
    assert not hub.is_running


@pytest.mark.integration
async def test_concurrent_api_requests():
    """Test API under concurrent load."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Make 100 concurrent requests
        tasks = [
            client.get("/api/v1/hub/stats")
            for _ in range(100)
        ]
        responses = await asyncio.gather(*tasks)
        
        # All should succeed
        assert all(r.status_code == 200 for r in responses)
```

---

## 10.4 Plugin Architecture and Legacy Compatibility

### Existing Plugin Singleton Patterns

The current plugin architecture uses static singleton patterns for server access:

| Plugin | Singleton Pattern | Server Access |
|--------|-------------------|---------------|
| **C++ Plugins** | `cServerDC::sCurrentServer` | Direct static access |
| **Lua Plugin** | `cServerDC::sCurrentServer` + `cpiLua::me` | Via `callbacks.cpp` |
| **Perl Plugin** | `cServerDC::sCurrentServer` | Via `callbacks.cpp` |
| **Python Plugin** | `cpiPython::me->server` | Dedicated static wrapper |

These patterns must be preserved during transition to maintain backward compatibility.

### Python Plugin Interpreter Modes

The Python plugin supports two interpreter modes with different data sharing characteristics:

#### Sub-Interpreter Mode (Default, Secure)

```cmake
# Default behavior - no flag needed
# Each script runs in isolated Python sub-interpreter
```

- **Script isolation:** Each Python script has its own globals, modules, and namespace
- **Benefits:** Scripts cannot interfere with each other, secure multi-tenant operation
- **Limitations:** Some Python packages (numpy, FastAPI, asyncio) may not work correctly
- **Thread behavior:** Background threads face restrictions with sub-interpreter GIL
- **Main program data access:** Scripts can read/modify hub data via `vh.*` wrapper API

#### Single Interpreter Mode (Less Secure, More Compatible)

```cmake
option(PYTHON_USE_SINGLE_INTERPRETER 
    "Use single Python interpreter (allows threading but scripts can see each other)" 
    OFF)
```

- **Shared namespace:** ALL scripts share the same `sys.modules`, globals, and Python state
- **Security implications:** Script A can `import` modules loaded by Script B
- **Benefits:** Full threading support, all Python packages work correctly
- **Main program data access:** Scripts can read/modify hub data via `vh.*` wrapper API
- **Inter-script data sharing:** Scripts CAN directly access each other's global variables

**WARNING:** In single interpreter mode, a malicious script could:
- Read/modify global variables from other scripts
- Override functions in shared modules
- Access cached sensitive data from other scripts

### Data Sharing Matrix

| Access Type | Sub-Interpreter | Single Interpreter |
|-------------|-----------------|-------------------|
| Hub data via `vh.*` API | ✓ Yes | ✓ Yes |
| Other script's globals | ✗ No | ⚠ Yes (security concern) |
| Shared Python modules | ✗ Separate copies | ⚠ Same instance |
| C++ static variables | ✓ Via wrapper only | ✓ Via wrapper only |

### Compatibility Strategy for Thin Verlihub

#### Phase 1: Shim Layer (Backward Compatible)

Maintain legacy plugin interfaces by creating a shim that maps old static access to new `HubContext`:

```cpp
// Legacy compatibility shim (TEMPORARY)
namespace nVerliHub {
    // The new architecture - explicit context
    static thread_local HubContext* tl_current_context = nullptr;
    
    // Legacy shim - maps to thread-local context
    // Deprecated but maintained for existing plugins
    cServerDC* GetLegacyServer() {
        return tl_current_context ? 
               tl_current_context->GetServerAdapter() : nullptr;
    }
}

// In plugin API context setup
void SetCurrentContext(HubContext* ctx) {
    tl_current_context = ctx;
}
```

#### Phase 2: Plugin Migration Path

1. **New plugins:** Use `HubContext` passed explicitly
2. **Legacy plugins:** Use shim layer (with deprecation warnings)
3. **Migration tools:** Static analyzer to detect `sCurrentServer` usage

#### Phase 3: Remove Legacy Support

After sufficient migration period:
- Remove shim layer
- Remove `sCurrentServer` completely
- All plugins use explicit context

### Thin Verlihub Python Core Integration

When the Python FastAPI wrapper loads the C++ core via SWIG:

```python
# In the thin verlihub architecture
from verlihub_py.core import _verlihub_core  # SWIG module

class HubBridge:
    def __init__(self):
        # Create explicit context - NO GLOBALS
        self._context = _verlihub_core.HubContext.Create(config_path)
    
    def start(self, port: int, address: str):
        # Pass context explicitly
        self._context.Start(port, address)
```

**Key difference:** The new architecture passes `HubContext` explicitly rather than using global singletons. The legacy Python plugin (for backward compatibility) will receive a `HubContext` reference during initialization rather than accessing `cpiPython::me->server`.

### Test Coverage for Plugin Compatibility

Unit tests must verify:

1. **Legacy shim works:** `GetLegacyServer()` returns valid server adapter
2. **Context isolation:** Multiple `HubContext` instances don't share state
3. **Plugin callbacks:** Legacy plugins receive correct context
4. **Thread safety:** Context access from multiple threads is safe

---

## 11. Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| SWIG complexity | High | Medium | Start with minimal interface, expand incrementally |
| Performance regression | High | Medium | Benchmark critical paths, optimize hot spots |
| Thread safety bugs | Critical | Medium | Extensive testing, static analysis tools (TSan) |
| Database sync issues | Medium | Low | Event-based invalidation, eventual consistency |
| Migration downtime | Medium | Low | Blue-green deployment, rollback plan |
| Plugin compatibility | Medium | High | Maintain legacy plugin interface initially |
| Python single-interp security | Medium | Low | Document risks, recommend sub-interpreter for multi-tenant |

---

## 12. Success Criteria

### Functional Requirements
- [ ] Hub handles 1000+ concurrent users without crashes
- [ ] All existing functionality preserved
- [ ] REST API covers all admin operations
- [ ] Real-time dashboard updates via WebSocket
- [ ] Database operations fully through SQLModel

### Non-Functional Requirements
- [ ] No global/static state in C++ core
- [ ] All public APIs are thread-safe
- [ ] Response time < 100ms for API calls
- [ ] Memory usage stable under load
- [ ] Test coverage > 80%

### Documentation
- [ ] API documentation (OpenAPI)
- [ ] Architecture documentation
- [ ] Migration guide
- [ ] Developer setup guide

---

## Appendix A: Glossary

| Term | Definition |
|------|------------|
| **HubContext** | Central object owning all hub state, replacing globals |
| **SWIG** | Simplified Wrapper and Interface Generator for Python bindings |
| **SQLModel** | Python library combining SQLAlchemy ORM with Pydantic validation |
| **Director** | SWIG feature enabling callbacks from C++ to Python |
| **Thread-safe** | Safe for concurrent access from multiple threads |
| **Lock-free** | Using atomic operations instead of mutexes |

---

## Appendix B: Reference Implementation Schedule

```
Week 1-2:   HubContext implementation + Thread-safe collections
Week 3-4:   cServerDC refactoring + Remove globals
Week 5-6:   Plugin system refactoring + Unit tests
Week 7-8:   SWIG interface + HubBridge
Week 9-10:  Python project setup + SQLModel models
Week 11-12: REST API implementation
Week 13-14: Dashboard implementation
Week 15-16: Integration testing + Documentation
```

Total estimated duration: **16 weeks** (4 months)

---

*Document Version: 1.0.0*
*Last Updated: February 2026*
*Authors: Verlihub Development Team*
