# Plan: Verlihub-py SWIG Wrapper Gap Analysis & Porting Roadmap

> **Goal**: Audit the relationship between (1) legacy Verlihub C++ code,
> (2) the new verlihub-py C++ core, and (3) the SWIG wrappers exposing
> the core to Python. Identify gaps in both the legacy→core port and in
> SWIG wrapping coverage, and produce a concrete plan to close them.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Legacy Verlihub Inventory](#2-legacy-verlihub-inventory)
3. [C++ Core Inventory (What Was Ported)](#3-c-core-inventory-what-was-ported)
4. [Legacy → Core Porting Status Map](#4-legacy--core-porting-status-map)
5. [SWIG Wrapper Coverage Map](#5-swig-wrapper-coverage-map)
6. [Gap Analysis: Features Not Yet Ported to Core](#6-gap-analysis-features-not-yet-ported-to-core)
7. [Gap Analysis: Core Methods Not Yet SWIG-Wrapped](#7-gap-analysis-core-methods-not-yet-swig-wrapped)
8. [Python-Side Substitutions](#8-python-side-substitutions)
9. [Porting Plan: Priority-Ordered Work Items](#9-porting-plan-priority-ordered-work-items)
10. [SWIG Wrapping Extension Plan](#10-swig-wrapping-extension-plan)
11. [Risk Assessment](#11-risk-assessment)
12. [Test Plan](#12-test-plan)

---

## 1. Architecture Overview

### Three-Layer Map

```
┌──────────────────────────────────────────────────────────────────┐
│  Layer 3: Python Application                                     │
│  ┌─────────┐ ┌──────────┐ ┌───────────┐ ┌──────────┐           │
│  │ FastAPI  │ │Dashboard │ │  LLM Bot  │ │  CLI     │           │
│  │ REST API │ │WebSocket │ │ (Claude)  │ │  Client  │           │
│  └────┬─────┘ └────┬─────┘ └────┬──────┘ └────┬─────┘           │
│       │             │            │              │                 │
│  ┌────▼─────────────▼────────────▼──────────────▼───┐            │
│  │  verlihub/core.py  (HubContext Python wrapper)   │            │
│  │  + HubEventHandler (IHubEventCallback subclass)  │            │
│  └──────────────────┬───────────────────────────────┘            │
│                     │ SWIG bindings                              │
├─────────────────────┼────────────────────────────────────────────┤
│  Layer 2: C++ Core  │  (src/core/)                               │
│  ┌──────────────────▼───────────────────────────────┐            │
│  │  verlihub_core.i → HubContext + NMDCHubServer    │            │
│  │  + NMDCProtocol + GeoIPLookup + ThreadSafeColls  │            │
│  └──────────────────┬───────────────────────────────┘            │
│                     │ inherits / reuses                          │
├─────────────────────┼────────────────────────────────────────────┤
│  Layer 1: Legacy    │  (src/*.cpp, src/*.h)                      │
│  ┌──────────────────▼───────────────────────────────┐            │
│  │  cServerDC, cDCProto, cUser, cBanList, cRegList, │            │
│  │  cAsyncSocketServer, cAsyncConn, cConnDC, cSetup │            │
│  │  cPluginManager, cMaxMindDB, cICUConvert, etc.   │            │
│  │  (~46,000 LOC across 140+ source files)          │            │
│  └──────────────────────────────────────────────────┘            │
└──────────────────────────────────────────────────────────────────┘
```

### Design Philosophy

Unlike eiskaltdcpp-py (which wraps an existing application context with
all managers accessible), verlihub-py takes a **selective port** approach:

- **Legacy code is not wrapped directly** — it stays as-is for the
  standalone `verlihub` binary
- **A new C++ core** (`src/core/`) reimplements hub server functionality
  using modern C++20, reusing only the networking layer (`cAsyncSocketServer`,
  `cAsyncConn`) from legacy
- **All persistence is delegated to Python** (SQLModel ORM) rather than
  reusing the legacy MySQL layer (`cMySQL`, `cConfMySQL`)
- **The SWIG wrapper exposes only the new core**, not legacy classes

This means there are TWO distinct gap categories:
1. **Legacy → Core gaps**: Features in legacy verlihub not yet reimplemented
   in `src/core/`
2. **Core → SWIG gaps**: Core features implemented in C++ but not exposed
   to Python via SWIG

---

## 2. Legacy Verlihub Inventory

### Size: ~46,000 LOC across 140+ files

#### Functional Areas (27 subsystems)

| # | Area | Key Classes | LOC (est.) | Files |
|---|------|-------------|------------|-------|
| 1 | **Core Server** | `cServerDC` | ~3,900 | `cserverdc.{h,cpp}` |
| 2 | **NMDC Protocol** | `cDCProto`, `cMessageDC` | ~5,400 | `cdcproto.{h,cpp}`, `cmessagedc.{h,cpp}` |
| 3 | **DC Connections** | `cConnDC` | ~900 | `cconndc.{h,cpp}` |
| 4 | **User Model** | `cUser` | ~900 | `cuser.{h,cpp}` |
| 5 | **User Collections** | `cUserCollection` | ~600 | `cusercollection.{h,cpp}` |
| 6 | **Ban System** | `cBan`, `cBanList` | ~1,200 | `cban.{h,cpp}`, `cbanlist.{h,cpp}` |
| 7 | **Registration** | `cRegList`, `cRegUserInfo` | ~500 | `creglist.{h,cpp}`, `creguserinfo.{h,cpp}` |
| 8 | **Kick System** | `cKick`, `cKickList` | ~300 | `ckick.{h,cpp}`, `ckicklist.{h,cpp}` |
| 9 | **Penalty System** | `cPenaltyList` | ~300 | `cpenaltylist.{h,cpp}` |
| 10 | **Trigger System** | `cTrigger`, `cTriggers` | ~400 | `ctrigger.{h,cpp}`, `ctriggers.{h,cpp}` |
| 11 | **Console/Commands** | `cDCConsole`, `cChatConsole` | ~4,500 | `cdcconsole.{h,cpp}`, `cchatconsole.{h,cpp}`, `ccommand*.{h,cpp}` |
| 12 | **Custom Redirects** | `cRedirect`, `cRedirects` | ~300 | `ccustomredirect*.{h,cpp}` |
| 13 | **Client Detection** | `cDCClients`, `cDCTagParser` | ~600 | `cdcclient*.{h,cpp}`, `cdctag.{h,cpp}` |
| 14 | **Configuration** | `cDCConf`, `cConfigBase`, `cSetupList` | ~2,500 | `cdcconf.{h,cpp}`, `cconfig*.{h,cpp}`, `csetuplist.{h,cpp}`, `cdbconf.{h,cpp}` |
| 15 | **Plugin System** | `cVHPluginMgr`, `cVHPlugin` | ~1,200 | `cplugin*.{h,cpp}`, `cvhplugin*.{h,cpp}`, `ccallbacklist.{h,cpp}` |
| 16 | **Database (MySQL)** | `cMySQL`, `cQuery`, `cConfMySQL` | ~1,800 | `cmysql.{h,cpp}`, `cquery.{h,cpp}`, `cconfmysql.{h,cpp}` |
| 17 | **Networking** | `cAsyncSocketServer`, `cAsyncConn` | ~3,000 | `casync*.{h,cpp}`, `cconn*.{h,cpp}` |
| 18 | **Scripting API** | `script_api.h` (40 C functions) | ~1,000 | `script_api.{h,cpp}` |
| 19 | **GeoIP** | `cMaxMindDB` | ~800 | `cmaxminddb.{h,cpp}` |
| 20 | **Server Info** | `cInfoServer` | ~300 | `cinfoserver.{h,cpp}` |
| 21 | **Encoding** | `cICUConvert` | ~500 | `cicuconvert.{h,cpp}` |
| 22 | **Connection Types** | `cConnTypes` | ~200 | `cconntypes.{h,cpp}` |
| 23 | **Compression** | `cZLib` | ~200 | `czlib.{h,cpp}` |
| 24 | **Rate Limiting** | `cFreqLimiter`, `cMeanFrequency` | ~300 | `cfreqlimiter.{h,cpp}`, `cmeanfrequency.h` |
| 25 | **Time/Timeout** | `cTime`, `cTimeOut` | ~400 | `ctime.{h,cpp}`, `ctimeout.{h,cpp}` |
| 26 | **Threading** | `cMutex`, `cThread`, `cWorkerThread` | ~400 | `cmutex.{h,cpp}`, `cthread*.{h,cpp}`, `cworkerthread.{h,cpp}` |
| 27 | **HTTP Client** | `cHTTPConn` | ~300 | `chttpconn.{h,cpp}` |

### Legacy Script API Functions (40 functions)

These represent the "public API" that legacy plugins (Lua, Python) use:

| Category | Functions |
|----------|-----------|
| **Messaging** | `SendDataToUser`, `SendToClass`, `SendToAll`, `SendToActive`, `SendToActiveClass`, `SendToPassive`, `SendToPassiveClass`, `SendPMToAll`, `SendToChat`, `SendToOpChat` |
| **User Mgmt** | `KickUser`, `CloseConnection`, `Ban`, `DeleteNickTempBan`, `DeleteIPTempBan` |
| **User Info** | `GetMyINFO`, `GetUserClass`, `GetUserHost`, `GetUserIP`, `SetUserIP`, `SetMyINFOFlag`, `UnsetMyINFOFlag`, `GetUsersCount`, `GetNickList`, `GetTotalShareSize` |
| **GeoIP** | `GetIPCC`, `GetIPCN`, `GetIPCity` |
| **Registration** | `AddRegUser`, `DelRegUser`, `SetRegClass`, `CheckBotNick` |
| **Configuration** | `SetConfig`, `GetConfig` |
| **Commands** | `ParseCommand`, `ScriptCommand`, `CheckDataPipe` |
| **Hub Control** | `StopHub`, `GetVHCfgDir` |

### Legacy Configuration (190+ variables in cDCConf)

Key groups: User limits (10+), Share limits (20+), Flood protection (50+),
Nick rules (10+), Message sizes (15+), Protocol features (15+), Hub info (15+),
Class differences (20+), Tag validation (15+), GeoIP zones (10+), Misc (20+).

---

## 3. C++ Core Inventory (What Was Ported)

### Size: ~5,000 LOC across 8 files in `src/core/`

| File | LOC | Purpose |
|------|-----|---------|
| `hub_context.h` | 897 | Main context object — lifecycle, config, users, messaging, plugins |
| `hub_context.cpp` | ~1,200 | Implementation of HubContext |
| `nmdc_hub_server.h` | 388 | NMDC server — connection state, protocol handling |
| `nmdc_hub_server.cpp` | ~800 | Implementation of NMDCHubServer |
| `nmdc_protocol.h` | 218 | Protocol message construction & parsing |
| `nmdc_protocol.cpp` | ~500 | Implementation of protocol utilities |
| `geo_ip_lookup.h` | ~100 | GeoIP lookup (.mmdb) |
| `geo_ip_lookup.cpp` | ~300 | Implementation of GeoIPLookup |
| `thread_safe_collections.h` | 622 | Thread-safe maps, user collection, counters |

### HubContext Public API (Exposed to Python)

#### Lifecycle
| Method | Ported From |
|--------|-------------|
| `Create(config_dir)` | `cServerDC::cServerDC(CfgBase)` |
| `Initialize()` | `cServerDC` init sequence |
| `Start(port, ip)` | `cServerDC::StartListening()` |
| `Stop()` | `cServerDC::SyncStop()` |
| `IsRunning()` | New (atomic flag) |
| `RequestShutdown(signal)` | `pending_signal_*` globals |
| `RequestReload()` | `cServerDC::ReloadNow()` |

#### User Operations
| Method | Ported From |
|--------|-------------|
| `GetUserCount()` | `cServerDC::mUserCountTot` |
| `GetUserNicks()` | Subset of `cServerDC::mUserList` |
| `GetUserInfo(nick, out)` | `script_api::GetMyINFO` + enhanced |
| `GetUserInfoSnapshots()` | New (thread-safe batch copy) |
| `KickUser(op, nick, reason)` | `script_api::KickUser` |
| `AddRobot(nick, desc, class)` | `cServerDC::AddRobot` |
| `RemoveRobot(nick)` | `cServerDC::DelRobot` |

#### Messaging
| Method | Ported From |
|--------|-------------|
| `SendToUser(nick, msg)` | `script_api::SendDataToUser` |
| `SendToAll(msg)` | `script_api::SendToAll` |
| `SendToClass(msg, min, max)` | `script_api::SendToClass` |
| `SendToOpChat(msg, from)` | `script_api::SendToOpChat` |

#### Configuration
| Method | Ported From |
|--------|-------------|
| `GetConfig(section, key, default)` | `script_api::GetConfig` |
| `SetConfig(section, key, value)` | `script_api::SetConfig` |
| `GetHubConfig()` | HubConfig struct (subset of cDCConf) |
| `GetHubName()` / `GetHubTopic()` | `cDCConf::hub_name` / `hub_topic` |
| `SetHubTopic(topic)` | `cDCProto::Create_HubTopic` |
| `SetMOTD(motd)` | New |
| `GetHubEncoding()` | `cDCConf::hub_encoding` |
| `GetTotalShare()` | `cServerDC::mTotalShare` |

#### Plugin Management
| Method | Ported From |
|--------|-------------|
| `LoadPlugin(path)` | `cPluginManager::LoadPlugin` |
| `UnloadPlugin(name)` | `cPluginManager::UnloadPlugin` |
| `ReloadPlugin(name)` | `cPluginManager::ReloadPlugin` |
| `GetLoadedPlugins()` | `cPluginManager::List` |
| `IsPluginLoaded(name)` | `cPluginManager::GetPlugin` |
| `ExecuteLuaScript(path)` | `cVHPlugin::LoadScript` |
| `UnloadLuaScript(path)` | `cVHPlugin::UnLoadScript` |
| `GetLoadedLuaScripts()` | New |
| `ExecutePythonScript(path)` | `cVHPlugin::LoadScript` |
| `UnloadPythonScript(path)` | `cVHPlugin::UnLoadScript` |
| `GetLoadedPythonScripts()` | New |

#### Event Callback (IHubEventCallback)
| Callback | Ported From |
|----------|-------------|
| `OnUserConnect(nick, ip)` | Plugin `OnNewConn` |
| `OnUserDisconnect(nick)` | Plugin `OnCloseConn` |
| `OnValidateNick(nick, ip)` | `cServerDC::ValidateNick` → moved to Python |
| `OnCheckPassword(nick, pass)` | `cRegList::FindRegInfo` + `PWVerify` → moved to Python |
| `OnUserLogin(nick, class)` | Plugin `OnUserLogin` |
| `OnUserLogout(nick)` | Plugin `OnUserLogout` |
| `OnChatMessage(nick, msg)` | Plugin `OnParsedMsgChat` |
| `OnPrivateMessage(from, to, msg)` | Plugin `OnParsedMsgPM` |
| `OnSearch(nick, query)` | Plugin `OnParsedMsgSearch` |
| `OnTimer(timestamp)` | `cServerDC::OnTimer` |
| `OnHubStarted()` | New |
| `OnHubStopping()` | New |
| `OnLog(level, message)` | New (C++ log forwarding) |
| `OnGetConfig(section, key, default)` | New (config bridge) |

### NMDCHubServer API

| Method | Ported From |
|--------|-------------|
| `SendToNick(nick, data)` | `cServerDC::DCPublic` + user lookup |
| `SendToAll(data)` | `cServerDC::SendToAll` |
| `SendChatToAll(from, msg)` | `cServerDC::DCPublicToAll` |
| `SendPM(from, to, msg)` | `cServerDC::DCPrivateHS` |
| `GetNickList()` | `cDCProto::NickList` |
| `GetOpList()` | `cServerDC::mOpList` iteration |
| `GetUserCount()` | `cServerDC::mUserCountTot` |
| `IsNickOnline(nick)` | `cServerDC::GetConnUserByNick` equivalent |
| `GetTotalShare()` | `cServerDC::mTotalShare` |
| `KickUser(nick, reason, op)` | `cServerDC::DCKickNick` |
| `DisconnectUser(nick)` | `script_api::CloseConnection` |
| `SetHubName/Topic/Security/OpChat/MaxUsers/MOTD` | `cDCConf` setters |

### NMDCProtocol Utilities

| Method | Ported From |
|--------|-------------|
| `GenerateLock()` | Random lock generation |
| `Lock2Key(lock)` | `cDCProto::Lock2Key` |
| `Escape/UnEscape(str)` | `cDCProto::EscapeChars/UnEscapeChars` |
| `MakeLock/Supports/HubName/Hello/GetPass/...` | `cDCProto::Create_*` factory methods |
| `ParseMyINFO(msg)` | `cDCProto::DC_MyINFO` parsing |
| `ParseTag(tag)` | Tag parsing from `cDCTagParser` |
| `ParsePrivateMessage(msg)` | `cDCProto::DC_To` parsing |
| `ParseChat(msg)` | `cDCProto::DC_Chat` parsing |
| `ParseSR(msg)` | `cDCProto::DC_SR` parsing |

---

## 4. Legacy → Core Porting Status Map

### Status Key
- **✅ Ported** — Reimplemented in `src/core/` or intentionally delegated to Python
- **🔄 Partial** — Core concept ported but missing features
- **🐍 Python** — Deliberately moved to Python layer (not in C++)
- **❌ Not Ported** — Feature exists in legacy but missing from both core and Python
- **🚫 Intentional** — Deliberately omitted (obsolete, replaced, or not needed)

### Master Map

| # | Legacy Subsystem | Status | Notes |
|---|-----------------|--------|-------|
| 1 | **Core Server** (`cServerDC`) | 🔄 Partial | `HubContext` replaces lifecycle, signals, user mgmt. Missing: `WhoCC`/`WhoCity`/`WhoIP` queries, protocol counters, zone-based user counts, `CheckUserClone`, hublist registration (in Python), update checks |
| 2 | **NMDC Protocol** (`cDCProto`) | 🔄 Partial | `NMDCProtocol` + `NMDCHubServer` cover auth flow + core messages. Missing: `$MCTo` (multi-chat), `$ExtJSON`, `$IN` extension, `$MyHubURL`, `$ConnectToMe`/`$RevConnectToMe` (passive relay), `$SA`/`$SP` (active/passive search variants), `$BotINFO`, admin commands (`$OpForceMove`, `$TempBan`, `$UnBan`, `$GetBanList`, `$SetTopic`, `$WhoIP`, `$UserIP`), `$ZOn` (zlib compression) |
| 3 | **DC Connections** (`cConnDC`) | ✅ Ported | `NMDCClient` struct + connection state machine in `NMDCHubServer` |
| 4 | **User Model** (`cUser`) | 🔄 Partial | `NMDCClient` + `UserInfoSnapshot` cover basic fields. Missing: `cUser::mRights` (fine-grained permissions), `mFloodHashes`/`mFloodCounters` (per-user flood state), `mxConn` (back-pointer to connection), user class methods |
| 5 | **User Collections** (`cUserCollection`) | ✅ Ported | `ThreadSafeUserCollection` replaces with reader-writer locking. Missing: `ufSendMinMax` (class-range send), `ufSendWithNick` (nick-interpolated send), country/zone filtering |
| 6 | **Ban System** (`cBan`, `cBanList`) | 🐍 Python | Ban storage/checking moved to Python/SQLModel DB. C++ core has no ban logic — relies on `OnValidateNick` callback. Missing from Python: IP range bans, host-pattern bans, share-size bans, prefix bans, temporary bans with auto-expiry, ban flag types (11 types in legacy) |
| 7 | **Registration** (`cRegList`, `cRegUserInfo`) | 🐍 Python | Moved to Python `RegUser` SQLModel. Password hashing upgraded to bcrypt. Core delegates via `OnCheckPassword` callback |
| 8 | **Kick System** (`cKick`, `cKickList`) | 🔄 Partial | `KickUser()` implemented in core + exposed via SWIG. Missing: kick history DB logging, `FindKick` for operator queries, drop (silent disconnect) vs kick distinction |
| 9 | **Penalty System** (`cPenaltyList`) | ❌ Not Ported | No equivalent in core or Python. Legacy penalties include: chat gag, search ban, CTM ban, PM ban, kick stop, share-0 stop, registration stop, opchat stop. Each with separate timers |
| 10 | **Trigger System** (`cTrigger`, `cTriggers`) | ❌ Not Ported | Auto-responding triggers with regex matching, timer-based firing, DB-stored definitions, class restrictions, MOTD/help flags. Not in core or Python |
| 11 | **Console/Commands** (`cDCConsole`, `cChatConsole`) | 🐍 Python | In-chat operator commands (`!kick`, `!ban`, etc.) handled by LLM bot + REST API. Missing: legacy command syntax compatibility, `cChatConsole` chat room management (invite/leave/out/members) |
| 12 | **Custom Redirects** (`cRedirect`, `cRedirects`) | ❌ Not Ported | Redirect users to alternate hubs on kick/full/share-limit/bad-password/etc. 11 redirect trigger types. Not in core or Python |
| 13 | **Client Detection** (`cDCClients`, `cDCTagParser`) | 🔄 Partial | `NMDCProtocol::ParseTag()` extracts client name/version/mode/slots/hubs. Missing: client whitelist/blacklist DB, version range enforcement, ban-by-client-type, `cDCClients` database management |
| 14 | **Configuration** (`cDCConf`, 190+ vars) | 🔄 Partial | `HubConfig` struct covers ~30 essential settings. YAML config via Python callback. Missing: ~160 legacy config vars (flood protection, tag validation, class differences, message sizes, full search config, protocol features, etc.) |
| 15 | **Plugin System** (`cVHPluginMgr`) | ✅ Ported | `LoadPlugin`/`UnloadPlugin`/`ReloadPlugin` + Lua/Python script management via HubContext. Event callbacks via `IHubEventCallback` |
| 16 | **Database (MySQL)** (`cMySQL`, `cQuery`) | 🐍 Python | Replaced by SQLModel async ORM — supports SQLite, MySQL, PostgreSQL. Legacy MySQL layer kept for standalone verlihub binary but not used by verlihub-py |
| 17 | **Networking** (`cAsyncSocketServer`, `cAsyncConn`) | ✅ Ported | New core inherits `cAsyncSocketServer` directly. Poll/select I/O multiplexing reused as-is |
| 18 | **Scripting API** (40 functions) | 🔄 Partial | ~20/40 functions have equivalents in HubContext. Missing: `SendToActive/Passive`, `SendToActiveClass/PassiveClass`, `Ban`, `DeleteNickTempBan/DeleteIPTempBan`, `GetMyINFO` (raw), `GetUserHost`, `SetUserIP`, `Set/UnsetMyINFOFlag`, `ParseCommand`, `ScriptCommand`, `CheckDataPipe`, `CheckBotNick`, `GetIPCC/CN/City` (in C++; Python does it) |
| 19 | **GeoIP** (`cMaxMindDB`) | ✅ Ported | `GeoIPLookup` class reimplemented in core with no `cServerDC` dependency. Also duplicated in Python `enrichment.py` for REST API |
| 20 | **Server Info** (`cInfoServer`) | ❌ Not Ported | Port/protocol/buffer/URL/system info display. Not in core. Partially covered by dashboard |
| 21 | **Encoding** (`cICUConvert`) | 🔄 Partial | `HubContext` owns an `cICUConvert*` pointer but new core doesn't directly use ICU for protocol messages. Python handles encoding for REST/dashboard |
| 22 | **Connection Types** (`cConnTypes`) | ❌ Not Ported | Per-connection-type slot/limit rules. Not in core or Python |
| 23 | **Compression** (`cZLib`) | ❌ Not Ported | `$ZOn` NMDC extension for zlib-compressed data. Not in core. Most modern DC clients don't require it |
| 24 | **Rate Limiting** (`cFreqLimiter`, `cMeanFrequency`) | ❌ Not Ported | Per-user rate limiting with configurable periods and thresholds. Not in core — currently no flood protection |
| 25 | **Time/Timeout** (`cTime`, `cTimeOut`) | 🚫 Intentional | Core uses `std::chrono` and `steady_clock` instead. Login timeout implemented via config |
| 26 | **Threading** (`cMutex`, `cThread`, `cWorkerThread`) | 🚫 Intentional | Core uses C++20 `std::jthread`, `std::shared_mutex`, `std::atomic`. No need for legacy wrappers |
| 27 | **HTTP Client** (`cHTTPConn`) | 🐍 Python | Python `httpx` library used for hublist registration, update checks, web search. No need in C++ core |

### Summary Statistics

| Status | Count | Percentage |
|--------|-------|------------|
| ✅ Ported | 5 | 19% |
| 🔄 Partial | 8 | 30% |
| 🐍 Python | 4 | 15% |
| ❌ Not Ported | 7 | 26% |
| 🚫 Intentional | 3 | 11% |

**Overall porting coverage: ~49% complete** (counting Ported + Python as done, Partial as half)

---

## 5. SWIG Wrapper Coverage Map

### What `verlihub_core.i` Currently Exposes

| Category | Exposed | Details |
|----------|---------|---------|
| **HubContext lifecycle** | ✅ Full | `Create`, `Initialize`, `Start`, `Stop`, `IsRunning` |
| **Signal handling** | ✅ Full | `RequestShutdown`, `RequestReload`, `HasPendingShutdown/Reload` |
| **User queries** | ✅ Full | `GetUserCount`, `GetUserNicks`, `GetUserInfo`, `GetUserInfoSnapshots` |
| **Messaging** | ✅ Full | `SendToUser`, `SendToAll`, `SendToClass`, `SendToOpChat` |
| **Kick/disconnect** | ✅ Full | `KickUser` |
| **Bot management** | ✅ Full | `AddRobot`, `RemoveRobot` |
| **Hub info** | ✅ Full | `GetHubName`, `GetHubTopic`, `SetHubTopic`, `SetMOTD`, `GetTotalShare`, `GetHubEncoding` |
| **Configuration** | ✅ Full | `GetConfig`, `SetConfig`, `GetHubConfig` |
| **Plugin management** | ✅ Full | `Load/Unload/ReloadPlugin`, `GetLoadedPlugins`, `IsPluginLoaded` |
| **Script management** | ✅ Full | `Execute/UnloadLuaScript`, `Execute/UnloadPythonScript`, `GetLoaded*Scripts` |
| **Event callbacks** | ✅ Full | `IHubEventCallback` director class with all 14 callbacks |
| **Data types** | ✅ Full | `UserInfoSnapshot`, `HubConfig`, `PluginInfo`, `HubEventType` |
| **Python extensions** | ✅ Full | Context manager (`__enter__`/`__exit__`), property accessors, dict helpers |

### What Is NOT Exposed (Intentionally Hidden)

| Item | Reason |
|------|--------|
| `GetServer()` | Returns nullptr (legacy only) |
| `GetNMDCServer()` | Internal; Python shouldn't access raw server |
| `GetPluginManager()` | Internal; use `LoadPlugin`/etc. methods |
| `GetICUConverter()` | Internal C++ encoding |
| `GetGeoIP()` | Internal; Python has own GeoIP |
| `ForEachUser`/`ForEachUserInClass` | C++ templates with concepts — not SWIG-compatible; use `GetUserInfoSnapshots()` |
| `Log`/`LogFmt` | `source_location` not SWIG-compatible; Python gets logs via `OnLog` callback |
| `FireEvent` | Internal dispatch |
| `ThreadSafeUserCollection` | Implementation detail |
| `NMDCHubServer` | Not directly exposed; accessed through HubContext |
| `NMDCProtocol` | Not exposed; internal protocol utilities |
| `GeoIPLookup` | Not exposed; Python has own GeoIP in `enrichment.py` |

### SWIG Wrapper Gap Assessment

**Current coverage of the C++ core API: approximately 90%** of methods
intended for Python consumption are wrapped. The hidden items are deliberately
internal. No unintentional gaps exist — the SWIG wrapper exposes everything
that HubContext makes public for external use.

---

## 6. Gap Analysis: Features Not Yet Ported to Core

### Priority 0 — Critical (Missing core hub functionality)

#### G1: Flood Protection / Rate Limiting
- **Legacy**: `cFreqLimiter` + `cMeanFrequency` + 50+ `cDCConf` flood settings per message type
- **Current**: Zero flood protection in NMDCHubServer — any client can spam unlimited messages
- **Impact**: Hub is vulnerable to DoS from a single misbehaving client
- **Proposal**: Implement token-bucket rate limiter in `NMDCHubServer` with per-client state, configurable via Python config callback. Core needs: chat flood, PM flood, search flood, connection flood (per-IP), MyINFO flood
- **Size estimate**: ~400 LOC C++

#### G2: Ban Checking in Protocol Layer
- **Legacy**: `cBanList` checks IP/nick/host/share before login completes
- **Current**: `OnValidateNick` callback delegates ALL ban checking to Python, which requires async DB query via `run_coroutine_threadsafe()`. This adds latency and complexity
- **Impact**: Elevated login latency; ban database lookup on every connection
- **Proposal**: Add an optional fast-path ban cache in NMDCHubServer — a `std::unordered_set<std::string>` of banned IPs/nicks loaded from Python at startup and refreshed on changes. The Python callback remains authoritative but the C++ cache provides instant rejection for known bans
- **Size estimate**: ~150 LOC C++

#### G3: Active/Passive User Distinction
- **Legacy**: Separate `mActiveUsers` and `mPassiveUsers` collections with dedicated send methods (`SendToActive`, `SendToPassive`)
- **Current**: `NMDCHubServer` tracks `mode` field per client but has no active/passive collections or targeted send
- **Impact**: Cannot send different search results or messages to active vs passive users (required for correct NMDC search routing)
- **Proposal**: Add `SendToActive(data)` and `SendToPassive(data)` methods to NMDCHubServer, plus class-filtered variants
- **Size estimate**: ~100 LOC C++

### Priority 1 — Important (Missing administrative features)

#### G4: `$ConnectToMe` / `$RevConnectToMe` Routing
- **Legacy**: `cDCProto::DC_ConnectToMe` and `DC_RevConnectToMe` handle direct client-to-client connection initiation (essential for file transfers)
- **Current**: `NMDCHubServer::HandleConnectToMe` and `HandleRevConnectToMe` exist as stubs — they receive the messages but their implementation status is unknown
- **Impact**: File transfers between clients may not work
- **Proposal**: Verify CTM/RCTM relay is fully implemented; add IP validation

#### G5: `$MCTo` (Multi-Chat To) Support
- **Legacy**: `cDCProto::DC_MCTo` — send message visible only to a specific set of users
- **Current**: Not implemented in NMDCProtocol or NMDCHubServer
- **Impact**: Cannot restrict chat messages to operator chat or specific user groups in-protocol
- **Proposal**: Add `HandleMCTo` to NMDCHubServer
- **Size estimate**: ~80 LOC C++

#### G6: IP Range / Subnet Bans in Python
- **Legacy**: `cBanList` supports IP range bans (`eBF_RANGE`), hostname bans (`eBF_HOST1/2/3`), share-size bans, prefix bans
- **Current**: Python `RegUser`/DB only stores nick-based data. No IP range ban support
- **Impact**: Cannot ban entire subnets or ISPs
- **Proposal**: Add `BanEntry` SQLModel with IP range fields, CIDR notation support, expiry timestamps
- **Size estimate**: ~200 LOC Python

#### G7: Penalty System
- **Legacy**: `cPenaltyList` — temporary per-user restrictions (chat gag, search ban, PM ban, CTM ban) with separate timers for each
- **Current**: No equivalent. The LLM bot can conceptually "ignore" spammers but there's no protocol-level enforcement
- **Impact**: Cannot temporarily restrict specific user capabilities
- **Proposal**: Add penalty table in Python DB + enforcement in `OnChatMessage`/`OnSearch` event handlers. Consider optional C++ cache for performance
- **Size estimate**: ~200 LOC Python + ~100 LOC C++

#### G8: `$UserIP` / `$WhoIP` Support
- **Legacy**: `cDCProto::DCO_UserIP` and `DCO_WhoIP` — operators can query user IPs
- **Current**: `NMDCProtocol::MakeUserIP` exists for single user but no `$WhoIP` handler
- **Impact**: Operators using legacy DC clients cannot query IPs via protocol
- **Proposal**: Add `HandleUserIP` and `HandleWhoIP` to NMDCHubServer (operator-only)
- **Size estimate**: ~60 LOC C++

#### G9: `$OpForceMove` Support
- **Legacy**: `cDCProto::DCO_OpForceMove` — redirect a user to another hub
- **Current**: Not implemented
- **Impact**: Cannot redirect users to alternate hubs
- **Proposal**: Add `HandleOpForceMove` + `ForceMove(nick, address)` in HubContext
- **Size estimate**: ~60 LOC C++ + SWIG exposure

#### G10: Server Info / Statistics
- **Legacy**: `cInfoServer` — port stats, protocol counters, buffer stats, system info
- **Current**: Basic counters only (user count, total share). No protocol-level statistics
- **Impact**: Dashboard lacks protocol-level diagnostics
- **Proposal**: Add protocol message counters to NMDCHubServer, expose via HubContext
- **Size estimate**: ~100 LOC C++ + SWIG exposure

### Priority 2 — Nice to Have

#### G11: Trigger System
- **Legacy**: `cTriggers` — auto-responding text triggers, timer-based, regex matching, DB-stored
- **Current**: Not ported. LLM bot handles conversational responses but triggers are deterministic
- **Proposal**: Implement as pure Python module using DB-stored trigger definitions
- **Size estimate**: ~300 LOC Python

#### G12: Custom Redirects
- **Legacy**: `cRedirects` — 11 redirect trigger types (kick, full, share limit, etc.)
- **Current**: Not ported
- **Proposal**: Add `$ForceMove` support in C++ (G9) + redirect rules in Python config
- **Size estimate**: ~100 LOC Python (rules) + G9 C++ work

#### G13: Client Detection Database
- **Legacy**: `cDCClients` — MySQL-backed whitelist/blacklist of DC client software with version ranges
- **Current**: `ParseTag()` extracts client info but no enforcement
- **Proposal**: Add client rules to Python config YAML. Enforce in `OnUserLogin` callback
- **Size estimate**: ~100 LOC Python

#### G14: ZLib Compression (`$ZOn`)
- **Legacy**: `cZLib` — NMDC extension for compressing data to slow clients
- **Current**: Not ported. Most modern clients handle bandwidth fine without compression
- **Proposal**: Low priority. Implement only if needed for legacy client compatibility
- **Size estimate**: ~200 LOC C++

#### G15: `$ExtJSON` / `$IN` Extensions
- **Legacy**: `cDCProto::DC_ExtJSON` and `DC_IN` — NMDC protocol extensions for JSON metadata and status messages
- **Current**: Not ported
- **Proposal**: Low priority. These are non-standard extensions used by few clients
- **Size estimate**: ~100 LOC C++

#### G16: `$MyHubURL` Extension
- **Legacy**: `cDCProto::DC_MyHubURL` — clients report their hub URL for referrer tracking
- **Current**: Not ported
- **Proposal**: Low priority
- **Size estimate**: ~30 LOC C++

#### G17: Connection Types Database
- **Legacy**: `cConnTypes` — per-connection-type slot/limit rules
- **Current**: Not ported. Modern approach would use tag parsing + config rules
- **Proposal**: Low priority; fold into client detection rules (G13)

#### G18: Chat Room Management
- **Legacy**: `cChatConsole` — private chat rooms with invite/leave/members commands
- **Current**: `OpChat` exists but no user-created chat rooms
- **Proposal**: Could be a Python-side feature with NMDC PM routing
- **Size estimate**: ~200 LOC Python

---

## 7. Gap Analysis: Core Methods Not Yet SWIG-Wrapped

The SWIG wrapper (`verlihub_core.i`) currently covers **~90% of HubContext's
public API** intended for external consumption. The remaining items are
intentionally hidden (internal pointers, templates, `source_location`).

### Items That SHOULD Be Exposed But Currently Aren't

| Method / Type | Reason to Expose | Priority |
|---------------|------------------|----------|
| `NMDCProtocol::ParseTag(tag)` | Useful for Python-side client detection rules | P2 |
| `NMDCProtocol::ParseMyINFO(msg)` | Useful for raw protocol debugging tools | P2 |
| `GeoIPLookup::Lookup(ip)` | Avoids duplicating MaxMind lookup in Python | P1 |
| `GeoIPResult` struct | Return type for GeoIPLookup | P1 |
| `HubContext::FindUser(nick)` | Returns `cUser*` — currently always nullptr in verlihub-py mode. Should return wrapped `UserInfoSnapshot` instead | P1 |
| `NMDCHubServer::DisconnectUser(nick)` | Silent disconnect (no kick message) — useful for flood response | P1 |
| `NMDCHubServer::GetTotalShare()` | Exposed on HubContext but not directly | OK |
| `NMDCConnState` enum | Useful for diagnostics | P2 |

### New Methods That Should Be Added to HubContext AND Wrapped

These are methods that don't exist yet but should be added to support
the features identified in the gap analysis:

| Proposed Method | For Gap | Priority |
|----------------|---------|----------|
| `SendToActive(msg)` | G3 | P0 |
| `SendToPassive(msg)` | G3 | P0 |
| `SendToActiveClass(msg, min, max)` | G3 | P0 |
| `SendToPassiveClass(msg, min, max)` | G3 | P0 |
| `ForceMove(nick, address)` | G9 | P1 |
| `DisconnectUser(nick)` | Exists on NMDCHubServer, needs HubContext proxy | P1 |
| `SendPM(from, to, message)` | Core has it on NMDCHubServer, needs HubContext proxy | P1 |
| `GetProtocolStats()` | G10 | P1 |
| `GetActiveUserCount()` / `GetPassiveUserCount()` | G3 | P1 |
| `BroadcastChat(from, msg)` | Convenience; NMDCHubServer has `SendChatToAll` | P1 |
| `SetFloodConfig(type, period, limit)` | G1 | P0 |
| `LoadBanCache(ips, nicks)` | G2 | P0 |
| `RefreshBanCache()` | G2 | P0 |

---

## 8. Python-Side Substitutions

Several legacy features have been intentionally replaced by Python-layer
implementations rather than porting to C++:

| Legacy Feature | Python Replacement | Adequacy |
|----------------|-------------------|----------|
| `cMySQL` + `cConfMySQL` | SQLModel async ORM | ✅ Superior (multi-DB, async, migrations) |
| `cRegList` + `cRegUserInfo` | `RegUser` model + bcrypt | ✅ Superior (bcrypt > MD5/crypt) |
| `cSetupList` (DB-backed config) | YAML config + `OnGetConfig` callback | ✅ Equivalent (easier to edit) |
| `cDCConsole` operator commands | REST API + LLM bot + CLI | ✅ Superior (remote access, AI assistance) |
| `script_api.h` (40 C functions) | HubContext Python wrapper methods | 🔄 ~50% covered |
| `cHTTPConn` (hublist registration) | Python `httpx` in `hublist.py` | ✅ Superior (async, modern HTTP) |
| `cMaxMindDB` GeoIP | Python `maxminddb` in `enrichment.py` | ✅ Equivalent + cache |
| `cInfoServer` statistics | Dashboard WebSocket + REST API | 🔄 Partial (needs protocol stats) |

---

## 9. Porting Plan: Priority-Ordered Work Items

### Phase 1: Security & Correctness (P0)

| # | Work Item | Type | Est. LOC | Gap |
|---|-----------|------|----------|-----|
| 1.1 | Implement token-bucket rate limiter in NMDCHubServer | C++ | 400 | G1 |
| 1.2 | Add per-client flood state tracking (chat, PM, search, MyINFO) | C++ | 200 | G1 |
| 1.3 | Expose `SetFloodConfig()` on HubContext | C++ + SWIG | 50 | G1 |
| 1.4 | Add ban cache (`unordered_set<string>`) in NMDCHubServer | C++ | 150 | G2 |
| 1.5 | Add `LoadBanCache()` / `RefreshBanCache()` to HubContext | C++ + SWIG | 80 | G2 |
| 1.6 | Add active/passive user tracking + `SendToActive/Passive` | C++ + SWIG | 150 | G3 |
| 1.7 | Verify `$ConnectToMe` / `$RevConnectToMe` relay works | C++ | 100 | G4 |
| | **Phase 1 Total** | | **~1,130** | |

### Phase 2: Administrative Features (P1)

| # | Work Item | Type | Est. LOC | Gap |
|---|-----------|------|----------|-----|
| 2.1 | Add `HandleMCTo` to NMDCHubServer | C++ | 80 | G5 |
| 2.2 | Add `BanEntry` SQLModel with IP ranges, CIDR, expiry | Python | 200 | G6 |
| 2.3 | Add penalty table + enforcement in event handlers | Python + C++ | 300 | G7 |
| 2.4 | Add `$UserIP` / `$WhoIP` protocol handlers | C++ | 60 | G8 |
| 2.5 | Add `$OpForceMove` handler + `ForceMove()` on HubContext | C++ + SWIG | 80 | G9 |
| 2.6 | Add protocol message counters + `GetProtocolStats()` | C++ + SWIG | 100 | G10 |
| 2.7 | Expose `DisconnectUser()` on HubContext | C++ + SWIG | 30 | Core gap |
| 2.8 | Expose `SendPM()` on HubContext | C++ + SWIG | 30 | Core gap |
| 2.9 | Expose `GeoIPLookup::Lookup()` via SWIG | SWIG | 40 | SWIG gap |
| 2.10 | Expose `BroadcastChat()` on HubContext | C++ + SWIG | 30 | Core gap |
| | **Phase 2 Total** | | **~950** | |

### Phase 3: Completeness (P2)

| # | Work Item | Type | Est. LOC | Gap |
|---|-----------|------|----------|-----|
| 3.1 | Implement trigger system in Python | Python | 300 | G11 |
| 3.2 | Add redirect rules to Python config + `$ForceMove` | Python | 100 | G12 |
| 3.3 | Add client detection rules to Python config | Python | 100 | G13 |
| 3.4 | Expose `NMDCProtocol::ParseTag` / `ParseMyINFO` via SWIG | SWIG | 40 | SWIG gap |
| 3.5 | Add chat room management in Python | Python | 200 | G18 |
| 3.6 | Add `$ExtJSON` / `$IN` / `$MyHubURL` support | C++ | 130 | G15/G16 |
| 3.7 | Add `$ZOn` zlib compression (optional) | C++ | 200 | G14 |
| | **Phase 3 Total** | | **~1,070** | |

### Grand Total: ~3,150 LOC across all phases

---

## 10. SWIG Wrapping Extension Plan

### New Items to Add to `verlihub_core.i`

#### Phase 1 Additions

```
// Active/passive messaging
%feature("docstring") nVerliHub::HubContext::SendToActive "...";
%feature("docstring") nVerliHub::HubContext::SendToPassive "...";
%feature("docstring") nVerliHub::HubContext::SendToActiveClass "...";
%feature("docstring") nVerliHub::HubContext::SendToPassiveClass "...";

// Flood configuration
%feature("docstring") nVerliHub::HubContext::SetFloodConfig "...";

// Ban cache
%feature("docstring") nVerliHub::HubContext::LoadBanCache "...";
%feature("docstring") nVerliHub::HubContext::RefreshBanCache "...";

// Active/passive counts
%feature("docstring") nVerliHub::HubContext::GetActiveUserCount "...";
%feature("docstring") nVerliHub::HubContext::GetPassiveUserCount "...";
```

#### Phase 2 Additions

```
// Force move
%feature("docstring") nVerliHub::HubContext::ForceMove "...";

// Disconnect
%feature("docstring") nVerliHub::HubContext::DisconnectUser "...";

// Private messaging via HubContext
%feature("docstring") nVerliHub::HubContext::SendPM "...";

// Chat broadcast
%feature("docstring") nVerliHub::HubContext::BroadcastChat "...";

// Protocol statistics
struct ProtocolStats {
    uint64_t messages_received;
    uint64_t messages_sent;
    uint64_t bytes_received;
    uint64_t bytes_sent;
    // per-type counters
    uint64_t chat_messages;
    uint64_t pm_messages;
    uint64_t searches;
    uint64_t ctm_messages;
    uint64_t myinfo_updates;
};
%template(ProtocolStats) nVerliHub::ProtocolStats;

// GeoIP direct access
%include "core/geo_ip_lookup.h"
%template(GeoIPResult) nVerliHub::GeoIPResult;
```

#### Phase 3 Additions

```
// Protocol parsing utilities
namespace NMDCProtocol {
    %include "core/nmdc_protocol.h"
}
// Expose TagData, MyINFOData structs
%template(TagData) nVerliHub::NMDCProtocol::TagData;
%template(MyINFOData) nVerliHub::NMDCProtocol::MyINFOData;
```

### No New `.i` Files Needed

Unlike eiskaltdcpp-py (which needed ~20 new `.i` files for 24 managers),
verlihub-py has a simpler architecture — the single `verlihub_core.i` file
is sufficient because:

1. Only one main class (`HubContext`) is exposed
2. No deep manager hierarchies to wrap
3. All data types are simple structs (no `boost::intrusive_ptr`, no template types)
4. Event callbacks are handled by a single director class

The SWIG wrapper growth is additive — new methods on HubContext, new
structs for return types, new docstrings. No structural changes needed.

---

## 11. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Flood attack before G1 implemented | **High** | Hub DoS | Prioritize Phase 1; add basic IP-rate-limit NOW |
| Ban cache stale data | Medium | Banned users reconnect | Set 60s refresh timer + immediate refresh on ban add |
| Active/passive misrouting | Medium | Search results lost | Audit `$ConnectToMe`/`$RevConnectToMe` before release |
| Python callback latency for auth | Low | Slow login | Ban cache (G2) + connection pooling in SQLModel |
| SWIG ABI breakage on struct change | Low | Build failure | Pin SWIG version, test in CI |
| Legacy feature parity expectations | Medium | User complaints | Document intentional omissions, justify with modern alternatives |
| Penalty system bypass | Medium | Spammers persist | Enforce penalties in C++ rate limiter, not just Python callbacks |

---

## 12. Test Plan

### Phase 1 Tests

| Test | Type | Description |
|------|------|-------------|
| `test_flood_chat` | Integration | Send 100 chat messages in 1 second, verify rate limiting kicks in |
| `test_flood_search` | Integration | Send 50 search queries in 1 second, verify throttling |
| `test_flood_per_ip` | Integration | Connect 20 clients from same IP, verify max_conn_per_ip |
| `test_ban_cache_ip` | Unit | Load IP into ban cache, verify instant rejection |
| `test_ban_cache_nick` | Unit | Load nick into ban cache, verify rejection at ValidateNick |
| `test_ban_cache_refresh` | Integration | Add ban via API, verify cache refresh picks it up |
| `test_send_to_active` | Integration | Send message, verify only active clients receive it |
| `test_send_to_passive` | Integration | Send message, verify only passive clients receive it |
| `test_ctm_relay` | Integration | Client A sends $CTM, verify Client B receives it |
| `test_rctm_relay` | Integration | Client A sends $RCTM, verify Client B receives it |

### Phase 2 Tests

| Test | Type | Description |
|------|------|-------------|
| `test_mcto` | Integration | Send $MCTo, verify only target receives message |
| `test_ban_ip_range` | Integration | Ban CIDR range, verify all IPs in range rejected |
| `test_ban_expiry` | Integration | Set 5s ban, verify auto-removal after expiry |
| `test_penalty_chat_gag` | Integration | Gag user, verify chat messages blocked |
| `test_penalty_search_ban` | Integration | Ban search, verify $Search rejected |
| `test_userip` | Integration | Operator sends $UserIP, verify correct response |
| `test_force_move` | Integration | Force-move user, verify $ForceMove sent |
| `test_protocol_stats` | Unit | Send known messages, verify counters match |
| `test_disconnect_user` | Integration | Disconnect user, verify clean disconnection |
| `test_geoip_swig` | Unit | Look up known IP via SWIG-wrapped GeoIPLookup |

### Phase 3 Tests

| Test | Type | Description |
|------|------|-------------|
| `test_trigger_match` | Unit | Define trigger in DB, verify auto-response |
| `test_trigger_timer` | Integration | Set timer trigger, verify fires on schedule |
| `test_redirect_on_full` | Integration | Fill hub, verify new connections get $ForceMove |
| `test_client_whitelist` | Integration | Allow only specific client version, verify others rejected |
| `test_extjson` | Integration | Client sends $ExtJSON, verify parsed and stored |

---

## Appendix A: Legacy Script API → New Core Mapping

| # | Legacy Function | New Core Equivalent | Status |
|---|----------------|---------------------|--------|
| 1 | `SendDataToUser(data, nick)` | `HubContext::SendToUser(nick, msg)` | ✅ |
| 2 | `SendToClass(data, min, max)` | `HubContext::SendToClass(msg, min, max)` | ✅ |
| 3 | `SendToAll(data)` | `HubContext::SendToAll(msg)` | ✅ |
| 4 | `SendToActive(data)` | — | ❌ G3 |
| 5 | `SendToActiveClass(data, min, max)` | — | ❌ G3 |
| 6 | `SendToPassive(data)` | — | ❌ G3 |
| 7 | `SendToPassiveClass(data, min, max)` | — | ❌ G3 |
| 8 | `SendPMToAll(data, from, min, max)` | Can be composed from `SendToClass` + PM wrapping | 🔄 |
| 9 | `SendToChat(nick, text, min, max)` | `HubContext::SendToAll(NMDCProtocol::MakeChat(nick, text))` | 🔄 Compose |
| 10 | `SendToOpChat(data, nick)` | `HubContext::SendToOpChat(msg, from)` | ✅ |
| 11 | `KickUser(op, nick, why, ...)` | `HubContext::KickUser(op, nick, reason)` | ✅ (simplified) |
| 12 | `CloseConnection(nick, delay)` | `NMDCHubServer::DisconnectUser(nick)` | 🔄 Not on HubContext |
| 13 | `Ban(nick, ip, reason, type, time)` | — (Python-side DB) | 🐍 |
| 14 | `DeleteNickTempBan(nick)` | — (Python-side DB) | 🐍 |
| 15 | `DeleteIPTempBan(ip)` | — (Python-side DB) | 🐍 |
| 16 | `GetMyINFO(nick)` | `HubContext::GetUserInfo(nick, snap)` → `snap.tag` etc. | ✅ (structured, not raw) |
| 17 | `GetUserClass(nick)` | `snap.user_class` from `GetUserInfo` | ✅ |
| 18 | `GetUserHost(nick)` | — (Python `enrichment.py` does DNS) | 🐍 |
| 19 | `GetUserIP(nick)` | `snap.ip` from `GetUserInfo` | ✅ |
| 20 | `SetUserIP(nick, ip)` | — | ❌ |
| 21 | `SetMyINFOFlag(nick, flag)` | — | ❌ |
| 22 | `UnsetMyINFOFlag(nick, flag)` | — | ❌ |
| 23 | `GetUsersCount()` | `HubContext::GetUserCount()` | ✅ |
| 24 | `GetNickList()` | `HubContext::GetUserNicks()` | ✅ |
| 25 | `GetTotalShareSize()` | `HubContext::GetTotalShare()` | ✅ |
| 26 | `GetIPCC(ip)` | Python `enrichment.py` or `GeoIPLookup` | 🐍 / 🔄 |
| 27 | `GetIPCN(ip)` | Python `enrichment.py` or `GeoIPLookup` | 🐍 / 🔄 |
| 28 | `GetIPCity(ip)` | Python `enrichment.py` or `GeoIPLookup` | 🐍 / 🔄 |
| 29 | `AddRegUser(nick, class, pass, op)` | Python `RegUser` SQLModel | 🐍 |
| 30 | `DelRegUser(nick)` | Python `RegUser` SQLModel | 🐍 |
| 31 | `SetRegClass(nick, class)` | Python `RegUser` SQLModel | 🐍 |
| 32 | `CheckBotNick(nick)` | — | ❌ |
| 33 | `SetConfig(conf, var, val)` | `HubContext::SetConfig(section, key, val)` | ✅ |
| 34 | `GetConfig(conf, var, def)` | `HubContext::GetConfig(section, key, def)` | ✅ |
| 35 | `ParseCommand(nick, cmd, pm)` | — (Python LLM bot / REST API) | 🐍 |
| 36 | `ScriptCommand(cmd, data, plug, script)` | — | ❌ |
| 37 | `CheckDataPipe(data)` | — (trivial: `data.endswith('|')`) | 🚫 |
| 38 | `StopHub(code, delay)` | `HubContext::RequestShutdown(code)` | ✅ (no delay) |
| 39 | `GetVHCfgDir()` | `HubContext::GetConfigDir()` | ✅ |
| 40 | `CheckProtoFloodAll(conn, type)` | — | ❌ G1 |

### Summary

| Status | Count | % |
|--------|-------|---|
| ✅ Mapped | 16 | 40% |
| 🔄 Partial / Composable | 4 | 10% |
| 🐍 Python-side | 9 | 22.5% |
| ❌ Not ported | 8 | 20% |
| 🚫 Not needed | 3 | 7.5% |

**Script API coverage: ~72.5%** (counting ✅ + 🔄 + 🐍 as covered)

---

## Appendix B: HubConfig vs cDCConf Coverage

`HubConfig` struct currently has **~30 fields**. Legacy `cDCConf` has **190+**.

### What's Covered

| HubConfig Field | cDCConf Equivalent |
|----------------|--------------------|
| `hub_name` | `hub_name` |
| `hub_desc` | `hub_desc` |
| `hub_topic` | `hub_topic` |
| `hub_host` | `hub_host` |
| `hub_owner` | `hub_owner` |
| `hub_encoding` | `hub_encoding` |
| `hub_security` | `hub_security` |
| `opchat_name` | `opchat_name` |
| `hub_category` | `hub_category` |
| `listen_port` | (constructor param) |
| `listen_ip` | (constructor param) |
| `max_users` | `max_users_total` |
| `min_share` | `min_share` |
| `max_share` | `max_share` |
| `min_slots` | (via tag validation) |
| `max_hubs_user/op` | `tag_max_hubs` |
| `max_conn_per_ip` | `max_users_from_ip` |
| `tls_enabled/port/cert/key` | (new; legacy uses compile-time flags) |
| `use_regserver` | (via hublist_host) |
| `allow_unregistered` | (implicit in class config) |
| `require_password` | `always_ask_password` |
| `login_timeout` | `timeout_length[eTO_LOGIN]` |
| `max_pass_attempts` | (new) |
| `flood_protection` | (simplified version of 50+ flood vars) |
| `chat_filter` | (new) |
| `anti_clone` | `clone_detect_count > 0` |
| `registration_require_invite` | (new) |
| `send_user_info` | `send_user_info` |

### What's Missing (~160 config vars)

Major missing groups:
- **Flood protection fine-tuning** (50+ vars): per-message-type period/limit/action
- **Class differences** (20+ vars): `classdif_reg`, `classdif_pm`, `classdif_kick`, etc.
- **Message size limits** (15+ vars): `max_chat_msg`, `max_pm_msg`, `max_len_search`, etc.
- **Tag validation** (15+ vars): `tag_allow_unknown`, `tag_allow_passive`, `tag_min_version`, etc.
- **Nick rules** (10+ vars): `nick_chars`, `nick_prefix`, `nick_prefix_cc`, etc.
- **Search configuration** (5+ vars): `search_number`, `min_search_chars`, `int_search`, etc.
- **Share limits by class** (10+ vars): `min_share_reg`, `min_share_vip`, etc.
- **Protocol features** (10+ vars): `drop_invalid_key`, `zlib_compress_level`, etc.
- **Welcome messages** (10+ vars): `msg_welcome[class]`, per-class welcome

Most of these can be added to the YAML config and passed to C++ via the
`OnGetConfig` callback without actual C++ code changes. The C++ core only
needs to read them when processing protocol messages (which requires adding
the config reads in appropriate protocol handlers).

---

## Appendix C: Feature Comparison Matrix

```
Feature                    Legacy VH    Core C++    SWIG    Python    Status
─────────────────────────────────────────────────────────────────────────────
NMDC Protocol
  Lock/Key handshake         ✅           ✅         —       —        ✅
  $Supports                  ✅           ✅         —       —        ✅
  $ValidateNick              ✅           ✅         —       —        ✅
  $MyPass                    ✅           ✅         —       —        ✅
  $MyINFO                    ✅           ✅         —       —        ✅
  $Chat (main chat)          ✅           ✅         —       —        ✅
  $To (PM)                   ✅           ✅         —       —        ✅
  $MCTo (multi-chat)         ✅           —          —       —        ❌ G5
  $Search                    ✅           ✅         —       —        ✅
  $SR (search result)        ✅           ✅         —       —        ✅
  $ConnectToMe               ✅           🔄         —       —        🔄 G4
  $RevConnectToMe            ✅           🔄         —       —        🔄 G4
  $GetNickList               ✅           ✅         —       —        ✅
  $Quit                      ✅           ✅         —       —        ✅
  $OpForceMove               ✅           —          —       —        ❌ G9
  $Kick                      ✅           ✅         —       —        ✅
  $UserIP                    ✅           🔄         —       —        🔄 G8
  $ZOn (compression)         ✅           —          —       —        ❌ G14
  $ExtJSON                   ✅           —          —       —        ❌ G15
  $IN (status)               ✅           —          —       —        ❌ G15
  $MyHubURL                  ✅           —          —       —        ❌ G16
  $BotINFO                   ✅           —          —       —        ❌

Hub Control
  Start/Stop                 ✅           ✅         ✅      ✅       ✅
  Signal handling            ✅           ✅         ✅      ✅       ✅
  Config reload              ✅           ✅         ✅      ✅       ✅
  Topic set                  ✅           ✅         ✅      ✅       ✅
  MOTD                       ✅           ✅         ✅      ✅       ✅

User Management
  User count                 ✅           ✅         ✅      ✅       ✅
  User list                  ✅           ✅         ✅      ✅       ✅
  User info query            ✅           ✅         ✅      ✅       ✅
  Kick                       ✅           ✅         ✅      ✅       ✅
  Force move                 ✅           —          —       —        ❌ G9
  Clone detection            ✅           —          —       —        ❌
  Active/passive tracking    ✅           🔄         —       —        🔄 G3

Messaging
  Send to user               ✅           ✅         ✅      ✅       ✅
  Send to all                ✅           ✅         ✅      ✅       ✅
  Send to class range        ✅           ✅         ✅      ✅       ✅
  Send to active/passive     ✅           —          —       —        ❌ G3
  Send to op chat            ✅           ✅         ✅      ✅       ✅
  Send PM to all             ✅           —          —       🔄      🔄
  Send by country            ✅           —          —       —        ❌

Ban System
  Nick ban                   ✅           —          —       🔄      🔄 G6
  IP ban                     ✅           —          —       🔄      🔄 G6
  IP range ban               ✅           —          —       —        ❌ G6
  Host pattern ban           ✅           —          —       —        ❌ G6
  Share size ban             ✅           —          —       —        ❌ G6
  Temporary bans             ✅           —          —       —        ❌ G6
  Ban cache (fast reject)    —            —          —       —        ❌ G2

Registration
  Add/Del/Modify             ✅           —          —       ✅      ✅ 🐍
  Password hashing           ✅ (MD5)     —          —       ✅(bcrypt) ✅ 🐍

Configuration
  190+ vars runtime          ✅           🔄(~30)    ✅      ✅       🔄
  YAML-based config          —            —          —       ✅       ✅ (new)

Plugins
  Load/Unload/Reload         ✅           ✅         ✅      ✅       ✅
  Lua scripts                ✅           ✅         ✅      ✅       ✅
  Python scripts             ✅           ✅         ✅      ✅       ✅

GeoIP
  Country/City lookup        ✅           ✅         —       ✅       ✅

Flood Protection             ✅           —          —       —        ❌ G1

Penalties                    ✅           —          —       —        ❌ G7

Triggers                     ✅           —          —       —        ❌ G11

Redirects                    ✅           —          —       —        ❌ G12

Client Detection             ✅           🔄(parse)  —       —        🔄 G13

Statistics/Info              ✅           🔄         —       ✅       🔄 G10

REST API                     —            —          —       ✅       ✅ (new)
Web Dashboard                —            —          —       ✅       ✅ (new)
LLM Bot                      —            —          —       ✅       ✅ (new)
Async Multi-DB               —            —          —       ✅       ✅ (new)
```

---

## Phase 4: Web Dashboard Integration (Post-Implementation)

> **NOTE**: After all SWIG-wrapped features are implemented and working (Phases 1–3),
> the new capabilities must be exposed through the REST API and surfaced in the
> verlihub web dashboard. This includes but is not limited to:
>
> - **ForceMove / Redirect**: UI for redirecting users to another hub
> - **Protocol Statistics**: Dashboard widget showing message counters (chat, PM, search, CTM, SR, MCTo, flood/ban blocked)
> - **GeoIP Lookup**: Expose per-user country/city in user list + standalone IP lookup endpoint
> - **Ban Management**: CRUD for IP range / CIDR bans, penalty entries; display active bans with expiry
> - **Penalty System**: UI for applying/viewing/lifting temporary restrictions (chat gag, search ban, PM ban)
> - **Flood Protection Config**: UI for tuning per-type rate limits (period, burst)
> - **$MCTo**: No direct dashboard action needed (protocol-only), but MCTo stats visible in protocol stats widget
> - **$UserIP / $WhoIP**: Operator IP lookup via dashboard (calls the same SWIG-backed method)
> - **Trigger System**: CRUD for custom triggers/commands
> - **Client Detection Rules**: CRUD for allowed/banned DC clients
> - **Redirect Rules**: CRUD for custom redirect addresses
>
> This work is tracked separately and should begin once the C++/SWIG/Python core
> is stable and all Phase 2 + Phase 3 items pass build/import tests.

---

## Phase 5: MCP & LLM Chatbot Full SWIG Integration

> **Goal**: Make the totality of SWIG-wrapped verlihub C++ functionality
> accessible from both the MCP protocol servers (in-process + standalone)
> and the embedded LLM chatbot (dashboard chat + NMDC bot). After this
> phase, every HubContext method reachable from Python has a corresponding
> MCP tool and LLM function-call definition.

### Current State (Pre-Phase 5)

The MCP servers and LLM gateway currently expose **14 read-only tools** and
**5 admin tools**, covering basic hub queries (info, users, stats, bans,
geo) and simple actions (kick, broadcast, PM, ban, hub command). However,
the majority of SWIG-exposed C++ APIs introduced in Phases 1–3 have **no
MCP tool or LLM function-call equivalent**.

### Gap Matrix: SWIG API → MCP/LLM Tool Coverage

| SWIG API | Category | MCP Tool | LLM Tool | Status |
|---|---|---|---|---|
| `SendToUser` | Messaging | `send_message_to_user` | `send_message_to_user` | ✅ Covered |
| `SendToAll` | Messaging | `send_broadcast` | `send_broadcast` | ✅ Covered |
| `KickUser` | Admin | `kick_user` | `kick_user` | ✅ Covered |
| `GetHubName/Topic` | Info | `get_hub_info` | `get_hub_info` | ✅ Covered |
| `GetUserCount` | Info | `get_hub_info` | `get_hub_statistics` | ✅ Covered |
| `GetTotalShare` | Info | `get_share_statistics` | `get_share_statistics` | ✅ Covered |
| `GetConfig/SetConfig` | Config | — | `get_hub_config`/`set_hub_config` | ⚠️ LLM only |
| `SetHubTopic` | Config | — | `set_topic` | ⚠️ LLM only |
| `SetMOTD` | Config | — | `set_motd` | ⚠️ LLM only |
| `SendToOpChat` | Messaging | — | — | ❌ Not exposed |
| `SendToClass` | Messaging | — | — | ❌ Not exposed |
| `SendToActive/Passive` | Messaging | — | — | ❌ Not exposed |
| `SendToActiveClass/PassiveClass` | Messaging | — | — | ❌ Not exposed |
| `BroadcastChat` | Messaging | — | — | ❌ Not exposed |
| `SendPM` (as-nick) | Messaging | — | — | ❌ Not exposed |
| `ForceMove` | Admin | — | — | ❌ Not exposed |
| `DisconnectUser` | Admin | — | — | ❌ Not exposed |
| `AddRobot/RemoveRobot` | Bot mgmt | — | — | ❌ Not exposed |
| `GetProtocolStats` | Stats | — | — | ❌ Not exposed |
| `LookupGeoIP` (per-IP) | Stats | — | — | ❌ Not exposed |
| `GetActiveUserCount/PassiveUserCount` | Stats | — | — | ❌ Not exposed |
| `LoadPlugin/UnloadPlugin/ReloadPlugin` | Plugins | — | — | ❌ Not exposed |
| `GetLoadedPlugins/IsPluginLoaded` | Plugins | — | — | ❌ Not exposed |
| `ExecuteLuaScript/UnloadLuaScript` | Scripts | — | — | ❌ Not exposed |
| `ExecutePythonScript/UnloadPythonScript` | Scripts | — | — | ❌ Not exposed |
| `GetLoadedLuaScripts/PythonScripts` | Scripts | — | — | ❌ Not exposed |
| `SetFloodConfig` | Flood | — | — | ❌ Not exposed |
| `LoadBanCache/AddBanCacheIP/Nick` | Ban cache | — | — | ❌ Not exposed |
| `ClearBanCache` | Ban cache | — | — | ❌ Not exposed |
| `RequestReload` | Lifecycle | — | — | ❌ Not exposed |

### Phase 5 Work Items

#### 5.1: New MCP + LLM Tools — Messaging (~120 LOC Python)

Add to both in-process MCP (`api/routes/mcp.py`) and LLM gateway (`api/routes/llm.py`):

| Tool Name | SWIG Method | Permission | Parameters |
|---|---|---|---|
| `send_to_opchat` | `SendToOpChat(msg, from)` | Admin | `message`, `from_nick?` |
| `send_to_class` | `SendToClass(msg, min, max)` | Admin | `message`, `min_class`, `max_class` |
| `send_to_active` | `SendToActive(msg)` | Admin | `message` |
| `send_to_passive` | `SendToPassive(msg)` | Admin | `message` |
| `broadcast_chat` | `BroadcastChat(from, msg)` | Admin | `from_nick`, `message` |
| `send_pm_as` | `SendPM(from, to, msg)` | Admin | `from_nick`, `to_nick`, `message` |

#### 5.2: New MCP + LLM Tools — Administration (~100 LOC Python)

| Tool Name | SWIG Method | Permission | Parameters |
|---|---|---|---|
| `force_move` | `ForceMove(nick, addr)` | Admin | `nick`, `address` |
| `disconnect_user` | `DisconnectUser(nick)` | Admin | `nick` |
| `add_robot` | `AddRobot(nick, desc, class)` | Admin | `nick`, `description`, `user_class` |
| `remove_robot` | `RemoveRobot(nick)` | Admin | `nick` |
| `set_hub_topic` | `SetHubTopic(topic)` | Admin | `topic` |
| `set_motd` | `SetMOTD(motd)` | Admin | `motd` |
| `get_hub_config` | `GetConfig(s, k, d)` | Operator | `section`, `key` |
| `set_hub_config` | `SetConfig(s, k, v)` | Admin | `section`, `key`, `value` |
| `reload_config` | `RequestReload()` | Admin | (none) |

#### 5.3: New MCP + LLM Tools — Statistics & GeoIP (~80 LOC Python)

| Tool Name | SWIG Method | Permission | Parameters |
|---|---|---|---|
| `get_protocol_stats` | `GetProtocolStats()` | Operator | (none) |
| `lookup_geoip` | `LookupGeoIP(ip)` | Operator | `ip` |
| `get_active_passive_counts` | `GetActiveUserCount()`/`GetPassiveUserCount()` | Operator | (none) |

#### 5.4: New MCP + LLM Tools — Plugin & Script Management (~150 LOC Python)

| Tool Name | SWIG Method | Permission | Parameters |
|---|---|---|---|
| `list_plugins` | `GetLoadedPlugins()` | Operator | (none) |
| `load_plugin` | `LoadPlugin(path)` | Admin | `plugin_path` |
| `unload_plugin` | `UnloadPlugin(name)` | Admin | `plugin_name` |
| `reload_plugin` | `ReloadPlugin(name)` | Admin | `plugin_name` |
| `list_lua_scripts` | `GetLoadedLuaScripts()` | Operator | (none) |
| `load_lua_script` | `ExecuteLuaScript(path)` | Admin | `script_path` |
| `unload_lua_script` | `UnloadLuaScript(path)` | Admin | `script_path` |
| `list_python_scripts` | `GetLoadedPythonScripts()` | Operator | (none) |
| `load_python_script` | `ExecutePythonScript(path)` | Admin | `script_path` |
| `unload_python_script` | `UnloadPythonScript(path)` | Admin | `script_path` |

#### 5.5: New MCP + LLM Tools — Flood & Ban Cache (~80 LOC Python)

| Tool Name | SWIG Method | Permission | Parameters |
|---|---|---|---|
| `set_flood_config` | `SetFloodConfig(type, period, tokens)` | Admin | `flood_type`, `period_ms`, `max_tokens` |
| `sync_ban_cache` | `LoadBanCache(ips, nicks)` | Admin | (none — loads from DB) |
| `add_ban_cache_ip` | `AddBanCacheIP(ip)` | Admin | `ip` |
| `add_ban_cache_nick` | `AddBanCacheNick(nick)` | Admin | `nick` |
| `clear_ban_cache` | `ClearBanCache()` | Admin | (none) |

#### 5.6: New MCP + LLM Tools — Penalty Management (~80 LOC Python)

| Tool Name | SWIG Method / Service | Permission | Parameters |
|---|---|---|---|
| `list_penalties` | `penalty_service.get_active_penalties()` | Operator | `nick?` |
| `add_penalty` | `penalty_service.add_penalty()` | Admin | `nick`, `penalty_type`, `reason`, `duration_minutes?` |
| `remove_penalty` | `penalty_service.remove_penalty()` | Admin | `nick`, `penalty_type?` |
| `cleanup_penalties` | `penalty_service.cleanup_expired()` | Admin | (none) |

#### 5.7: New MCP + LLM Tools — Triggers & Redirects (~80 LOC Python)

| Tool Name | Service | Permission | Parameters |
|---|---|---|---|
| `list_triggers` | `trigger_service` | Operator | (none) |
| `add_trigger` | `trigger_service` | Admin | `command`, `response`, `min_class?` |
| `remove_trigger` | `trigger_service` | Admin | `trigger_id` |
| `list_redirects` | `redirect_service` | Operator | (none) |
| `add_redirect` | `redirect_service` | Admin | `address`, `trigger_type`, `enabled?` |
| `remove_redirect` | `redirect_service` | Admin | `redirect_id` |

#### 5.8: New MCP Resources (~40 LOC Python)

| URI | Name | Description |
|---|---|---|
| `hub://plugins` | Loaded Plugins | List of loaded plugins and scripts |
| `hub://penalties` | Active Penalties | Current penalty restrictions by user |
| `hub://protocol_stats` | Protocol Statistics | Message counters and throughput |
| `hub://triggers` | Triggers | Configured auto-response triggers |
| `hub://flood_config` | Flood Config | Current flood protection settings |

#### 5.9: New MCP Prompts (~30 LOC Python)

| Prompt | Description | Arguments |
|---|---|---|
| `security_audit` | Analyze flood stats, ban cache, active penalties for threats | (none) |
| `plugin_status` | Report on loaded plugins, scripts, and their health | (none) |
| `traffic_analysis` | Analyze protocol stats for anomalies (flood, spam patterns) | (none) |

#### 5.10: Standalone MCP Server Parity (`client/mcp.py`) (~200 LOC Python)

Update the standalone MCP server (`verlihub-mcp serve`) to mirror all new
tools from 5.1–5.7, making the same calls via `AsyncHubClient` REST endpoints.
This requires corresponding REST API endpoints for each new tool (many already
exist in the routes; others need thin wrappers).

#### 5.11: Bot Chat Tool Integration (~60 LOC Python)

Update `bot_chat.py` to include the new tools in the bot's LLM function-call
definitions based on user class:

- **Operators** (class 3+): all read-only + stats + plugins list + penalties list + triggers list
- **Admins** (class 5+): all above + all write tools (kick, ban, force-move, flood config, plugin manage, penalty manage, trigger manage)

### Phase 5 Summary

| Item | Type | Est. LOC | Files Modified |
|---|---|---|---|
| 5.1 Messaging tools | Python | 120 | `mcp.py`, `llm.py` |
| 5.2 Admin tools | Python | 100 | `mcp.py`, `llm.py` |
| 5.3 Stats/GeoIP tools | Python | 80 | `mcp.py`, `llm.py` |
| 5.4 Plugin/Script tools | Python | 150 | `mcp.py`, `llm.py` |
| 5.5 Flood/Ban cache tools | Python | 80 | `mcp.py`, `llm.py` |
| 5.6 Penalty tools | Python | 80 | `mcp.py`, `llm.py` |
| 5.7 Trigger/Redirect tools | Python | 80 | `mcp.py`, `llm.py` |
| 5.8 New MCP resources | Python | 40 | `mcp.py`, `client/mcp.py` |
| 5.9 New MCP prompts | Python | 30 | `mcp.py`, `client/mcp.py` |
| 5.10 Standalone MCP parity | Python | 200 | `client/mcp.py` |
| 5.11 Bot chat tool integration | Python | 60 | `bot_chat.py` |
| **Phase 5 Total** | | **~1,020** | |

### Post-Phase 5: Complete Tool Inventory

After Phase 5, the MCP and LLM gateways will expose:

- **Read-only tools**: 14 existing + 3 new stats + 5 new list tools = **22 tools**
- **Admin tools**: 5 existing + 6 messaging + 9 admin + 5 flood/ban + 4 penalty + 3 trigger/redirect + 10 plugin/script = **42 tools**
- **Resources**: 4 existing + 5 new = **9 resources**
- **Prompts**: 3 existing + 3 new = **6 prompts**
- **Total tool coverage**: 100% of SWIG-exposed HubContext methods accessible via MCP

### Intentionally Excluded from MCP/LLM

| API | Reason |
|---|---|
| `RequestShutdown(signal)` | Too dangerous for AI-initiated shutdown — use CLI/dashboard only |
| `HasPendingShutdown/Reload` | Internal state polling — no value as a tool |
| `GetShutdownSignal` | Internal |
| `GetConfigDir` | Filesystem path — security risk to expose |
| `Initialize/Start/Stop` | Hub lifecycle — managed by server process, not tools |
| `SetEventCallback` | Internal wiring, not an operational tool |

### Dependencies

Phase 5 requires all of Phases 1–4 to be complete:
- Phase 1 (flood, ban cache, active/passive) → tools 5.5
- Phase 2 (MCTo, ForceMove, stats, GeoIP, penalties) → tools 5.2, 5.3, 5.6
- Phase 3 (triggers, redirects, client detection) → tools 5.7
- Phase 4 (REST API endpoints) → item 5.10 (standalone MCP needs REST endpoints)

---

## Implementation Progress

### Phase Status Summary

| Phase | Description | Status | Commit | Date |
|---|---|---|---|---|
| **Phase 1** | Security & Correctness (P0) — Flood protection, ban cache, active/passive messaging, CTM/RCTM hardening | ✅ **COMPLETE** | `be0cb63` | 2026-03 |
| **Phase 2** | Administrative Features (P1) — MCTo, UserIP/WhoIP, ForceMove, Protocol stats, GeoIP SWIG, penalties | ✅ **COMPLETE** | `672a51c` | 2026-03 |
| **Phase 3** | Completeness (P2) — ExtJSON/IN/MyHubURL handlers, ZLib compression, triggers, redirects, client detection, chat rooms  | ✅ **COMPLETE** | `2b3731e` | 2026-03 |
| **Phase 4** | Web Dashboard Integration — REST API endpoints, dashboard templates, flood config UI, protocol stats UI | ✅ **COMPLETE** | `8219f6d` | 2026-03 |
| **Phase 5** | MCP & LLM Chatbot Full SWIG Integration — new tools, resources, prompts, standalone parity, bot chat integration | ✅ **COMPLETE** | `84da020` | 2026-03 |
| **Phase 5+** | Gap fix: add `send_to_active_class`/`send_to_passive_class` across all layers (core, MCP, LLM, REST, client) + bot module refactor | ✅ **COMPLETE** | — | 2026-03 |

### Test Coverage

| Test File | Tests | Covers |
|---|---|---|
| `test_phase3_services.py` | 49 | TriggerCache, RedirectCache, ClientDetectionCache, ChatRoom, ChatRoomManager |
| `test_phase4_api.py` | 21 | ForceMove, ProtocolStats, GeoIP, WhoIP, FloodConfig, OpChat, Disconnect API endpoints |
| `test_core_wrapper.py` | 49 | HubEventHandler (13 event types incl. ExtJSON/MyHubURL/UserINUpdate), HubContext wrapper methods (force_move, disconnect_user, send_to_opchat, get_protocol_stats, lookup_geoip, set/get_flood_config, send_pm_as, send_chat_as), lifecycle, signals |
| `test_dashboard_extended.py` | 49 | All dashboard routes (14 unauthenticated redirect tests incl. triggers/redirects/clients/penalties/flood-config/protocol-stats, 16 authenticated page tests) |
| `test_llm_integration.py` | ~157 | MCP tools (40+), LLM tools (58 total: 18 readonly + 40 admin incl. send_to_active_class/send_to_passive_class), _execute_tool handlers (Phase 5 messaging/admin/stats/plugins/flood/penalties/triggers/redirects), prompts, resources, auth middleware, chat sessions, action catalog |
| **Total (all test files)** | **~1,750** | Full regression suite (1,750 passed, 46 skipped, 0 failures) |

### Phase 5 Detailed Deliverables

| Component | File | Changes |
|---|---|---|
| Core wrappers | `core.py` | 24 new methods: send_to_active/passive, send_to_active_class/passive_class, broadcast_chat, add/remove_robot, get_active/passive_user_count, load/unload/reload_plugin, get_loaded_plugins, is_plugin_loaded, execute/unload_lua_script, get_loaded_lua_scripts, execute/unload_python_script, get_loaded_python_scripts, load_ban_cache, add_ban_cache_ip/nick, clear_ban_cache, request_reload |
| In-process MCP | `api/routes/mcp.py` | 40+ new tools, 5 new resources (hub://plugins, penalties, protocol_stats, triggers, flood_config), 3 new prompts (security_audit, plugin_status, traffic_analysis) |
| LLM gateway | `api/routes/llm.py` | 18 readonly + 40 admin tool definitions, _execute_tool handlers for all Phase 5 tools, expanded action catalog |
| REST APIs | `api/routes/hub.py` | New endpoints: send-to-active, send-to-passive, send-to-active-class, send-to-passive-class, broadcast-chat, robot CRUD, active-passive-counts, plugins CRUD, lua/python-scripts CRUD, ban-cache (sync/add-ip/add-nick/clear) |
| REST client | `client/api.py` | 30+ new AsyncHubClient methods mirroring all Phase 5 REST endpoints |
| Standalone MCP | `client/mcp.py` | 30+ new tools, 5 new resources, 3 new prompts, all dispatch handlers |
| Bot chat | `bot_chat.py` | Automatic inheritance via llm.py's _build_*_tools() and _execute_tool() |

### Git Branch

- Branch: `verlihub-py-llm`
- All phases complete. Bot modules refactored into `verlihub.bot` package.
