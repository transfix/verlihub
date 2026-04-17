# verlihub-py SWIG Wrapper Implementation Progress

Tracking progress against [FULL_SWIG_WRAP_PLAN.md](FULL_SWIG_WRAP_PLAN.md).

## Phase 1: Security & Correctness (P0) — COMPLETE

### 1.1 Token-bucket rate limiter in NMDCHubServer (G1) ✅

**Files modified:**
- `src/core/nmdc_hub_server.h` — Added `FloodType` enum, `FloodLimit` struct,
  `FloodBucket`/`FloodState` structs, `FloodState flood` field on `NMDCClient`,
  `CheckFlood()` private method, `SetFloodConfig()`/`GetFloodConfig()` public API,
  flood config array `m_flood_limits` with defaults, `m_max_flood_warnings`
- `src/core/nmdc_hub_server.cpp` — Implemented `CheckFlood()` (token-bucket with
  refill, warning escalation, auto-disconnect), integrated into `OnNewMessage()`
  dispatch for Chat, PM, Search, MyINFO, CTM/RCTM message types

**Design:**
- Token-bucket algorithm: each flood type has configurable `period_ms` and `max_tokens`
- Tokens refill based on elapsed time; one token consumed per allowed message
- Operators (class >= 3) are exempt from flood checks
- After `m_max_flood_warnings` (default 3) consecutive flood hits, client is disconnected
- Defaults: Chat 5/1s, PM 5/1s, Search 5/5s, MyINFO 2/5s, CTM 10/1s

### 1.2 Per-client flood state tracking (G1) ✅

- `FloodState` initialized on connect in `OnNewConn()` with current limits
- Warning counter resets on successful (non-flooded) messages
- Flood state is per-connection, automatically cleaned up on disconnect

### 1.3 Expose `SetFloodConfig()` on HubContext + SWIG (G1) ✅

**Files modified:**
- `src/core/hub_context.h` — Added `SetFloodConfig(int type, int period_ms, int max_tokens)`
- `src/core/hub_context.cpp` — Implemented proxy to `NMDCHubServer::SetFloodConfig()`
- `src/swig/verlihub_core.i` — Added docstring for `SetFloodConfig`

**Python usage:**
```python
ctx.SetFloodConfig(0, 2000, 3)  # Chat: 3 msgs per 2 seconds
ctx.SetFloodConfig(2, 10000, 3)  # Search: 3 per 10 seconds
```

### 1.4 Ban cache in NMDCHubServer (G2) ✅

**Files modified:**
- `src/core/nmdc_hub_server.h` — Added `m_banned_ips`, `m_banned_nicks`
  (`unordered_set<string>`), `m_ban_cache_mutex`, public API methods
  (`LoadBanCache`, `AddBanCacheIP/Nick`, `RemoveBanCacheIP/Nick`, `ClearBanCache`),
  private `IsIPBanned()`/`IsNickBanned()` helpers
- `src/core/nmdc_hub_server.cpp` — Implemented all ban cache methods, integrated
  IP check in `OnNewConn()` and nick check in `HandleValidateNick()`

**Design:**
- Fast-path rejection: banned IPs rejected at TCP accept (before handshake)
- Banned nicks rejected at `$ValidateNick` (before password check)
- Python remains authoritative; cache is a fast complement
- Separate mutex from client maps to minimize contention

### 1.5 `LoadBanCache()` / `RefreshBanCache()` on HubContext + SWIG (G2) ✅

**Files modified:**
- `src/core/hub_context.h` — Added `LoadBanCache`, `AddBanCacheIP/Nick`,
  `RemoveBanCacheIP/Nick`, `ClearBanCache`
- `src/core/hub_context.cpp` — Implemented proxies
- `src/swig/verlihub_core.i` — Added docstrings for all 6 ban cache methods

**Python usage:**
```python
ctx.LoadBanCache(["1.2.3.4", "5.6.7.8"], ["spammer", "troll"])
ctx.AddBanCacheIP("10.0.0.1")
ctx.RemoveBanCacheNick("reformed_user")
ctx.ClearBanCache()
```

### 1.6 Active/passive user tracking + `SendToActive/Passive` (G3) ✅

**Files modified:**
- `src/core/nmdc_hub_server.h` — Added `SendToActive()`, `SendToPassive()`,
  `SendToActiveClass()`, `SendToPassiveClass()`, `GetActiveUserCount()`,
  `GetPassiveUserCount()`, private `SendToConnsFiltered()` helper
- `src/core/nmdc_hub_server.cpp` — Implemented all active/passive methods
  using mode field from parsed tag data ('A'=active, 'P'=passive)
- `src/core/hub_context.h` — Added HubContext proxy declarations
- `src/core/hub_context.cpp` — Implemented HubContext proxies
- `src/swig/verlihub_core.i` — Added docstrings + Python properties
  `active_user_count`, `passive_user_count`

**Design:**
- Uses existing `client.mode` field (parsed from `$MyINFO` tag on login)
- `SendToConnsFiltered()` is the shared inner loop for mode+class filtering
- No separate collections needed — filtering is done at send time

### 1.7 Verify `$ConnectToMe` / `$RevConnectToMe` relay (G4) ✅

**Files modified:**
- `src/core/nmdc_hub_server.cpp` — Added IP validation to `HandleConnectToMe()`
  (claimed IP must match sender's real IP), added sender nick verification to
  `HandleRevConnectToMe()` (sender_nick must match connection's nick)

**Security hardening:**
- `$ConnectToMe`: prevents connection spoofing by validating the IP:port address
  in the CTM message matches the sender's actual TCP connection IP
- `$RevConnectToMe`: verifies sender_nick field matches the authenticated nick
  to prevent impersonation

## Phase 2: Administrative Features (P1) — NOT STARTED

| # | Work Item | Status |
|---|-----------|--------|
| 2.1 | `$MCTo` handler | ❌ |
| 2.2 | `BanEntry` SQLModel with IP ranges | ❌ |
| 2.3 | Penalty system | ❌ |
| 2.4 | `$UserIP` / `$WhoIP` handlers | ❌ |
| 2.5 | `$OpForceMove` + `ForceMove()` | ❌ |
| 2.6 | Protocol stats + `GetProtocolStats()` | ❌ |
| 2.7 | `DisconnectUser()` on HubContext | ✅ (done in Phase 1) |
| 2.8 | `SendPM()` on HubContext | ✅ (done in Phase 1) |
| 2.9 | GeoIP via SWIG | ❌ |
| 2.10 | `BroadcastChat()` on HubContext | ✅ (done in Phase 1) |

## Phase 3: Completeness (P2) — NOT STARTED

---

## Summary

| Phase | Items | Done | LOC (est.) |
|-------|-------|------|------------|
| Phase 1 (P0) | 7 | 7 | ~800 |
| Phase 2 (P1) | 10 | 3* | ~600 remaining |
| Phase 3 (P2) | 7 | 0 | ~1,070 |

*Items 2.7, 2.8, 2.10 were completed ahead of schedule during Phase 1.

### New Methods Exposed via SWIG (Phase 1)

| Method | Category |
|--------|----------|
| `SetFloodConfig(type, period_ms, max_tokens)` | Flood protection |
| `LoadBanCache(ips, nicks)` | Ban cache |
| `AddBanCacheIP(ip)` | Ban cache |
| `AddBanCacheNick(nick)` | Ban cache |
| `RemoveBanCacheIP(ip)` | Ban cache |
| `RemoveBanCacheNick(nick)` | Ban cache |
| `ClearBanCache()` | Ban cache |
| `SendToActive(msg)` | Active/passive messaging |
| `SendToPassive(msg)` | Active/passive messaging |
| `SendToActiveClass(msg, min, max)` | Active/passive messaging |
| `SendToPassiveClass(msg, min, max)` | Active/passive messaging |
| `GetActiveUserCount()` | Active/passive info |
| `GetPassiveUserCount()` | Active/passive info |
| `DisconnectUser(nick)` | User management |
| `SendPM(from, to, msg)` | Messaging |
| `BroadcastChat(from, msg)` | Messaging |
