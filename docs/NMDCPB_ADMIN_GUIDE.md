# NMDCpb Admin & API Guide

## 1. Overview

The NMDCpb hub plugin provides relay channel management, E2E encrypted PM
brokering, and admin tooling via:

- **Chat commands** — Operator commands in hub main chat
- **REST API** — JSON endpoints for programmatic access
- **WebSocket** — Real-time event streaming
- **Dashboard UI** — Web-based admin panel

---

## 2. Chat Commands

Available to operators (class ≥ 5) in hub main chat:

| Command | Description |
|---------|-------------|
| `+nmdcpb stats` | Show plugin statistics (messages routed, bytes forwarded, errors, etc.) |
| `+nmdcpb users` | List all NMDCpb-capable users currently online |
| `+nmdcpb relay` | List all active relay sessions with details |

### Example

```
+nmdcpb stats
```
```
NMDCpb Plugin Statistics:
  PB messages routed: 1247
  Relay created:      23
  Relay closed:       18
  Relay bytes:        4523776
  E2EPM forwarded:    89
  Rate limited:       3
  Flood mutes:        1
  Opaque forwards:    412
  Opaque fallbacks:   2
```

---

## 3. REST API Endpoints

All endpoints require admin authentication (JWT cookie, `user_class ≥ 5`).

Base URL: `/dashboard/nmdcpb/api`

### GET `/api/stats`

Returns plugin statistics and configuration.

**Response:**
```json
{
  "pb_messages_routed": 1247,
  "relay_sessions_created": 23,
  "relay_sessions_closed": 18,
  "relay_bytes_forwarded": 4523776,
  "e2epm_forwarded": 89,
  "rate_limited": 3,
  "flood_mutes": 1,
  "relay_opaque_forwards": 412,
  "relay_opaque_fallbacks": 2,
  "active_relay_sessions": 5,
  "pb_users_count": 12,
  "config": {
    "relay_max_payload": 65536,
    "relay_max_sessions_per_user": 5,
    "relay_idle_timeout": 300,
    "rate_limit_window": 60,
    "rate_limit_max_tokens": 100,
    "flood_mute_seconds": 120
  }
}
```

### GET `/api/users`

Returns all NMDCpb-capable users currently online.

**Response:**
```json
{
  "users": [
    {
      "nick": "alice",
      "features": ["NMDCpb", "NMDCpbRelay", "NMDCpbE2EPM"],
      "active_relays": 2
    }
  ],
  "total": 1
}
```

### GET `/api/relays`

Returns all active relay sessions.

**Response:**
```json
{
  "sessions": [
    {
      "relay_id": 1,
      "user_a": "alice",
      "user_b": "bob",
      "created_at": "2025-06-01T10:00:00Z",
      "bytes_forwarded": 102400,
      "bytes_forwarded_human": "100.0 KB",
      "last_activity": "2025-06-01T10:05:00Z",
      "age_seconds": 300,
      "idle_seconds": 0,
      "is_idle": false
    }
  ],
  "total": 1,
  "pending_requests": 0
}
```

### GET `/api/relay/{relay_id}`

Returns details for a specific relay session.

### POST `/api/relay/{relay_id}/close`

Force-close a relay session. Notifies both participants with reason code 3 (admin).

**Response:**
```json
{
  "ok": true,
  "message": "Relay session 1 closed"
}
```

### POST `/api/relay/close-all`

Close all active relay sessions.

### POST `/api/relay/close-user/{nick}`

Close all relay sessions for a specific user.

---

## 4. WebSocket Events

Connect to `/ws/relay` (requires admin JWT cookie).

### Event Types

| Event | Fields | Description |
|-------|--------|-------------|
| `connected` | `message`, `time` | Initial connection confirmation |
| `relay_created` | `relay_id`, `user_a`, `user_b` | New relay session established |
| `relay_closed` | `relay_id`, `user_a`, `user_b`, `reason`, `bytes_forwarded` | Session terminated |
| `ping`/`pong` | `time` | Keepalive (30s timeout) |

### Example

```javascript
const ws = new WebSocket('wss://hub.example.com/ws/relay');
ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    switch (msg.type) {
        case 'relay_created':
            console.log(`Relay #${msg.relay_id}: ${msg.user_a} ↔ ${msg.user_b}`);
            break;
        case 'relay_closed':
            console.log(`Relay #${msg.relay_id} closed (${msg.bytes_forwarded} bytes)`);
            break;
    }
};
```

---

## 5. Dashboard UI

Access the NMDCpb admin dashboard at `/dashboard/nmdcpb/`.

### Features

- **Statistics cards** — Live counters for messages routed, relay bytes, active
  sessions, opaque forwards, rate-limited events, flood mutes
- **Relay session table** — All active sessions with user pairs, bytes
  forwarded, age, idle time, and close buttons
- **User table** — NMDCpb-capable users online with their features and active
  relay counts
- **Configuration display** — Current plugin configuration values
- **Auto-refresh** — Dashboard refreshes every 5 seconds
- **Admin actions** — Close individual sessions, close all, close all for a user

### Authentication

The dashboard requires an admin session (JWT cookie with `user_class ≥ 5`).
Log in through the main Verlihub dashboard at `/dashboard/login`.

---

## 6. Configuration

Plugin configuration is set in the hub plugin initialization:

| Setting | Default | Description |
|---------|---------|-------------|
| `RELAY_MAX_PAYLOAD` | 65536 | Maximum relay data frame size (bytes) |
| `RELAY_MAX_SESSIONS_PER_USER` | 5 | Maximum concurrent relay sessions per user |
| `RELAY_IDLE_TIMEOUT` | 300 | Idle session timeout (seconds) |
| `RATE_LIMIT_WINDOW` | 60 | Rate limit window (seconds) |
| `RATE_LIMIT_MAX_TOKENS` | 100 | Max messages per window |
| `FLOOD_MUTE_SECONDS` | 120 | Duration of flood mute (seconds) |
| `ROTATION_MESSAGE_THRESHOLD` | 1000 | E2EPM key rotation after N messages (C++) |
| `ROTATION_TIME_THRESHOLD` | 3600 | E2EPM key rotation after N seconds (C++) |

---

## 7. Troubleshooting

### No NMDCpb users shown
- Verify client sends `NMDCpb` in `$Supports`
- Check hub log for `NMDCpb negotiated for <nick>`

### Relay sessions not establishing
- Both users must have `NMDCpbRelay` in their features
- Check user's relay session count against `RELAY_MAX_SESSIONS_PER_USER`
- Look for rate-limit or flood-mute entries in `+nmdcpb stats`

### E2EPM key exchange failing
- Both users must have `NMDCpbE2EPM` feature
- Check for `pm_key_exchange` messages in hub debug log
- Verify cryptography library is installed (`pip install cryptography`)

### Dashboard 403 errors
- Ensure logged-in user has `user_class ≥ 5`
- Check JWT cookie is present (`access_token` cookie)

### High relay_opaque_fallbacks count
- This means opaque forwarding is falling back to full re-serialization
- Usually benign — happens when protobuf field extraction fails
  (e.g., unusual field ordering or nested extensions)
- Check for protocol version mismatches between clients
