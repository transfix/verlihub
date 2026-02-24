# NMDCpb Protocol Workflows

> Wire-level protocol documentation for the NMDCpb extension, including HubRelay
> transfers and End-to-End Encrypted Private Messages (E2EPM).
>
> Covers both implementation stacks:
> - **verlihub** (C++ hub + Python scripting)
> - **eiskaltdcpp** (C++ client library) + **eiskaltdcpp-py** (Python bindings)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [NMDCpb Core Protocol](#2-nmdcpb-core-protocol)
3. [Protobuf Envelope Format](#3-protobuf-envelope-format)
4. [E2EPM — End-to-End Encrypted Private Messages](#4-e2epm--end-to-end-encrypted-private-messages)
5. [HubRelay — Hub-Relayed Encrypted Transfers](#5-hubrelay--hub-relayed-encrypted-transfers)
6. [PrivateSearch — Targeted User Search](#6-privatesearch--targeted-user-search)
7. [Implementation Map](#7-implementation-map)

---

## 1. Architecture Overview

### Component Stack

```
┌──────────────────────────────────────────────────────┐
│                   eiskaltdcpp-qt                     │  Qt5/6 GUI
├──────────────────────────────────────────────────────┤
│              eiskaltdcpp-py (SWIG bridge)            │  Python ↔ C++
├──────────────────────────────────────────────────────┤
│    dcpp library (NmdcHub, E2EPMManager, Relay, ...)  │  C++ core
└───────────────────┬──────────────────────────────────┘
                    │ NMDC + NMDCpb protocol
                    ▼
┌──────────────────────────────────────────────────────┐
│          verlihub (cServerDC, cDCProto, ...)          │  C++ hub
├──────────────────────────────────────────────────────┤
│    Python scripting: hub_api, relay, dashboard        │  Python plugins
└──────────────────────────────────────────────────────┘
```

### Protocol Layer

All NMDCpb messages are transported inside standard NMDC commands:

| Command | Direction | Description |
|---------|-----------|-------------|
| `$PB <nick> <base64>`  | Hub broadcast | Protobuf message from `<nick>` to all |
| `$PBB <nick> <base64>` | Hub → clients | Hub-originated broadcast |
| `$PBR <to> <from> <base64>` | Routed | Private protobuf message `from` → `to` |

The `<base64>` payload is a serialized `PbEnvelope` protobuf message, encoded with base64url (RFC 4648 §5, no padding).

---

## 2. NMDCpb Core Protocol

### 2.1 Feature Negotiation

```
Client → Hub:   $Supports NMDCpb HubRelay ...
Hub → Client:   $Supports NMDCpb HubRelay ...
```

Both sides announce capabilities in `$Supports`. The hub echoes back the features it supports.

### 2.2 Chat Message Flow (Protobuf)

**Sending a public chat message:**

```
Client A                        Hub                         Client B
   │                             │                             │
   │ $PB AliceNick <base64>      │                             │
   │────────────────────────────>│                             │
   │                             │ NMDCpb clients:             │
   │                             │ $PB AliceNick <base64>      │
   │                             │────────────────────────────>│
   │                             │                             │
   │                             │ Legacy clients:             │
   │                             │ <AliceNick> Hello!          │
   │                             │─────────────────>(legacy)   │
```

**verlihub implementation:**
- `cDCProto::DC_PB()` parses the envelope, checks `PbChat` type
- Calls `cPbTranslate::pbChatToLegacy()` to generate `<nick> text` for legacy clients
- Calls `cUserCollection::SendToAllWithFeature(eSF_NMDCPB, ...)` for protobuf clients
- Calls `cUserCollection::SendToAllWithoutFeature(eSF_NMDCPB, ...)` for legacy text

**eiskaltdcpp implementation:**
- `NmdcHub::sendPbEnvelope()` serializes → base64url → `$PB nick data|`
- `NmdcHub::onLine()` routes `$PB`/`$PBB`/`$PBR` to `handlePbCommand()`

### 2.3 Private Message Flow (Protobuf, unencrypted)

```
Client A                        Hub                         Client B
   │                             │                             │
   │ $PBR BobNick AliceNick b64  │                             │
   │────────────────────────────>│                             │
   │                             │ $PBR BobNick AliceNick b64  │
   │                             │────────────────────────────>│
```

The hub forwards `$PBR` to the target user. Non-E2EPM private messages use `PbChat` with `is_pm=true` inside the envelope.

---

## 3. Protobuf Envelope Format

Every NMDCpb payload is a `PbEnvelope`:

```protobuf
message PbEnvelope {
    enum Route { BROADCAST = 0; DIRECT = 1; HUB_ORIGINATED = 2; }
    Route route = 1;
    string from_nick = 2;
    string to_nick = 3;           // only for DIRECT
    uint64 timestamp = 4;
    uint32 sequence = 5;

    oneof payload {
        PbChat chat = 10;
        PbUserInfo user_info = 11;
        PbSearch search = 12;
        PbSearchResult search_result = 13;
        PbPMKeyExchange pm_key_exchange = 20;
        PbEncryptedPM encrypted_pm = 21;
        PbRelayRequest relay_request = 22;
        PbRelayAck relay_ack = 23;
        PbRelayData relay_data = 24;
        PbRelayClosed relay_closed = 25;
        PbPrivateSearch private_search = 60;
        PbPrivateSearchResult private_search_result = 61;
    }
}
```

---

## 4. E2EPM — End-to-End Encrypted Private Messages

### 4.1 Cryptographic Primitives

| Primitive | Algorithm | Purpose |
|-----------|-----------|---------|
| Key agreement | X25519 (Curve25519 DH) | Ephemeral session key exchange |
| Key derivation | HKDF-SHA256 | Derive enc/dec keys from shared secret |
| Encryption | ChaCha20-Poly1305 (AEAD) | Message encryption + authentication |
| Fingerprint | Emoji hash of sorted public keys | Visual key verification |

### 4.2 Key Exchange Sequence

```
Client A (Alice)                    Hub                    Client B (Bob)
   │                                 │                         │
   │  Generate X25519 keypair (a)    │                         │
   │                                 │                         │
   │  $PBR Bob Alice [PbPMKeyExchange(pub_a)]                  │
   │────────────────────────────────>│                         │
   │                                 │  $PBR Bob Alice [kex]   │
   │                                 │────────────────────────>│
   │                                 │                         │
   │                                 │  Generate keypair (b)   │
   │                                 │  Derive shared secret   │
   │                                 │    S = X25519(b, pub_a) │
   │                                 │  HKDF → enc_key, dec_key│
   │                                 │  TOFU: check/store key  │
   │                                 │                         │
   │  $PBR Alice Bob [PbPMKeyExchange(pub_b)]                  │
   │<────────────────────────────────│                         │
   │                                 │<────────────────────────│
   │  Derive shared secret           │                         │
   │    S = X25519(a, pub_b)         │                         │
   │  HKDF → enc_key, dec_key        │                         │
   │  TOFU: check/store key          │                         │
   │                                 │                         │
   │  ═══ E2EPM Session Established ═══                        │
   │  Fingerprint: 🔐🎯🌟🎵🔑🎨   │                         │
```

**Key derivation details:**
```
shared_secret = X25519(our_priv, peer_pub)
salt = "nmdcpb-e2epm-v1"
info = sort(pub_a, pub_b)   // deterministic ordering
enc_key = HKDF-SHA256(shared_secret, salt, info + "enc")  → 32 bytes
dec_key = HKDF-SHA256(shared_secret, salt, info + "dec")  → 32 bytes
```

The `enc`/`dec` role assignment is deterministic: the side with the lexicographically smaller public key uses `enc_key` for sending, `dec_key` for receiving. The other side uses the reverse.

### 4.3 Encrypted PM Exchange

```
Alice                               Hub                         Bob
  │                                  │                            │
  │  Serialize PbPMPlaintext(text)   │                            │
  │  nonce = counter++               │                            │
  │  AAD = "e2epm\0" + hub + "\0"    │                            │
  │        + peer_nick               │                            │
  │  ct = ChaCha20-Poly1305(         │                            │
  │       enc_key, nonce, AAD, pt)   │                            │
  │                                  │                            │
  │  $PBR Bob Alice                  │                            │
  │   [PbEncryptedPM(ct, nonce,      │                            │
  │    sender_pubkey_hint)]          │                            │
  │─────────────────────────────────>│                            │
  │                                  │  Forward opaquely          │
  │                                  │  (hub CANNOT read ct)      │
  │                                  │───────────────────────────>│
  │                                  │                            │
  │                                  │  Verify nonce > last_seen  │
  │                                  │  Verify pubkey hint        │
  │                                  │  pt = Decrypt(dec_key,     │
  │                                  │       nonce, AAD, ct)      │
  │                                  │  Parse PbPMPlaintext       │
  │                                  │  Display decrypted message │
```

### 4.4 TOFU (Trust On First Use)

The E2EPMManager stores the last known public key per peer. On subsequent key exchanges:

1. **First encounter**: Key stored, no warning
2. **Same key**: No warning (reconnection, same device)
3. **Different key**: **Key change warning** — possible MITM or the peer changed devices

### 4.5 Implementation Classes

#### verlihub (C++ hub)

| File | Role |
|------|------|
| `cdcproto.cpp` — `DC_PBR()` | Routes `$PBR` to target user. Hub sees opaque ciphertext |
| `cpbtranslate.cpp` | No translation for E2EPM (hub cannot read encrypted payload) |
| `crelay.h/cpp` | E2EPM config: `e2epm_enabled`, `e2epm_min_class`, flood settings |

#### verlihub Python

| File | Role |
|------|------|
| `verlihub/client/nmdcpb.py` | Pure Python NMDCpb client with protobuf support |
| `verlihub/client/nmdcpb_crypto.py` | X25519, ChaCha20-Poly1305, HKDF (via `cryptography` lib) |

#### eiskaltdcpp (C++ client)

| File | Role |
|------|------|
| `dcpp/NmdcPbCrypto.h/cpp` | X25519, ChaCha20-Poly1305, HKDF-SHA256, emoji fingerprints (OpenSSL EVP) |
| `dcpp/E2EPMManager.h/cpp` | Session lifecycle: key exchange, encrypt, decrypt, TOFU, pending queue. Owned by `DCContext` via `ContextAware` pattern |
| `dcpp/NmdcHub.cpp` — `handlePbCommand()` | Dispatches `PbPMKeyExchange` and `PbEncryptedPM` |
| `dcpp/NmdcHub.cpp` — `sendEncryptedPM()` | Initiates key exchange if needed, encrypts and sends PM |

#### eiskaltdcpp-py (Python bridge)

| File | Role |
|------|------|
| `src/bridge.h/cpp` | SWIG-wrapped: `e2epmInitiate()`, `e2epmIsEstablished()`, `e2epmGetFingerprint()`, `sendEncryptedPM()`, `e2epmCloseSession()` |
| `src/callbacks.h` | Director callbacks: `onE2EPMEstablished()`, `onE2EPMMessage()`, `onE2EPMKeyChanged()` |
| `python/eiskaltdcpp/dc_client.py` | `e2epm_established`, `e2epm_message`, `e2epm_key_changed` events |
| `python/eiskaltdcpp/async_client.py` | `wait_e2epm_established()`, `wait_e2epm_message()` async coroutines |

---

## 5. HubRelay — Hub-Relayed Encrypted Transfers

### 5.1 Purpose

When two clients are both in **passive mode** (behind NAT, no direct connection possible), legacy NMDC simply fails the transfer. HubRelay routes the data through the hub as an encrypted intermediary.

### 5.2 Relay Session Setup

```
Client A (Passive)              Hub                     Client B (Passive)
   │                             │                             │
   │  Both passive: transfer     │                             │
   │  would fail in legacy NMDC  │                             │
   │                             │                             │
   │  $PBR Bob Alice             │                             │
   │  [PbRelayRequest(           │                             │
   │    token, pub_a,            │                             │
   │    bandwidth_hint)]         │                             │
   │────────────────────────────>│                             │
   │                             │  Hub validates:             │
   │                             │  - relay_enabled?           │
   │                             │  - max_sessions exceeded?   │
   │                             │  - bandwidth within limits? │
   │                             │                             │
   │                             │  Assigns relay_id           │
   │                             │  $PBR Bob Alice [request]   │
   │                             │────────────────────────────>│
   │                             │                             │
   │                             │  Client B decides:          │
   │                             │  accept/reject              │
   │                             │                             │
   │                             │  $PBR Alice Bob             │
   │                             │  [PbRelayAck(               │
   │                             │    relay_id, accepted,      │
   │                             │    pub_b)]                  │
   │<────────────────────────────│                             │
   │                             │<────────────────────────────│
   │                             │                             │
   │  Derive shared secret       │                             │
   │  S = X25519(a, pub_b)       │                             │
   │  HKDF → relay enc/dec keys  │                             │
   │                             │                             │
   │  ═══ Relay Session Active: relay_id ═══                   │
```

### 5.3 Encrypted Data Transfer

```
Client A                        Hub                         Client B
   │                             │                             │
   │  Encrypt chunk:             │                             │
   │  nonce = relay_counter++    │                             │
   │  ct = ChaCha20(relay_key,   │                             │
   │       nonce, chunk_data)    │                             │
   │                             │                             │
   │  $PBR Bob Alice             │                             │
   │  [PbRelayData(              │                             │
   │    relay_id, ct, seq)]      │                             │
   │────────────────────────────>│                             │
   │                             │  Hub forwards opaquely      │
   │                             │  (cannot decrypt chunk)     │
   │                             │  Bandwidth accounting       │
   │                             │────────────────────────────>│
   │                             │                             │
   │                             │  Decrypt: plaintext chunk   │
   │                             │  Reassemble file data       │
   │                             │                             │
   │  ... more chunks ...        │                             │
   │                             │                             │
   │  $PBR Bob Alice             │                             │
   │  [PbRelayClosed(relay_id)]  │                             │
   │────────────────────────────>│                             │
   │                             │  Cleanup relay session      │
   │                             │────────────────────────────>│
```

### 5.4 Passive-to-Passive Detection

In eiskaltdcpp, the `ConnectionManager` timer detects passive-to-passive deadlocks:

```cpp
// ConnectionManager.cpp — timer callback
if (user.isPassive() && !clientManager->isActive()) {
    if (hubSupportsRelay(user)) {
        // Route through relay instead of dropping
        initiateRelay(user);
    } else {
        // Legacy: remove source as impossible
        passiveUsers.push_back(user);
    }
}
```

### 5.5 Implementation Classes

#### verlihub (C++ hub)

| File | Role |
|------|------|
| `crelay.h/cpp` | `cRelayManager`: session tracking, bandwidth throttling, timer cleanup |
| `cdcproto.cpp` — `DC_PBR()` | Routes relay data between participants, validates relay_id |
| Config variables | `relay_enabled`, `relay_max_sessions`, `relay_bw_limit_*`, `relay_idle_timeout`, etc. |

#### eiskaltdcpp (C++ client)

| File | Role |
|------|------|
| `dcpp/RelayConnection.h/cpp` | `RelayManager`: session lifecycle, temp relay IDs, key exchange, encrypt/decrypt relay data |
| `dcpp/ConnectionManager.cpp` | Timer hook: detects passive-passive, initiates relay |
| `dcpp/QueueManager.cpp` | `addSource()`: allows passive sources when relay is available |
| `dcpp/NmdcHub.cpp` | Relay initiation on `$RevConnectToMe`, `handlePbCommand()` dispatches relay_request/ack/closed |

---

## 6. PrivateSearch — Targeted User Search

### 6.1 Purpose

Standard NMDC search (`$Search`) broadcasts the query to every connected user and the hub itself, making it visible to search spy tools and all clients. **PrivateSearch** allows a client to search only a specific user's shares without revealing the query to anyone else.

### 6.2 Wire Protocol

```
Client A                        Hub                         Client B
   │                             │                             │
   │  $PBR Bob Alice             │                             │
   │  [PbPrivateSearch(          │                             │
   │    search_id, query,        │                             │
   │    file_type, max_results)] │                             │
   │────────────────────────────>│                             │
   │                             │  Route to Bob only          │
   │                             │  (no broadcast, no logging  │
   │                             │   of search query)          │
   │                             │────────────────────────────>│
   │                             │                             │
   │                             │  Bob searches local shares  │
   │                             │  Returns results via $PBR   │
   │                             │                             │
   │  $PBR Alice Bob             │                             │
   │  [PbPrivateSearchResult(    │                             │
   │    search_id, results[])]   │                             │
   │<────────────────────────────│                             │
   │                             │<────────────────────────────│
```

### 6.3 Combined with E2EPM

When both clients have an E2EPM session, the search query and results can be encrypted:

```
Client A                        Hub                         Client B
   │                             │                             │
   │  Encrypt search query with  │                             │
   │  E2EPM session key          │                             │
   │                             │                             │
   │  $PBR Bob Alice             │                             │
   │  [PbEncryptedPM(            │                             │
   │    encrypted PbPrivateSearch)] │                           │
   │────────────────────────────>│                             │
   │                             │  Opaque forward             │
   │                             │────────────────────────────>│
   │                             │                             │
   │                             │  Decrypt → PbPrivateSearch  │
   │                             │  Search shares              │
   │                             │  Encrypt results            │
   │                             │                             │
   │  $PBR Alice Bob             │                             │
   │  [PbEncryptedPM(            │                             │
   │    encrypted results)]      │                             │
   │<────────────────────────────│                             │
   │                             │<────────────────────────────│
```

In this mode, the hub cannot see what is being searched or the results.

### 6.4 Protobuf Messages

```protobuf
message PbPrivateSearch {
    string search_id = 1;           // Unique ID to correlate request/response
    string query = 2;               // Search string (filename pattern or keywords)
    string tth = 3;                 // TTH root hash (base32) — mutually exclusive with query
    
    enum FileType {
        ANY = 0;
        AUDIO = 1;
        COMPRESSED = 2;
        DOCUMENT = 3;
        EXECUTABLE = 4;
        PICTURE = 5;
        VIDEO = 6;
        DIRECTORY = 7;
        TTH = 8;
    }
    FileType file_type = 4;
    
    uint64 min_size = 5;            // Minimum file size bytes (0 = no limit)
    uint64 max_size = 6;            // Maximum file size bytes (0 = no limit)
    uint32 max_results = 7;         // Max results to return (default: 10, max: 100)
    repeated string extensions = 8; // File extension filter (e.g., ["mp3", "flac"])
}

message PbPrivateSearchResult {
    string search_id = 1;           // Matches the request search_id
    
    message Result {
        string filename = 1;
        string path = 2;            // Full path within share
        uint64 size = 3;
        string tth = 4;             // Tiger Tree Hash (base32)
        uint32 free_slots = 5;
        uint32 total_slots = 6;
        bool is_directory = 7;
    }
    repeated Result results = 2;
    bool is_partial = 3;            // true if results were truncated by max_results
    string error = 4;               // Non-empty if search failed
}
```

### 6.5 Implementation Status

**Complete across all codebases:**

| Component | File(s) | Status |
|-----------|---------|--------|
| Proto schema | `nmdcpb.proto` (fields 60, 61) | ✅ Implemented |
| C++ handler (receive search) | `NmdcHub.cpp` `handlePbCommand()` | ✅ Searches local ShareManager |
| C++ handler (receive results) | `NmdcHub.cpp` `handlePbCommand()` | ✅ Fires `SearchManagerListener::SR()` |
| C++ sender | `NmdcHub::sendPrivateSearch()` | ✅ Builds envelope + sends via `$PBR` |
| Hub routing | `hub_plugin.py` `_route_direct()` | ✅ Opaque forward (same as E2EPM) |
| Python client sender | `NMDCpbClient.send_private_search()` | ✅ Returns search_id |
| Python client result sender | `NMDCpbClient.send_private_search_result()` | ✅ Sends result envelope |
| Python client callbacks | `on_private_search`, `on_private_search_result` | ✅ Fires on receive |
| eiskaltdcpp-py bridge | `DCBridge::privateSearch()` | ✅ Wraps `NmdcHub::sendPrivateSearch()` |
| Python wrapper | `DCClient.private_search()`, `AsyncDCClient.private_search()` | ✅ |
| C++ tests | 5 Catch2 tests in `test_nmdcpb_crypto.cpp` | ✅ 133/133 pass |
| Python tests | 11 tests in `test_nmdcpb.py` | ✅ 77/77 pass |

---

## 7. Implementation Map

### File Matrix

| Feature | verlihub C++ | verlihub Python | eiskaltdcpp C++ | eiskaltdcpp-py |
|---------|-------------|-----------------|-----------------|----------------|
| **NMDCpb core** | `cdcproto.cpp`, `cpbtranslate.cpp` | `nmdcpb.py` | `NmdcHub.cpp`, `Encoder.cpp` | `bridge.cpp`, `dc_client.py` |
| **E2EPM** | `cdcproto.cpp` (opaque forward) | `nmdcpb_crypto.py` | `E2EPMManager.cpp`, `NmdcPbCrypto.cpp` | `bridge.cpp` (E2EPM methods) |
| **HubRelay** | `crelay.cpp` | `relay.py` | `RelayConnection.cpp`, `ConnectionManager.cpp` | `bridge.cpp` (`hubSupportsRelay`) |
| **PrivateSearch** | `cdcproto.cpp` (route only) | `hub_plugin.py`, `client.py` | `NmdcHub.cpp`, `ShareManager.cpp` | `bridge.cpp`, `dc_client.py`, `async_client.py` |

### DCContext Manager Hierarchy (eiskaltdcpp)

```
DCContext
  ├── ResourceManager
  ├── SettingsManager
  ├── LogManager
  ├── TimerManager
  ├── HashManager
  ├── CryptoManager
  ├── SearchManager
  ├── ClientManager
  ├── ConnectionManager
  ├── DownloadManager
  ├── UploadManager
  ├── ThrottleManager
  ├── QueueManager
  ├── ShareManager
  ├── FavoriteManager
  ├── FinishedManager
  ├── ADLSearchManager
  ├── ConnectivityManager
  ├── MappingManager
  ├── DebugManager
  └── E2EPMManager          ← NMDCpb (WITH_NMDCPB)
```

All managers inherit `ContextAware` and are accessed via `ctx()->getXxxManager()`.
The `E2EPMManager` is conditionally compiled when `WITH_NMDCPB` is defined.

### Test Coverage

| Component | Tests | Framework |
|-----------|-------|-----------|
| verlihub C++ | 6 passed | Catch2 / CTest |
| verlihub Python | 77 passed | pytest |
| eiskaltdcpp C++ | 133 passed | Catch2 / CTest |
| eiskaltdcpp-py | 1 passed | pytest / CTest |

---

## 8. Relay-Only Mode — IP Privacy

### Overview

Relay-Only mode is a client-side privacy feature that prevents a user's IP
address from being revealed to other clients on the hub. When enabled, **all**
file transfers and searches are routed through the hub's relay infrastructure
instead of using direct client-to-client connections.

> **Important limitation:** Relay-only clients can only interact (transfer,
> search) with other NMDCpb-capable clients that support HubRelay. They
> cannot exchange files with regular NMDC clients. This is an accepted
> trade-off for IP privacy.

### Threat Model — IP Leakage Vectors

The following table lists every protocol vector through which a client's IP
can be exposed, and how relay-only mode mitigates each one.

| # | Vector | Severity | Mitigation |
|---|--------|----------|------------|
| 1 | `$ConnectToMe nick ip:port` | CRITICAL | Client never sends CTM; hub blocks CTM from/to relay-only users |
| 2 | `$Search ip:port ...` (active) | CRITICAL | Client forces passive mode (`Hub:nick`); hub blocks active search from relay-only |
| 3 | Direct TCP connection | CRITICAL | No direct connections — all transfers via relay |
| 4 | UDP search reply | CRITICAL | Passive-only mode eliminates UDP replies |
| 5 | `$RevConnectToMe` (indirect) | CRITICAL | Client initiates relay via `$PBR` instead; hub blocks RCTM from/to relay-only |
| 6 | `$UserIP` broadcast | HIGH | Hub skips relay-only users in IP broadcasts; sends `0.0.0.0` on join |
| 7 | `$UserIP` operator query | HIGH | Hub's `ShowUserIP()` returns early for relay-only users |
| 8 | Script API `GetUserIP` | HIGH | Relay-only users masked at hub level |
| 9 | `$MyINFO` mode flag | LOW | Mode char is `R` (relay) — no IP information leaked |
| 10 | Search result IP:port | MEDIUM | Hub:nick format used — no IP in search results |

### Protocol — Feature Negotiation

```
Client → Hub:  $Supports UserCommand NMDCpb HubRelay RelayOnly|
Hub → Client:  $Supports UserCommand NMDCpb HubRelay RelayOnly|
```

The `RelayOnly` feature token in `$Supports`:
- **Client → Hub:** "I want relay-only mode; do not reveal my IP"
- **Hub → Client:** "I acknowledge relay-only mode and will enforce it"

The hub only echoes `RelayOnly` back if its relay subsystem is enabled
(`relay_enabled` config option).

### Protocol — Connection Flow (Relay-Only)

When a relay-only client wants to download from another NMDCpb user:

```
 Relay-Only Client              Hub                    Remote Client
       │                         │                          │
       │  $PBR (PbRelayRequest)  │                          │
       │────────────────────────>│                          │
       │                         │  Creates relay session   │
       │                         │  $PBR (PbRelayRequest)   │
       │                         │─────────────────────────>│
       │                         │                          │
       │                         │  Relay established       │
       │  Data via relay         │                          │
       │<═══════════════════════>│<════════════════════════>│
       │                         │                          │
```

No `$ConnectToMe` or `$RevConnectToMe` is ever sent. The hub's relay
manager mediates the entire transfer.

### Protocol — Search Flow (Relay-Only)

```
 Relay-Only Client              Hub                    Other Clients
       │                         │                          │
       │  $Search Hub:nick ...   │                          │
       │────────────────────────>│                          │
       │                         │  Broadcasts to passive   │
       │                         │  search targets only     │
       │                         │                          │
       │  $SR via hub relay      │                          │
       │<────────────────────────│                          │
```

Active search (`$Search ip:port ...`) is never sent. The client always
uses passive search (`$Search Hub:<nick> ...`) regardless of its actual
network connectivity.

### Protocol — `$MyINFO` Mode Character

```
$MyINFO $ALL nick desc<tag>$ $R$email$sharesize$|
                              ^
                          Mode 'R' = relay-only
```

The mode character `R` signals to other clients and the hub that this
user operates exclusively through the relay. No IP information is encoded.

### Protocol — `$UserIP` Handling

For relay-only users, the hub:
1. **Does not include** them in `$UserIP` broadcasts to operators
2. **Sends `0.0.0.0`** as the IP when the user joins (required by protocol)
3. **Returns early** from `ShowUserIP()` for relay-only users

### Implementation Details

#### eiskaltdcpp (Client Library)

| File | Change |
|------|--------|
| `dcpp/SettingsManager.h` | Added `RELAY_ONLY_MODE` to `IntSetting` enum |
| `dcpp/SettingsManager.cpp` | Tag `"RelayOnlyMode"`, default `false` |
| `dcpp/NmdcHub.h` | `SUPPORTS_RELAYONLY` flag, `isRelayOnly()` method |
| `dcpp/NmdcHub.cpp` | All connection/search/IP handlers guarded |

Key method — `NmdcHub::isRelayOnly()`:
```cpp
bool NmdcHub::isRelayOnly() const {
    return BOOLSETTING(RELAY_ONLY_MODE)
        && hasHubRelaySupport()
        && hasNmdcPbSupport();
}
```

Relay-only mode requires all three conditions:
1. User enabled `RELAY_ONLY_MODE` setting
2. Hub supports `HubRelay`
3. Hub supports `NMDCpb` (for protobuf relay requests)

#### verlihub (Hub)

| File | Change |
|------|--------|
| `src/cconndc.h` | `eSF_RELAYONLY = 1UL << 32` support flag |
| `src/cdcproto.cpp` | `$Supports` parse, CTM/RCTM/Search blocking |
| `src/cserverdc.cpp` | `$UserIP` masking, search broadcast filtering |

The hub enforces relay-only at the protocol level:
- **Blocks outgoing** `$ConnectToMe` and `$RevConnectToMe` from relay-only users
- **Blocks incoming** `$ConnectToMe` and `$RevConnectToMe` to relay-only users
- **Blocks active** `$Search` from relay-only users
- **Masks IPs** in `$UserIP` broadcasts and join notifications

#### eiskaltdcpp-py (Python Bridge)

| File | Change |
|------|--------|
| `src/bridge.h` | `isRelayOnly()`, `setRelayOnlyMode()` declarations |
| `src/bridge.cpp` | Implementation using `getContext()->getSettingsManager()` |
| `python/eiskaltdcpp/dc_client.py` | `is_relay_only()`, `set_relay_only_mode()` |
| `python/eiskaltdcpp/async_client.py` | Async wrappers |

Python usage:
```python
from eiskaltdcpp import DCClient

client = DCClient()
client.set_relay_only_mode(True)   # Enable before connecting
client.connect("nmdc://hub.example.com:411")

# Check if relay-only is active (requires hub support)
if client.is_relay_only("nmdc://hub.example.com:411"):
    print("IP is protected — all transfers via relay")
```

#### verlihub Python Client

| File | Change |
|------|--------|
| `python/verlihub/client/nmdcpb/wire.py` | `FEATURE_RELAYONLY`, `check_supports()` 3-tuple |
| `python/verlihub/client/nmdcpb/client.py` | `relay_only_mode` property, mode 'R' |
| `python/verlihub/client/nmdcpb/hub_plugin.py` | Updated `OnParsedMsgSupports()` |

Python usage:
```python
from verlihub.client.nmdcpb.client import NMDCpbClient

client = NMDCpbClient()
client.relay_only_mode = True
await client.connect("nmdc://hub.example.com:411")

if client.hub_supports_relayonly:
    print("Hub will enforce relay-only privacy")
```

### Configuration

| Setting | Location | Default | Description |
|---------|----------|---------|-------------|
| `RelayOnlyMode` | eiskaltdcpp `DCPlusPlus.xml` | `0` | Enable relay-only privacy mode |
| `relay_enabled` | verlihub hub config | varies | Hub must have relay subsystem enabled |

### Compatibility Matrix

| Client A | Client B | Can Transfer? | Method |
|----------|----------|--------------|--------|
| Relay-only | Relay-only (NMDCpb) | Yes | Hub relay |
| Relay-only | NMDCpb (normal) | Yes | Hub relay |
| Relay-only | Regular NMDC | **No** | No common relay support |
| Normal | Normal | Yes | Direct connection |
| Normal | Regular NMDC | Yes | Direct connection |
