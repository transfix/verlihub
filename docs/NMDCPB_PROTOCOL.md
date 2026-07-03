# NMDCpb Wire Protocol Specification

> Version 1.0 — June 2025

## 1. Overview

NMDCpb is a backward-compatible protobuf extension to the NMDC (NeoModus Direct Connect)
protocol. It enables structured binary messaging (chat, search, relay, E2E encryption,
media, voice/video) while preserving full interoperability with legacy NMDC clients and hubs.

### Design Principles

1. **Backward compatible** — NMDCpb messages use NMDC `$` command syntax; legacy clients
   and hubs ignore them silently.
2. **Incremental adoption** — Feature negotiation via `$Supports`; clients announce
   `NMDCpb` (or individual sub-features) and fall back gracefully.
3. **Hub-validated** — The hub rewrites `from_nick` on every message to prevent spoofing.
4. **Efficient relay** — Bulk data uses opaque forwarding; the hub routes by relay-ID
   without deserializing payloads.

---

## 2. Wire Formats

### 2.1 `$PB` — Base64-Encoded Envelope

```
$PB <base64url_payload>|
```

| Field | Description |
|-------|-------------|
| `$PB ` | 4-byte text prefix (ASCII) |
| `<base64url_payload>` | RFC 4648 §5 base64url (no padding) of a serialized `PbEnvelope` |
| `\|` | Standard NMDC message terminator |

This is the default format. Easy to implement, interoperates with NMDC text parsing.

### 2.2 `$PBB` — Binary-Framed Envelope

```
$PBB <length_hex>\n<raw_protobuf_bytes>|
```

| Field | Description |
|-------|-------------|
| `$PBB ` | 5-byte text prefix |
| `<length_hex>` | Payload length in hex ASCII |
| `\n` | Separator (0x0A) |
| `<raw_protobuf_bytes>` | Serialized `PbEnvelope` (exactly `length` bytes) |
| `\|` | NMDC terminator (after binary payload) |

Avoids the ~33% base64 overhead. Recommended for relay data and large payloads.

### 2.3 `$PBR` — Relay Data Shortcut

```
$PBR <relay_id_hex> <length_hex>\n<encrypted_bytes>|
```

A streamlined framing for relay data that skips the full protobuf envelope. The hub
routes based solely on the relay ID without deserializing the payload.

---

## 3. Feature Negotiation

### 3.1 `$Supports` Flags

Clients advertise NMDCpb capability in the standard NMDC `$Supports` handshake:

| Flag | Description |
|------|-------------|
| `NMDCpb` | Base NMDCpb support (envelope, chat, user info, search) |
| `NMDCpbRelay` | Relay channel support |
| `NMDCpbE2EPM` | End-to-end encrypted private messaging |
| `NMDCpbMedia` | Media upload/download |
| `NMDCpbCall` | Voice/video call signaling |

### 3.2 Negotiation Flow

```
Client → Hub:  $Supports NMDCpb NMDCpbRelay NMDCpbE2EPM ...
Hub → Client:  $Supports NMDCpb NMDCpbRelay ...
```

The hub echoes back only the features it supports. Clients must check the hub's
response before using any NMDCpb feature.

---

## 4. Envelope Schema

Every NMDCpb message is wrapped in a `PbEnvelope`:

```protobuf
message PbEnvelope {
  enum RouteType {
    BROADCAST = 0;  // To all NMDCpb clients (ADC B-type analog)
    DIRECT    = 1;  // To a specific user (ADC D-type)
    HUB       = 2;  // Client→hub only (ADC H-type)
    INFO      = 3;  // Hub→client only (ADC I-type)
    ECHO      = 4;  // Like DIRECT, sender gets a copy (ADC E-type)
    FEATURE   = 5;  // Broadcast to clients with matching features
  }

  RouteType route    = 1;
  string from_nick   = 2;  // Sender nick (hub-validated)
  string to_nick     = 3;  // Recipient nick (DIRECT/ECHO)
  string features    = 4;  // Required features (FEATURE route)
  uint64 timestamp   = 5;  // Unix millis
  uint32 sequence    = 6;  // Per-connection sequence number

  oneof payload {
    PbChat              chat              = 10;
    PbUserInfo          user_info         = 11;
    PbSearch            search            = 12;
    PbSearchResult      search_result     = 13;
    PbConnect           connect           = 14;
    PbHubInfo           hub_info          = 15;
    PbUserList          user_list         = 16;
    PbRelayRequest      relay_request     = 20;
    PbRelayAck          relay_ack         = 21;
    PbRelayData         relay_data        = 22;
    PbRelayClosed       relay_closed      = 23;
    PbRelayStatus       relay_status      = 24;
    PbPMKeyExchange     pm_key_exchange   = 25;
    PbEncryptedPM       encrypted_pm      = 26;
    PbStatus            status            = 30;
    PbExtension         extension         = 31;
    PbMediaUpload       media_upload      = 40;
    PbMediaMeta         media_meta        = 41;
    PbMediaDelete       media_delete      = 42;
    PbMediaCapabilities media_capabilities = 43;
    PbCallOffer         call_offer        = 50;
    PbCallAnswer        call_answer       = 51;
    PbCallCandidate     call_candidate    = 52;
    PbCallEnd           call_end          = 53;
    PbCallMediaControl  call_media_control = 54;
    PbHubStream         hub_stream        = 55;
  }
}
```

### 4.1 Routing Rules

| Route | From | To | Hub Action |
|-------|------|----|------------|
| `BROADCAST` | Any | All NMDCpb users | Validate `from_nick`, broadcast |
| `DIRECT` | Any | `to_nick` | Validate, deliver to target only |
| `HUB` | Any | Hub | Process internally, do not forward |
| `INFO` | Hub | Client | Hub-generated announcements |
| `ECHO` | Any | `to_nick` + sender | Deliver to target, echo back to sender |
| `FEATURE` | Any | Matching clients | Broadcast to clients with listed features |

The hub **always** overwrites `from_nick` with the authenticated nick of the sender
to prevent spoofing.

---

## 5. Relay Channel Protocol

Relay channels provide hub-mediated data channels between two users. The data
is opaque to the hub — typically encrypted with a session key established
during the relay handshake.

### 5.1 Session Lifecycle

```
 Requester                Hub                  Responder
     |                     |                       |
     |-- relay_request --->|                       |
     |                     |--- relay_request ---->|
     |                     |                       |
     |                     |<-- relay_ack ---------|
     |<-- relay_ack -------|                       |
     |                     |                       |
     |== relay_data ======>|== relay_data ========>|
     |<= relay_data =======|<= relay_data ========|
     |                     |                       |
     |-- relay_closed ---->|--- relay_closed ----->|
```

### 5.2 Messages

**`PbRelayRequest`** (field 20):
```protobuf
message PbRelayRequest {
  string token      = 1;  // Unique request token
  string purpose    = 2;  // Human-readable description
  bytes  public_key = 3;  // X25519 public key for session encryption
}
```

**`PbRelayAck`** (field 21):
```protobuf
message PbRelayAck {
  string token         = 1;  // Matches request token
  bool   accepted      = 2;
  uint32 relay_id      = 3;  // Hub-assigned (only if accepted)
  bytes  public_key    = 4;  // Responder's X25519 public key
  string reject_reason = 5;
}
```

**`PbRelayData`** (field 22):
```protobuf
message PbRelayData {
  uint32 relay_id = 1;
  bytes  data     = 2;  // Opaque payload (encrypted by participants)
  uint64 offset   = 3;  // Stream offset for ordering
}
```

**`PbRelayClosed`** (field 23):
```protobuf
message PbRelayClosed {
  uint32 relay_id = 1;
  uint32 reason   = 2;  // 0=normal, 1=error, 2=timeout, 3=admin
}
```

### 5.3 Hub Relay Policy

- **Max sessions per user**: Configurable (default 5)
- **Max payload size**: 65536 bytes per `relay_data` frame
- **Idle timeout**: 300 seconds with no data
- **Rate limiting**: Token bucket per user (configurable)

---

## 6. End-to-End Encrypted Private Messages (E2EPM)

### 6.1 Key Exchange

Uses X25519 Diffie-Hellman for key agreement:

```
 Alice                    Hub                     Bob
   |                       |                       |
   |-- pm_key_exchange --->|--- pm_key_exchange -->|
   |   (Alice's X25519)    |   (Alice's X25519)    |
   |                       |                       |
   |                       |<- pm_key_exchange ----|
   |<- pm_key_exchange ----|   (Bob's X25519)      |
   |   (Bob's X25519)      |                       |
   |                       |                       |
   |===== encrypted_pm ===>|=== encrypted_pm ====>|
```

### 6.2 Encryption

- **Algorithm**: ChaCha20-Poly1305 (AEAD)
- **Key derivation**: HKDF-SHA256 from X25519 shared secret
- **Nonce**: Incremented per message (12-byte, little-endian counter)
- **AAD**: None (associated data is empty)

### 6.3 Key Rotation

Keys are automatically rotated when either threshold is reached:

| Threshold | Default |
|-----------|---------|
| Messages sent/received | 1000 |
| Time elapsed | 3600 seconds |

Rotation is transparent: the initiator sends a fresh `pm_key_exchange`; the
responder completes the exchange. Old keys are securely wiped.

### 6.4 Messages

**`PbPMKeyExchange`** (field 25):
```protobuf
message PbPMKeyExchange {
  bytes public_key = 1;  // X25519 ephemeral public key
}
```

**`PbEncryptedPM`** (field 26):
```protobuf
message PbEncryptedPM {
  bytes  ciphertext = 1;  // ChaCha20-Poly1305 encrypted message
  bytes  nonce      = 2;  // 12-byte nonce
  uint32 key_id     = 3;  // Key generation identifier
}
```

---

## 7. Status & Error Codes

**`PbStatus`** (field 30):
```protobuf
message PbStatus {
  enum Severity {
    DEBUG   = 0;
    INFO    = 1;
    WARNING = 2;
    ERROR   = 3;
    FATAL   = 4;
  }
  Severity severity = 1;
  uint32   code     = 2;
  string   message  = 3;
  string   ref_cmd  = 4;  // Command that triggered this status
}
```

### Standard Error Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 10 | Unknown payload type |
| 11 | Permission denied |
| 12 | Rate limited |
| 13 | Feature not supported |
| 14 | Bad request |
| 17 | Unknown relay session |
| 18 | Not a relay participant |
| 19 | Relay data exceeds max size |
| 20 | Max relay sessions reached |

---

## 8. Security Considerations

1. **Hub-validated from_nick** — The hub overwrites `from_nick` on every
   forwarded message; clients cannot spoof sender identity.
2. **Relay data opacity** — The hub cannot read relay data; it is encrypted
   end-to-end by the participants.
3. **Rate limiting** — Per-user sliding-window token bucket prevents flooding.
4. **Replay protection** — E2EPM uses monotonic nonce counters; relay data uses
   stream offsets.
5. **Key rotation** — Automatic rotation after message/time thresholds limits
   the window of compromise.
6. **Input validation** — All protobuf fields are length-checked before
   processing; malformed messages are dropped with error status.

---

## 9. Compatibility Matrix

| Component | Required Version | Notes |
|-----------|-----------------|-------|
| EiskaltDC++ | nmdcpb-extension branch | C++ client library |
| eiskaltdcpp-py | nmdcpb-bridge branch | Python SWIG bindings |
| Verlihub (Python) | verlihub-py-e2e-ext branch | Hub with NMDCpb plugin |
| Protobuf | ≥ 3.19 | Proto3 syntax |

---

## 10. References

- [NMDC Protocol](https://nmdc.sourceforge.io/NMDC.html)
- [ADC Protocol](https://adc.sourceforge.io/ADC.html)
- [Protocol Buffers](https://protobuf.dev/)
- [RFC 4648 — Base64url](https://datatracker.ietf.org/doc/html/rfc4648#section-5)
- [RFC 7748 — X25519](https://datatracker.ietf.org/doc/html/rfc7748)
- [RFC 8439 — ChaCha20-Poly1305](https://datatracker.ietf.org/doc/html/rfc8439)
