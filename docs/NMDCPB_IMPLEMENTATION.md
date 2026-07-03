# NMDCpb Protocol Extension — Verlihub Hub Implementation

## Overview

Verlihub implements the server side of the NMDCpb protocol extension — a
protobuf-based structured messaging layer that extends NMDC with six
integrated features:

| Extension | Hub Role | Key Module |
|-----------|----------|------------|
| **NMDCpb** | Protobuf dispatch, legacy translation, feature negotiation | `hub_plugin.py` |
| **HubRelay** | Relay session management, encrypted data routing | `hub_plugin.py` |
| **E2EPM** | Key exchange forwarding, encrypted PM routing | `hub_plugin.py` |
| **MediaShare** | Media storage (FS/S3), HTTP upload/download API, P2P routing | `media_handler.py`, `media_api.py` |
| **Channels** | Channel lifecycle, membership, message routing, E2E key mgmt | `channel_manager.py` |
| **VoiceVideo** | Call signaling routing, hub streams, SFU fan-out | `call_manager.py` |

## Architecture

### Plugin Structure

NMDCpb runs as a Python plugin inside verlihub's plugin system. The entry
point is the `DC_PB()` handler in `hub_plugin.py` which intercepts `$PB`
messages.

```
verlihub C++ core
  └─ python plugin (scripts/hub_api.py)
       └─ NMDCpb hub_plugin.py
            ├─ MediaHandler (media_handler.py)
            │   └─ MediaStorage: FileSystemStorage / S3Storage (media_storage.py)
            ├─ MediaAPI (media_api.py) — FastAPI HTTP endpoints
            ├─ ChannelManager (channel_manager.py)
            │   └─ E2E sender key routing
            └─ CallManager + HubStreamManager (call_manager.py)
                └─ SFU group call fan-out
```

### Message Flow

1. Client sends `$PB <base64(PbEnvelope)>|`
2. verlihub's `DC_Supports()` detects `NMDCpb` → sets feature bit
3. `cDCProto::DC_PB()` invokes Python plugin's `OnPBMessage()` hook
4. `hub_plugin.py` deserializes `PbEnvelope`, routes by payload type:

```
PbChat       → translate to NMDC $<nick> msg| for legacy clients
PbPM         → translate to NMDC $To: target| for legacy clients
PbSearch     → translate to NMDC $Search| for legacy clients
PbRelay*     → relay session management (accept/reject/forward)
PbE2e*       → forward key exchange / encrypted PM to target
PbMedia*     → MediaHandler (store/retrieve/route metadata)
PbP2PMedia*  → route P2P media references & data between peers
PbChannel*   → ChannelManager (CRUD, message routing, E2E keys)
PbCall*      → CallManager (signaling, group fan-out)
PbHubStream  → HubStreamManager (broadcast, subscribe)
```

### Hub-Hosted Media API

The HTTP media API is mounted on the hub's FastAPI app at `/api/media/`:

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/media/upload` | Bearer token | Upload media file (multipart) |
| GET | `/api/media/quota` | Bearer token | Get user's storage quota |
| GET | `/api/media/{id}` | Bearer token | Download media file |
| GET | `/api/media/{id}/thumb` | Bearer token | Download thumbnail |
| GET | `/api/media/{id}/meta` | Bearer token | Get media metadata |
| DELETE | `/api/media/{id}` | Bearer token | Delete media (owner/admin) |

Session tokens are generated per-user with HMAC-SHA256, configurable secret
and TTL via `VH_MEDIA_TOKEN_SECRET` and `VH_MEDIA_TOKEN_TTL` environment
variables.

### Channel Manager

`channel_manager.py` manages:
- Channel CRUD (create/delete), join/leave, topic, kick, role assignment
- Message routing (broadcast to all channel members)
- `#general` auto-join (translates main chat ↔ channel messages)
- E2E sender key distribution for private channels
- Key rotation on membership changes (join/leave/kick/admin)
- Channel history storage with configurable retention
- Admin commands: `+nmdcpb channel list|create|delete|info`

### Call Manager

`call_manager.py` manages:
- 1:1 call routing: offer → answer → candidate exchange → end
- Media control: mute/unmute, video on/off, screen share
- Group calls: SFU fan-out (`_forward_to_group()`)
- Hub streams: start/stop/join/leave with broadcaster permissions
- Configurable limits: max duration, max participants, stream count

### Media Storage

Two storage backends in `media_storage.py`:

| Backend | Class | Config |
|---------|-------|--------|
| **Filesystem** | `FileSystemStorage` | `storage_path`, sharded by media_id prefix |
| **S3** | `S3Storage` | `s3_bucket`, `s3_prefix`, `s3_endpoint_url` |

Both implement: `store()`, `retrieve()`, `get_meta()`, `delete()`,
`get_thumbnail()`, `list_expired()`, `purge_expired()`, `get_quota()`.

Media features:
- Per-user quotas with configurable max
- Automatic expiry with TTL enforcement
- SHA-256 checksums on upload
- Thumbnail generation for images
- E2E encrypted media (hub stores ciphertext only)
- MIME type whitelist and size limits

## Configuration

Hub admin commands for NMDCpb management:

```
+nmdcpb status              — Show NMDCpb extension status
+nmdcpb relay list          — List active relay sessions
+nmdcpb relay close <id>    — Force-close a relay session
+nmdcpb channel list        — List all channels
+nmdcpb channel create <name> [--private]
+nmdcpb channel delete <name>
+nmdcpb channel info <name>
```

Environment variables:
| Variable | Default | Description |
|----------|---------|-------------|
| `VH_MEDIA_STORAGE_PATH` | `/var/lib/verlihub/media` | Media file storage path |
| `VH_MEDIA_MAX_SIZE` | `10485760` | Max upload size (bytes) |
| `VH_MEDIA_DEFAULT_TTL` | `86400` | Default media expiry (seconds) |
| `VH_MEDIA_TOKEN_SECRET` | (random) | HMAC secret for session tokens |
| `VH_MEDIA_TOKEN_TTL` | `3600` | Session token TTL (seconds) |
| `VH_CHANNEL_MAX_PER_HUB` | `100` | Max channels per hub |
| `VH_CALL_MAX_DURATION` | `3600` | Max call duration (seconds) |
| `VH_CALL_MAX_PARTICIPANTS` | `10` | Max group call participants |
| `VH_STREAM_MAX_CONCURRENT` | `5` | Max simultaneous hub streams |

## Tests

425 pytest tests (excluding socket/live tests that need a running hub):

```bash
cd /path/to/verlihub/python
pytest tests/test_nmdcpb_*.py --noconftest -q
```

Key test files:
- `tests/test_nmdcpb_integration.py` — Core protocol, relay, E2EPM, segment relay
- `tests/test_nmdcpb_media.py` — Media storage, handler, capabilities, expiry
- `tests/test_nmdcpb_p2p_media.py` — P2P media routing, multi-source
- `tests/test_nmdcpb_channels.py` — Channel CRUD, E2E encryption, sender keys (80 tests)
- `tests/test_nmdcpb_voicevideo.py` — Call signaling, group calls, hub streams (94 tests)
- `tests/test_nmdcpb_media_api.py` — HTTP media API, session tokens, endpoints (24 tests)
- `tests/test_nmdcpb_segment_relay.py` — Segmented download relay
- `tests/test_nmdcpb_benchmark.py` — Performance benchmarks
- `tests/test_nmdcpb_fuzz.py` — Fuzz testing with Hypothesis
- `tests/test_nmdcpb_socket.py` — Live socket integration (requires running hub)
- `tests/test_nmdcpb_live.py` — Docker-based live tests

## Related Documentation

- [NMDCPB_PROTOCOL.md](NMDCPB_PROTOCOL.md) — Wire protocol specification
- [NMDCPB_PROTOCOL_WORKFLOWS.md](NMDCPB_PROTOCOL_WORKFLOWS.md) — Message flow diagrams
- [NMDCPB_ADMIN_GUIDE.md](NMDCPB_ADMIN_GUIDE.md) — Hub administrator guide

## Implementation Status

| Phase | Status | Key Deliverables |
|-------|--------|-----------------|
| Phase 1: NMDCpb | ✅ Complete | hub_plugin.py PB dispatch, legacy translation, feature negotiation |
| Phase 2: HubRelay + E2EPM | ✅ Complete | Relay routing, E2EPM forwarding, rate limiting |
| Phase 3/3.5: Advanced Relay | ✅ Complete | Resume token remapping, TTH verify, stealth search routing |
| Phase 4: MediaShare | ✅ Complete | FileSystemStorage, S3Storage, MediaHandler, P2P routing |
| Phase 4.5: Channels | ✅ Complete | ChannelManager, E2E sender keys, key rotation, `#general` |
| Phase 5: VoiceVideo | ✅ Complete | CallManager, HubStreamManager, SFU fan-out |
| Phase 6: Integration & REST | ✅ Complete | HTTP media API (6 endpoints), session token auth, 24 media API tests |
| Phase 7: Media Codecs | 🔲 N/A | Hub is codec-agnostic (forwards opaque media data) |
