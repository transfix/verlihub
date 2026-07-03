# Full-Featured DC Client for Verlihub Python Package

## Overview

Wrap the eiskaltdcpp core library (`libeiskaltdcpp`) via SWIG to provide a
full-featured NMDC/ADC client importable from Python. This replaces the
current pure-Python `NMDCClient` (which only handles chat/auth) with a client
capable of search, download, upload, file hashing, file list browsing, TLS,
UPnP, segmented transfers, throttling — everything a desktop DC client can do.

## Current State

### Verlihub `NMDCClient` (`verlihub/client/nmdc.py`, 687 lines)

Pure-Python, handles only:
- Lock/Key handshake, password auth, `$MyINFO`
- Public chat, private messages
- `$NickList`, `$OpList`, `$Quit`, user tracking
- Hub command execution

**Cannot do:** search, file transfer, hashing, file lists, share management,
TLS, UPnP, segmented downloads, throttling.

### eiskaltdcpp `dcpp/` → `libeiskaltdcpp.so`

Mature C++ DC client core (~60 source files), builds as a shared library on
Linux. Full feature set: NMDC + ADC protocols, search, segmented downloads,
uploads, file hashing (TTH), TLS, UPnP, throttling, IP filtering, Lua
scripting.

### eiskaltdcpp Daemon (`eiskaltdcpp-daemon/`)

Headless consumer of `libeiskaltdcpp`. Its `ServerThread` class is the
reference implementation for using the core library without a GUI. Exposes
50+ operations via JSONRPC. This is our blueprint.

## Architecture

```
verlihub/python/
├── verlihub/
│   ├── client/
│   │   ├── __init__.py          # Re-exports DCClient alongside NMDCClient
│   │   ├── nmdc.py              # Existing basic client (kept for simple use)
│   │   ├── api.py               # Existing REST API client
│   │   └── dc_client.py         # NEW: Pythonic wrapper around _dc_core
│   └── ...
├── dc_core/                     # NEW: SWIG-based C++ extension
│   ├── CMakeLists.txt           # Build system for the SWIG module
│   ├── dc_core.i                # Master SWIG interface file
│   ├── bridge.h                 # C++ bridge class header
│   ├── bridge.cpp               # C++ bridge class implementation
│   ├── callbacks.h              # Callback dispatcher (C++ → Python)
│   ├── callbacks.cpp            # Callback dispatcher implementation
│   ├── typemaps.i               # SWIG typemaps for STL/custom types
│   └── enums.i                  # SWIG enum wrappers
└── pyproject.toml               # Updated with scikit-build-core
```

### Why SWIG

- Verlihub already uses SWIG for its C++ core → Python bridge (`verlihub/core.py`),
  so the project has established SWIG patterns and developer familiarity.
- SWIG generates the wrapper code automatically from `.i` interface files and
  C++ headers — less hand-written glue code than pybind11.
- SWIG-generated modules integrate naturally with the existing build system
  (CMake + setuptools).
- SWIG director classes provide a clean mechanism for routing C++ virtual
  callback methods into Python override methods.

## eiskaltdcpp Core Library Reference

### Singleton Managers

All managers are singletons accessed via `ManagerClass::getInstance()`.
Initialization order (from `dcpp::startup()`):

1. `Util::initialize()` — paths, config dirs
2. `ResourceManager` → `SettingsManager` → `LogManager` → `TimerManager`
3. `HashManager` → `CryptoManager` → `SearchManager` → `ClientManager`
4. `ConnectionManager` → `DownloadManager` → `UploadManager` → `ThrottleManager`
5. `QueueManager` → `ShareManager` → `FavoriteManager` → `FinishedManager`
6. `ADLSearchManager` → `ConnectivityManager` → `MappingManager` → `DebugManager`

Shutdown (`dcpp::shutdown()`) reverses this order, saving state first.

### Listener/Observer Pattern

Event dispatch uses `Speaker<ListenerType>`:
- `addListener(Listener*)` / `removeListener(Listener*)`
- `fire(EventType, args...)` calls `on(EventType, args...)` on all listeners

Key listener interfaces:

| Listener | Key Events |
|----------|-----------|
| `ClientListener` | `Connected`, `Failed`, `Message`, `StatusMessage`, `UserUpdated`, `UserRemoved`, `GetPassword`, `Redirect`, `NickTaken`, `SearchFlood` |
| `ClientManagerListener` | `UserConnected`, `UserDisconnected`, `ClientConnected`, `ClientDisconnected` |
| `SearchManagerListener` | `SR` (search result received) |
| `DownloadManagerListener` | `Requesting`, `Starting`, `Tick`, `Complete`, `Failed` |
| `UploadManagerListener` | `Starting`, `Tick`, `Complete`, `Failed` |
| `QueueManagerListener` | `Added`, `Finished`, `Removed`, `Moved` |
| `HashManagerListener` | progress events |

### Core Classes

**`Client`** — abstract base for hub connections. Key methods:
- `connect()`, `disconnect()`, `search()`, `hubMessage()`, `privateMessage()`
- `password()`, `info()`, `getUserCount()`, `isConnected()`
- Created via `ClientManager::getClient(url)` (returns `NmdcHub*` or `AdcHub*`)

**`NmdcHub`** — NMDC protocol implementation. Handles `$Lock`/`$Key`,
`$MyINFO`, `$Search`, `$SR`, `$ConnectToMe`/`$RevConnectToMe`, encoding.

**`SearchManager`** — search dispatch + UDP result listener.

**`QueueManager`** — download queue (add, remove, prioritize, persist).

**`DownloadManager`** / **`UploadManager`** — active transfer management.

**`ShareManager`** — shared directory management, file list generation, incoming
search response.

**`HashManager`** — TTH file hashing (background thread).

**`ConnectionManager`** — peer TCP connections (incoming listener + outgoing).

**`SettingsManager`** — all configuration via `DCPlusPlus.xml`.

### Daemon `ServerThread` — Our Blueprint

The daemon's `ServerThread` is the proven headless consumer of the library.
It implements all the listener interfaces above and exposes these operations
(which map 1:1 to our Python API):

```
connectClient(address, encoding)       disconnectClient(address)
sendMessage(hubUrl, message)           sendPrivateMessage(hub, nick, message)
sendSearchOnHubs(search, mode, ...)    returnSearchResults(resultarray, hubUrl)
clearSearchResults(hubUrl)             addInQueue(dir, name, size, tth)
removeQueueItem(target)                moveQueueItem(source, target)
setPriorityQueueItem(target, priority) listQueue() / listQueueTargets()
getFileList(hub, nick, match)          openFileList(filelist)
closeFileList(filelist)                lsDirInList(dir, filelist)
downloadDirFromList(target, to, list)  downloadFileFromList(file, to, list)
addDirInShare(dir, virtname)           delDirFromShare(dir)
renameDirInShare(dir, virtname)        listShare()
refreshShare()                         getHashStatus()
pauseHash()                            settingsGetSet(param, value)
getHubUserList(huburl)                 getUserInfo(nick, huburl)
listConnectedClients()                 listHubsFullDesc()
configReload()                         matchAllList()
```

## SWIG Integration Design

### Master Interface File (`dc_core.i`)

```swig
%module(directors="1") _dc_core

%{
#include "bridge.h"
#include "callbacks.h"
%}

%include "std_string.i"
%include "std_vector.i"
%include "std_map.i"
%include "stdint.i"

%include "typemaps.i"
%include "enums.i"

// Enable director for callback class so Python can override
%feature("director") DCClientCallback;

%include "callbacks.h"
%include "bridge.h"
```

### Callback Dispatch via SWIG Directors

SWIG directors allow C++ virtual methods to be overridden in Python.
We define an abstract callback class in C++:

```cpp
// callbacks.h
class DCClientCallback {
public:
    virtual ~DCClientCallback() {}

    // Hub events
    virtual void onHubConnected(const std::string& hubUrl, const std::string& hubName) {}
    virtual void onHubDisconnected(const std::string& hubUrl, const std::string& reason) {}
    virtual void onHubRedirect(const std::string& hubUrl, const std::string& newUrl) {}
    virtual void onHubPasswordRequest(const std::string& hubUrl) {}

    // Chat
    virtual void onChatMessage(const std::string& hubUrl, const std::string& nick,
                               const std::string& message, bool thirdPerson) {}
    virtual void onPrivateMessage(const std::string& hubUrl, const std::string& fromNick,
                                  const std::string& toNick, const std::string& message) {}
    virtual void onStatusMessage(const std::string& hubUrl, const std::string& message) {}

    // Users
    virtual void onUserConnected(const std::string& hubUrl, const std::string& nick) {}
    virtual void onUserDisconnected(const std::string& hubUrl, const std::string& nick) {}
    virtual void onUserUpdated(const std::string& hubUrl, const std::string& nick) {}

    // Search
    virtual void onSearchResult(const std::string& hubUrl, const std::string& file,
                                int64_t size, int freeSlots, int totalSlots,
                                const std::string& tth, const std::string& nick) {}

    // Transfers
    virtual void onDownloadStarting(const std::string& target, const std::string& nick,
                                    int64_t size) {}
    virtual void onDownloadComplete(const std::string& target, const std::string& nick,
                                    int64_t size, int64_t speed) {}
    virtual void onDownloadFailed(const std::string& target, const std::string& reason) {}
    virtual void onUploadStarting(const std::string& file, const std::string& nick,
                                  int64_t size) {}
    virtual void onUploadComplete(const std::string& file, const std::string& nick,
                                  int64_t size) {}

    // Queue
    virtual void onQueueItemAdded(const std::string& target, int64_t size,
                                  const std::string& tth) {}
    virtual void onQueueItemFinished(const std::string& target, int64_t size) {}
    virtual void onQueueItemRemoved(const std::string& target) {}

    // Hashing
    virtual void onHashProgress(const std::string& currentFile,
                                uint64_t bytesLeft, size_t filesLeft) {}
};
```

Python usage with director:

```python
from verlihub._dc_core import DCBridge, DCClientCallback

class MyHandler(DCClientCallback):
    def onChatMessage(self, hub_url, nick, message, third_person):
        print(f"<{nick}> {message}")

    def onSearchResult(self, hub_url, file, size, free_slots, total_slots, tth, nick):
        print(f"Found: {file} ({size} bytes) from {nick}")

    def onDownloadComplete(self, target, nick, size, speed):
        print(f"Downloaded: {target} at {speed} B/s")

handler = MyHandler()
bridge = DCBridge()
bridge.initialize("~/.verlihub/dcpp")
bridge.set_callback(handler)
bridge.connect_hub("dchub://example.com:411")
```

### C++ Bridge Class (`DCBridge`)

Mirrors `ServerThread` from the daemon. Implements all C++ listener interfaces
and forwards events to the `DCClientCallback` director:

```cpp
// bridge.h
#include <string>
#include <vector>
#include <map>
#include <cstdint>

// Forward declare — actual includes in bridge.cpp
class DCClientCallback;

struct HubInfo {
    std::string url;
    std::string name;
    std::string description;
    int userCount;
    int64_t sharedBytes;
    bool connected;
    bool isOp;
};

struct UserInfo {
    std::string nick;
    std::string description;
    std::string connection;
    std::string email;
    std::string cid;
    int64_t shareSize;
    bool isOp;
    bool isBot;
};

struct SearchResultInfo {
    std::string file;
    int64_t size;
    std::string tth;
    std::string nick;
    std::string hubUrl;
    std::string hubName;
    int freeSlots;
    int totalSlots;
    bool isDirectory;
};

struct QueueItemInfo {
    std::string target;
    std::string filename;
    int64_t size;
    int64_t downloadedBytes;
    std::string tth;
    int priority;        // 0=paused, 1=lowest .. 5=highest
    int sources;
    int onlineSources;
    int status;          // 0=queued, 1=running, 2=finished
};

struct TransferInfo {
    std::string filename;
    std::string nick;
    std::string hubUrl;
    int64_t size;
    int64_t pos;
    int64_t speed;       // bytes/sec
    bool isDownload;
};

struct ShareDirInfo {
    std::string realPath;
    std::string virtualName;
    int64_t size;
};

struct HashStatus {
    std::string currentFile;
    uint64_t bytesLeft;
    size_t filesLeft;
    bool paused;
};

struct FileListEntry {
    std::string name;
    int64_t size;
    std::string tth;       // empty for directories
    bool isDirectory;
};

struct TransferStats {
    int64_t downloadSpeed;   // bytes/sec
    int64_t uploadSpeed;     // bytes/sec
    int64_t totalDownloaded; // lifetime bytes
    int64_t totalUploaded;   // lifetime bytes
    int downloadCount;
    int uploadCount;
};

class DCBridge {
public:
    DCBridge();
    ~DCBridge();

    // =========================================================================
    // Lifecycle
    // =========================================================================

    /// Initialize the DC core library.
    /// @param configDir  Directory for DCPlusPlus.xml, certs, hash DB, etc.
    ///                   Defaults to ~/.verlihub/dcpp/
    void initialize(const std::string& configDir = "");

    /// Shut down cleanly — saves queue, settings, disconnects all hubs.
    void shutdown();

    /// Whether initialize() has been called successfully.
    bool isInitialized() const;

    // =========================================================================
    // Callbacks
    // =========================================================================

    /// Set the callback handler (Python subclass of DCClientCallback).
    /// NULL disables callbacks. Caller retains ownership.
    void setCallback(DCClientCallback* cb);

    // =========================================================================
    // Hub Connections
    // =========================================================================

    /// Connect to a hub.
    /// @param url       Hub URL, e.g. "dchub://example.com:411"
    /// @param encoding  Character encoding, e.g. "CP1252". Empty = UTF-8.
    void connectHub(const std::string& url, const std::string& encoding = "");

    /// Disconnect from a hub.
    void disconnectHub(const std::string& url);

    /// List all connected (and connecting) hubs.
    std::vector<HubInfo> listHubs();

    /// Check if connected to a specific hub.
    bool isConnected(const std::string& hubUrl);

    // =========================================================================
    // Chat
    // =========================================================================

    /// Send a public chat message to a hub.
    void sendMessage(const std::string& hubUrl, const std::string& message);

    /// Send a private message to a user on a hub.
    void sendPM(const std::string& hubUrl, const std::string& nick,
                const std::string& message);

    /// Get chat history for a hub (up to maxLines, 0 = all buffered).
    std::vector<std::string> getChatHistory(const std::string& hubUrl,
                                            int maxLines = 50);

    // =========================================================================
    // Users
    // =========================================================================

    /// Get user list for a hub.
    std::vector<UserInfo> getHubUsers(const std::string& hubUrl);

    /// Get detailed info for a specific user on a hub.
    UserInfo getUserInfo(const std::string& nick, const std::string& hubUrl);

    // =========================================================================
    // Search
    // =========================================================================

    /// Send a search to all connected hubs (or a specific hub).
    /// @param query     Search string (or TTH hash for TTH search)
    /// @param fileType  0=any, 1=audio, 2=compressed, 3=document,
    ///                  4=executable, 5=picture, 6=video, 7=directory, 8=TTH
    /// @param sizeMode  0=don't care, 1=at least, 2=at most
    /// @param size      Size in bytes (0 = don't care)
    /// @param hubUrl    If non-empty, search only this hub
    /// @return token string for matching results
    std::string search(const std::string& query, int fileType = 0,
                       int sizeMode = 0, int64_t size = 0,
                       const std::string& hubUrl = "");

    /// Get accumulated search results for a hub.
    std::vector<SearchResultInfo> getSearchResults(const std::string& hubUrl);

    /// Clear search results for a hub.
    void clearSearchResults(const std::string& hubUrl);

    // =========================================================================
    // Download Queue
    // =========================================================================

    /// Add a file to the download queue.
    /// @param target    Local download path
    /// @param name      Display name
    /// @param size      File size in bytes
    /// @param tth       Tiger Tree Hash
    /// @return true on success
    bool addToQueue(const std::string& target, const std::string& name,
                    int64_t size, const std::string& tth);

    /// Add a magnet link to the download queue.
    bool addMagnet(const std::string& magnetLink, const std::string& downloadDir);

    /// Remove an item from the queue by target path.
    void removeFromQueue(const std::string& target);

    /// Move a queued item's target path.
    void moveQueueItem(const std::string& source, const std::string& target);

    /// Set download priority (0=paused .. 5=highest).
    void setPriority(const std::string& target, int priority);

    /// List all items in the download queue.
    std::vector<QueueItemInfo> listQueue();

    /// Clear entire download queue.
    void clearQueue();

    /// Match all downloaded file lists against the queue.
    void matchAllLists();

    // =========================================================================
    // File Lists
    // =========================================================================

    /// Request a file list from a user on a hub.
    bool requestFileList(const std::string& hubUrl, const std::string& nick,
                         bool matchQueue = false);

    /// List locally available file lists.
    std::vector<std::string> listLocalFileLists();

    /// Open a downloaded file list for browsing.
    bool openFileList(const std::string& fileListId);

    /// Browse a directory inside an opened file list.
    std::vector<FileListEntry> browseFileList(const std::string& fileListId,
                                              const std::string& directory = "/");

    /// Download a file from an opened file list.
    bool downloadFileFromList(const std::string& fileListId,
                              const std::string& filePath,
                              const std::string& downloadTo);

    /// Download an entire directory from an opened file list.
    bool downloadDirFromList(const std::string& fileListId,
                             const std::string& dirPath,
                             const std::string& downloadTo);

    /// Close an opened file list.
    void closeFileList(const std::string& fileListId);

    /// Close all opened file lists.
    void closeAllFileLists();

    // =========================================================================
    // Sharing
    // =========================================================================

    /// Add a directory to share.
    void addShareDir(const std::string& realPath, const std::string& virtualName);

    /// Remove a directory from share.
    void removeShareDir(const std::string& realPath);

    /// Rename a shared directory's virtual name.
    void renameShareDir(const std::string& realPath, const std::string& newVirtName);

    /// List shared directories.
    std::vector<ShareDirInfo> listShare();

    /// Refresh (rescan) shared directories.
    void refreshShare();

    /// Get total share size in bytes.
    int64_t getShareSize();

    /// Get total number of shared files.
    int64_t getSharedFileCount();

    // =========================================================================
    // Transfers
    // =========================================================================

    /// Get list of active transfers (uploads + downloads).
    std::vector<TransferInfo> getActiveTransfers();

    /// Get aggregate transfer statistics.
    TransferStats getTransferStats();

    // =========================================================================
    // Hashing
    // =========================================================================

    /// Get current hash progress.
    HashStatus getHashStatus();

    /// Pause or resume file hashing.
    void pauseHashing(bool pause = true);

    // =========================================================================
    // Settings
    // =========================================================================

    /// Get a setting value by name (e.g. "Nick", "DownloadDirectory").
    std::string getSetting(const std::string& name);

    /// Set a setting value by name.
    void setSetting(const std::string& name, const std::string& value);

    /// Reload configuration from disk.
    void reloadConfig();

    // =========================================================================
    // IP Filtering
    // =========================================================================

    /// Enable/disable IP filter.
    void setIpFilterEnabled(bool enabled);

    /// List IP filter rules.
    std::vector<std::string> listIpFilterRules();

    /// Add IP filter rules (one per line).
    void addIpFilterRules(const std::string& rules);

    /// Remove IP filter rules.
    void purgeIpFilterRules(const std::string& rules);
};
```

### SWIG Typemaps (`typemaps.i`)

Template instantiations for STL containers used in the bridge:

```swig
%template(HubInfoVector) std::vector<HubInfo>;
%template(UserInfoVector) std::vector<UserInfo>;
%template(SearchResultVector) std::vector<SearchResultInfo>;
%template(QueueItemVector) std::vector<QueueItemInfo>;
%template(TransferInfoVector) std::vector<TransferInfo>;
%template(ShareDirVector) std::vector<ShareDirInfo>;
%template(FileListEntryVector) std::vector<FileListEntry>;
%template(StringVector) std::vector<std::string>;
```

### Build System (`dc_core/CMakeLists.txt`)

```cmake
find_package(Python3 REQUIRED COMPONENTS Development)
find_package(SWIG 4.0 REQUIRED)
include(UseSWIG)

# Find libeiskaltdcpp via pkg-config or direct path
find_package(PkgConfig)
pkg_check_modules(DCPP REQUIRED eiskaltdcpp)

set_source_files_properties(dc_core.i PROPERTIES
    CPLUSPLUS ON
    SWIG_FLAGS "-python;-py3;-directors")

swig_add_library(_dc_core
    TYPE SHARED
    LANGUAGE python
    SOURCES dc_core.i bridge.cpp callbacks.cpp)

target_include_directories(_dc_core PRIVATE
    ${Python3_INCLUDE_DIRS}
    ${DCPP_INCLUDE_DIRS})

target_link_libraries(_dc_core
    ${DCPP_LIBRARIES}
    ${Python3_LIBRARIES})

# Install alongside the Python package
install(TARGETS _dc_core DESTINATION verlihub/)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/_dc_core.py DESTINATION verlihub/)
```

## Pythonic Wrapper (`dc_client.py`)

High-level Python class wrapping `_dc_core.DCBridge`:

```python
"""
Full-featured DC client powered by libeiskaltdcpp.

Example:
    from verlihub.client import DCClient

    def on_chat(hub_url, nick, message, third_person):
        print(f"<{nick}> {message}")

    def on_result(hub_url, file, size, free, total, tth, nick):
        print(f"Found: {file} ({size} bytes) TTH:{tth}")

    with DCClient(nick="MyBot", download_dir="/tmp/dc") as dc:
        dc.on_chat_message = on_chat
        dc.on_search_result = on_result

        dc.connect("dchub://example.com:411")
        dc.search("ubuntu iso", file_type=FileType.ANY)
        time.sleep(10)
        for r in dc.get_search_results("dchub://example.com:411"):
            dc.download(r.tth, r.file, r.size)
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Optional

try:
    from verlihub._dc_core import DCBridge, DCClientCallback
    _HAS_DC_CORE = True
except ImportError:
    _HAS_DC_CORE = False


class FileType(IntEnum):
    ANY = 0
    AUDIO = 1
    COMPRESSED = 2
    DOCUMENT = 3
    EXECUTABLE = 4
    PICTURE = 5
    VIDEO = 6
    DIRECTORY = 7
    TTH = 8


class SizeMode(IntEnum):
    DONT_CARE = 0
    AT_LEAST = 1
    AT_MOST = 2


class Priority(IntEnum):
    PAUSED = 0
    LOWEST = 1
    LOW = 2
    NORMAL = 3
    HIGH = 4
    HIGHEST = 5


class DCClient:
    """Full-featured DC client backed by libeiskaltdcpp via SWIG."""

    def __init__(
        self,
        config_dir: str = "~/.verlihub/dcpp",
        nick: str = "VerlihubClient",
        download_dir: str = "",
        slots: int = 3,
    ):
        if not _HAS_DC_CORE:
            raise ImportError(
                "verlihub._dc_core not available. "
                "Build with: cmake -DWITH_DC_CORE=ON && make"
            )
        self._bridge = DCBridge()
        self._config_dir = os.path.expanduser(config_dir)
        self._nick = nick
        self._download_dir = download_dir
        self._slots = slots
        self._initialized = False

        # Callable callbacks (set by user)
        self.on_chat_message: Optional[Callable] = None        # (hub, nick, msg, 3rd)
        self.on_private_message: Optional[Callable] = None     # (hub, from, to, msg)
        self.on_search_result: Optional[Callable] = None       # (hub, file, size, ...)
        self.on_download_complete: Optional[Callable] = None   # (target, nick, size, speed)
        self.on_download_failed: Optional[Callable] = None     # (target, reason)
        self.on_hub_connected: Optional[Callable] = None       # (hub, name)
        self.on_hub_disconnected: Optional[Callable] = None    # (hub, reason)
        self.on_user_connected: Optional[Callable] = None      # (hub, nick)
        self.on_user_disconnected: Optional[Callable] = None   # (hub, nick)

    def __enter__(self) -> DCClient:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        """Initialize the DC core and apply settings."""
        self._bridge.initialize(self._config_dir)
        self._bridge.setSetting("Nick", self._nick)
        if self._download_dir:
            self._bridge.setSetting("DownloadDirectory", self._download_dir)
        self._bridge.setSetting("Slots", str(self._slots))
        # Wire up callback handler
        self._handler = _CallbackRouter(self)
        self._bridge.setCallback(self._handler)
        self._initialized = True

    def stop(self) -> None:
        """Shut down cleanly."""
        if self._initialized:
            self._bridge.shutdown()
            self._initialized = False

    # --- Hub operations ---
    def connect(self, hub_url: str, encoding: str = "") -> None: ...
    def disconnect(self, hub_url: str) -> None: ...
    def list_hubs(self) -> list: ...
    def send_chat(self, hub_url: str, message: str) -> None: ...
    def send_pm(self, hub_url: str, nick: str, message: str) -> None: ...
    def get_users(self, hub_url: str) -> list: ...

    # --- Search ---
    def search(self, query: str, file_type: FileType = FileType.ANY,
               size_mode: SizeMode = SizeMode.DONT_CARE, size: int = 0,
               hub_url: str = "") -> str: ...
    def get_search_results(self, hub_url: str) -> list: ...
    def clear_search_results(self, hub_url: str) -> None: ...

    # --- Downloads ---
    def download(self, tth: str, name: str, size: int,
                 target_dir: str = "") -> None: ...
    def download_magnet(self, magnet: str, target_dir: str = "") -> None: ...
    def remove_download(self, target: str) -> None: ...
    def set_priority(self, target: str, priority: Priority) -> None: ...
    def list_queue(self) -> list: ...

    # --- File lists ---
    def request_file_list(self, hub_url: str, nick: str) -> None: ...
    def browse_file_list(self, list_id: str, directory: str = "/") -> list: ...
    def download_from_list(self, list_id: str, path: str,
                           target_dir: str = "") -> None: ...

    # --- Sharing ---
    def add_share(self, real_path: str, virtual_name: str) -> None: ...
    def remove_share(self, real_path: str) -> None: ...
    def refresh_share(self) -> None: ...
    def list_share(self) -> list: ...

    # --- Transfers & Hashing ---
    def get_transfers(self) -> list: ...
    def get_transfer_stats(self) -> dict: ...
    def get_hash_status(self) -> dict: ...
    def pause_hashing(self, pause: bool = True) -> None: ...

    # --- Settings ---
    def get_setting(self, name: str) -> str: ...
    def set_setting(self, name: str, value: str) -> None: ...


class _CallbackRouter(DCClientCallback):
    """Routes SWIG director callbacks to user-set callables."""

    def __init__(self, client: DCClient):
        super().__init__()
        self._client = client

    def onChatMessage(self, hub, nick, msg, third_person):
        if self._client.on_chat_message:
            self._client.on_chat_message(hub, nick, msg, third_person)

    def onSearchResult(self, hub, file, size, free, total, tth, nick):
        if self._client.on_search_result:
            self._client.on_search_result(hub, file, size, free, total, tth, nick)

    # ... etc for all callbacks
```

## Implementation Phases

### Phase 1: Build Infrastructure (Small)

Build `libeiskaltdcpp.so` with development headers:
```bash
cd eiskaltdcpp && mkdir build && cd build
cmake .. -DNO_UI_DAEMON=ON -DWITH_DEV_FILES=ON -DUSE_QT5=OFF -DUSE_GTK3=OFF
make -j$(nproc)
```

Create `dc_core/CMakeLists.txt` with SWIG + `libeiskaltdcpp` linkage.

### Phase 2: Core Bridge — Lifecycle + Hub Connection (Medium)

- Implement `DCBridge::initialize()` / `shutdown()` (wraps `dcpp::startup()`/`dcpp::shutdown()`)
- Implement `connectHub()` / `disconnectHub()` / `listHubs()`
- Implement `DCClientCallback` director class
- Wire up `ClientListener` events → callback director
- Write SWIG `.i` file for these types
- Test: connect to a hub from Python, receive chat messages

### Phase 3: Pythonic Wrapper — Lifecycle + Chat (Small)

- Create `dc_client.py` with `DCClient` class
- Context manager, callback routing via `_CallbackRouter`
- Update `client/__init__.py` to export `DCClient`
- Test: Python script that connects and chats

### Phase 4: Search (Medium)

- Implement `search()`, `getSearchResults()`, `clearSearchResults()` in bridge
- Wire up `SearchManagerListener::SR` → `onSearchResult` callback
- Add `SearchResultInfo` struct + SWIG template
- Test: search from Python, collect results

### Phase 5: Download Queue (Medium)

- Implement `addToQueue()`, `addMagnet()`, `removeFromQueue()`, `moveQueueItem()`, `setPriority()`, `listQueue()`, `clearQueue()`
- Wire up `QueueManagerListener` + `DownloadManagerListener` events
- Test: queue a file, monitor download, verify completion

### Phase 6: Share + Hashing (Small)

- Implement `addShareDir()`, `removeShareDir()`, `listShare()`, `refreshShare()`
- Implement `getHashStatus()`, `pauseHashing()`
- Wire up `HashManagerListener`
- Test: add share, watch hashing progress

### Phase 7: Transfer Monitoring (Small)

- Implement `getActiveTransfers()`, `getTransferStats()`
- Wire up `DownloadManagerListener::Tick`, `UploadManagerListener::Tick`
- Test: monitor active transfers during downloads

### Phase 8: File List Browsing (Small)

- Implement `requestFileList()`, `openFileList()`, `browseFileList()`, `downloadFileFromList()`, `downloadDirFromList()`, `closeFileList()`
- Add `FileListEntry` struct
- Test: get user's file list, browse, download selected files

### Phase 9: Settings + IP Filter (Small)

- Implement `getSetting()`, `setSetting()`, `reloadConfig()`
- Implement IP filter methods
- Test: change settings from Python, verify persistence

### Phase 10: Build Integration (Medium)

- Integrate into `pyproject.toml` via `scikit-build-core` or `setuptools` + cmake extension
- Add `libeiskaltdcpp-dev` as documented build prerequisite
- Ensure `pip install -e .` builds the SWIG module
- CI integration

### Phase 11: Tests + Documentation (Medium)

- Unit tests for the bridge (mock hub)
- Integration tests against verlihub hub instance
- API documentation with usage examples
- Migration guide from `NMDCClient` to `DCClient`

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| **GIL + C++ threads** — eiskaltdcpp runs multiple internal threads (timer, hasher, connection) that may call director callbacks | SWIG director calls automatically acquire GIL. Ensure callback Python code is lightweight; offload heavy work to separate threads/queues |
| **C++ exceptions** — core library may throw | Wrap all bridge methods in try/catch, translate to Python exceptions via `%exception` directive in SWIG |
| **Singleton lifetime** — `dcpp::startup()`/`shutdown()` must be called exactly once | Guard with `isInitialized()` flag; register `atexit` handler in Python wrapper |
| **Build complexity** — requires `libeiskaltdcpp` headers + `.so` | Document prerequisites; provide build script; consider bundling as git submodule (already in workspace) |
| **Encoding** — NMDC uses CP1252/CP1251 | Already handled by `NmdcHub::toUtf8()`/`fromUtf8()` — Python always sees UTF-8 |
| **Thread safety** — Python callbacks from C++ threads | SWIG directors handle GIL. Bridge methods that read shared state should hold appropriate C++ locks (already done in the library's managers) |

## libeiskaltdcpp Dependency Strategy

### Options Evaluated

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A. Ubuntu system package** | `apt install libeiskaltdcpp-dev libeiskaltdcpp2.4t64` | Zero build effort; official distro package; automatic security updates | Frozen at v2.4.2 (March 2021); **no pkg-config `.pc` file shipped**; only available on Debian/Ubuntu; no control over build flags; can't get upstream fixes without waiting for distro update cycle |
| **B. CMake `FetchContent` from GitHub** | Pull upstream source at configure time via `FetchContent_Declare(eiskaltdcpp GIT_REPOSITORY ... GIT_TAG v2.4.2)` | Pin to any tag/commit; always reproducible; no submodule management; works on any OS with CMake ≥ 3.14 | First build downloads ~15 MB; builds the entire eiskaltdcpp tree (including GUI targets unless suppressed); adds build time; network required at configure time |
| **C. Git submodule** | `git submodule add https://github.com/eiskaltdcpp/eiskaltdcpp.git` | Explicit version pinning; offline after initial clone; easy `git submodule update --remote` to pull new commits; source always present for developers | Adds ~15 MB to repo clones; developers must remember `git submodule init/update`; version bumps require explicit commits |
| **D. Copy sources into verlihub tree** | Vendor `dcpp/` + `extra/` + `dht/` directly | Always available; no submodule/fetch complexity | Enormous maintenance burden; loses upstream git history; manual merge effort for updates; license compliance complexity |
| **E. Hybrid: system package preferred, FetchContent fallback** | Try `find_package`/`pkg_check_modules` first; fall back to `FetchContent` if not found | Best of both worlds; CI can use system packages for speed; developers can build from source anywhere | Most complex CMake logic |

### Recommendation: **Option B — CMake `FetchContent`** (with system package detection)

The best approach is a hybrid that **prefers the system package when available**
but **falls back to building from source via `FetchContent`** when it isn't.
This is preferred over a git submodule because:

1. **Upstream is nearly dormant** — last release v2.4.2 was March 2021, with
   only 82 commits in 5 years since. There won't be frequent updates to track.

2. **Pinning to a specific tag/commit is explicit** — the `FetchContent` block
   in CMakeLists.txt documents exactly which version we depend on. Bumping is
   a one-line commit changing the `GIT_TAG`.

3. **No submodule ceremony** — developers clone the verlihub repo and build.
   No `--recursive`, no `git submodule update`. The source is fetched
   automatically at cmake configure time (and cached in the build directory).

4. **Works with official releases** — we pin to `GIT_TAG v2.4.2` (or a
   specific commit hash for post-release fixes). When/if upstream tags v2.5.0,
   we bump the tag and test.

5. **System package fast-path** — on Ubuntu/Debian, CI and production
   deployments can `apt install libeiskaltdcpp-dev` and skip the source build
   entirely. The CMake logic tries `find_package` first.

6. **The Ubuntu dev package lacks a `.pc` file** — but ships headers at
   `/usr/include/eiskaltdcpp/dcpp/` and `libeiskaltdcpp.so`, so we can detect
   it via a custom `FindEiskaltDCPP.cmake` module that checks for the header
   and library directly.

### CMake Integration

```cmake
# verlihub/python/dc_core/CMakeLists.txt

# ------------------------------------------------------------------
# 1. Try to find system-installed libeiskaltdcpp
# ------------------------------------------------------------------
find_path(EISKALTDCPP_INCLUDE_DIR
    NAMES dcpp/DCPlusPlus.h
    PATHS /usr/include/eiskaltdcpp
          /usr/local/include/eiskaltdcpp
)

find_library(EISKALTDCPP_LIBRARY
    NAMES eiskaltdcpp
    PATHS /usr/lib /usr/lib/x86_64-linux-gnu /usr/local/lib
)

if (EISKALTDCPP_INCLUDE_DIR AND EISKALTDCPP_LIBRARY)
    message(STATUS "Found system libeiskaltdcpp:")
    message(STATUS "  Include: ${EISKALTDCPP_INCLUDE_DIR}")
    message(STATUS "  Library: ${EISKALTDCPP_LIBRARY}")
    
    add_library(eiskaltdcpp::dcpp SHARED IMPORTED)
    set_target_properties(eiskaltdcpp::dcpp PROPERTIES
        IMPORTED_LOCATION "${EISKALTDCPP_LIBRARY}"
        INTERFACE_INCLUDE_DIRECTORIES "${EISKALTDCPP_INCLUDE_DIR}"
    )
else()
    # ------------------------------------------------------------------
    # 2. Fall back to building from source via FetchContent
    # ------------------------------------------------------------------
    message(STATUS "System libeiskaltdcpp not found, building from source...")
    include(FetchContent)
    
    FetchContent_Declare(eiskaltdcpp
        GIT_REPOSITORY https://github.com/eiskaltdcpp/eiskaltdcpp.git
        GIT_TAG        v2.4.2          # Pin to official release
        GIT_SHALLOW    TRUE            # Don't fetch full history
    )
    
    # Configure eiskaltdcpp build options — disable everything except the
    # core library (no GUI, no daemon, no CLI)
    set(NO_UI_DAEMON  ON CACHE BOOL "" FORCE)
    set(USE_QT5       OFF CACHE BOOL "" FORCE)
    set(USE_GTK3      OFF CACHE BOOL "" FORCE)
    set(USE_ASPELL    OFF CACHE BOOL "" FORCE)
    set(JSONRPC_DAEMON OFF CACHE BOOL "" FORCE)
    set(WITH_DEV_FILES OFF CACHE BOOL "" FORCE)
    set(WITH_EMOTICONS OFF CACHE BOOL "" FORCE)
    set(WITH_EXAMPLES  OFF CACHE BOOL "" FORCE)
    set(WITH_SOUNDS    OFF CACHE BOOL "" FORCE)
    set(WITH_LUASCRIPTS OFF CACHE BOOL "" FORCE)
    set(LUA_SCRIPT     OFF CACHE BOOL "" FORCE)
    
    FetchContent_MakeAvailable(eiskaltdcpp)
    
    # The dcpp target is built by eiskaltdcpp's CMakeLists
    # Create an alias for consistent usage
    if (TARGET dcpp)
        add_library(eiskaltdcpp::dcpp ALIAS dcpp)
    endif()
endif()
```

### Distro Packaging Notes

For Debian/Ubuntu `.deb` packaging of verlihub, the `debian/control` file
should list:

```
Build-Depends: libeiskaltdcpp-dev (>= 2.4.2), swig (>= 4.0), python3-dev, ...
```

This uses the system package (option A) without FetchContent, which is the
standard pattern for distribution packaging. The FetchContent fallback is
for developer builds and non-Debian systems.

### Version Pinning Policy

- **Default pin:** `GIT_TAG v2.4.2` (latest official release)
- **Bump process:** Change `GIT_TAG` in CMakeLists.txt, run tests, commit
- **For upstream fixes not yet released:** Can pin to a specific commit hash
  (e.g. `GIT_TAG 697db4b0`) rather than waiting for a tagged release
- **The Ubuntu package is v2.4.2** — identical to the latest upstream release,
  so there is no version mismatch to worry about currently

## Dependency Summary

**Build requirements:**
- CMake ≥ 3.14 (for FetchContent)
- SWIG ≥ 4.0
- Python ≥ 3.10 development headers
- libeiskaltdcpp — either system package or auto-fetched from GitHub
- OpenSSL, zlib, bzip2, iconv, gettext (transitive deps of libeiskaltdcpp)

**Runtime requirements:**
- libeiskaltdcpp.so (system package or built from source)
- Python ≥ 3.10

**Existing verlihub Python dependencies (unchanged):**
- fastapi, uvicorn, sqlmodel, asyncmy, pydantic, httpx, etc.
