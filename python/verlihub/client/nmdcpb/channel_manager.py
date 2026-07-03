"""
NMDCpb Channel Manager
======================

Hub-side channel management for public and private (E2E-encrypted) channels.
Implements Section 9 of the NMDCpb Extension Plan.

Responsibilities:
- Channel registry and lifecycle (create, delete, list)
- Membership tracking (join, leave, kick, role changes)
- #general auto-creation and auto-join for NMDCpb users
- Permission enforcement (min class, per-channel roles)
- Message history buffer (configurable depth per channel)
- Channel message routing (broadcast to members only)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable

from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope,
    PbChannel,
    PbChannelList,
    PbChannelCreated,
    PbChannelMemberUpdate,
    PbChannelInvite,
    PbChannelInviteResponse,
    PbChannelHistory,
    PbChannelEncrypted,
    PbSenderKeyRotation,
    PbChat,
    PbStatus,
    CHANNEL_MEMBER,
    CHANNEL_ADMIN,
    CHANNEL_OWNER,
    CHANNEL_READONLY,
)
from verlihub.client.nmdcpb.wire import WireCodec

log = logging.getLogger("nmdcpb_hub.channels")


# ---------------------------------------------------------------------------
# Configuration (Section 9.8)
# ---------------------------------------------------------------------------

@dataclass
class ChannelConfig:
    """Hub-level channel configuration — mirrors Section 9.8 config keys."""
    enabled: bool = True
    max_per_hub: int = 50
    max_per_user: int = 10
    max_members: int = 200
    create_min_class: int = 1
    private_enabled: bool = True
    private_create_min_class: int = 1
    private_max_members: int = 50
    history_depth: int = 100
    history_ttl: int = 86400          # seconds (24h)
    private_history: bool = False
    name_max_length: int = 32
    topic_max_length: int = 200


# ---------------------------------------------------------------------------
# Channel data structures
# ---------------------------------------------------------------------------

class ChannelRole(IntEnum):
    """Mirror of protobuf ChannelRole for internal use."""
    MEMBER = 0
    ADMIN = 1
    OWNER = 2
    READONLY = 3


# Map protobuf ChannelRole values to internal enum
_PB_ROLE_MAP = {
    CHANNEL_MEMBER: ChannelRole.MEMBER,
    CHANNEL_ADMIN: ChannelRole.ADMIN,
    CHANNEL_OWNER: ChannelRole.OWNER,
    CHANNEL_READONLY: ChannelRole.READONLY,
}


@dataclass
class HistoryEntry:
    """A single message in the channel history buffer."""
    timestamp: int       # ms since epoch
    from_nick: str
    pb_bytes: bytes      # serialized PbEnvelope (for replay)
    is_encrypted: bool = False


@dataclass
class Channel:
    """Server-side channel state."""
    channel_id: str
    name: str
    topic: str = ""
    is_private: bool = False
    owner_nick: str = ""
    created_at: int = 0  # ms since epoch

    # Membership: nick → ChannelRole
    members: dict[str, ChannelRole] = field(default_factory=dict)

    # History buffer (ring buffer via list, capped at config.history_depth)
    history: list[HistoryEntry] = field(default_factory=list)

    # Pending invitations: nick → inviter_nick
    pending_invites: dict[str, str] = field(default_factory=dict)

    @property
    def member_count(self) -> int:
        return len(self.members)

    def has_member(self, nick: str) -> bool:
        return nick in self.members

    def get_role(self, nick: str) -> ChannelRole | None:
        return self.members.get(nick)

    def is_admin_or_owner(self, nick: str) -> bool:
        role = self.members.get(nick)
        return role in (ChannelRole.OWNER, ChannelRole.ADMIN)

    def is_owner(self, nick: str) -> bool:
        return self.members.get(nick) == ChannelRole.OWNER

    def add_member(self, nick: str, role: ChannelRole = ChannelRole.MEMBER) -> None:
        self.members[nick] = role

    def remove_member(self, nick: str) -> ChannelRole | None:
        return self.members.pop(nick, None)


# Channel-ID validation: alphanumeric + hyphens, 1-32 chars
_CHANNEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# The built-in general channel
GENERAL_CHANNEL_ID = "general"


# ---------------------------------------------------------------------------
# ChannelManager
# ---------------------------------------------------------------------------

class ChannelManager:
    """Hub-side channel management.

    Parameters
    ----------
    config : ChannelConfig
        Hub-level channel configuration.
    send_fn : callable(data: str, nick: str) -> bool
        Function to send raw wire data to a user.
    status_fn : callable(nick: str, severity: int, code: int, message: str) -> None
        Function to send a PbStatus message to a user.
    get_user_class_fn : callable(nick: str) -> int
        Function to retrieve a user's class level (0=guest .. 10=master).
        Returns 0 if user class cannot be determined.
    """

    def __init__(
        self,
        config: ChannelConfig | None = None,
        send_fn: Callable[[str, str], bool] | None = None,
        status_fn: Callable[[str, int, int, str], None] | None = None,
        get_user_class_fn: Callable[[str], int] | None = None,
    ):
        self.config = config or ChannelConfig()
        self._send_fn = send_fn or self._noop_send
        self._status_fn = status_fn or self._noop_status
        self._get_user_class = get_user_class_fn or (lambda _nick: 1)

        # channel_id → Channel
        self._channels: dict[str, Channel] = {}

        # Stats
        self.stats: dict[str, int] = {
            "channels_created": 0,
            "channels_deleted": 0,
            "channel_joins": 0,
            "channel_leaves": 0,
            "channel_kicks": 0,
            "channel_messages_routed": 0,
            "channel_invites_sent": 0,
            "channel_history_replays": 0,
        }

        # Create the built-in #general channel
        self._ensure_general()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _noop_send(data: str, nick: str) -> bool:
        return True

    @staticmethod
    def _noop_status(nick: str, severity: int, code: int, message: str) -> None:
        pass

    def _ensure_general(self) -> Channel:
        """Create #general if it doesn't exist."""
        if GENERAL_CHANNEL_ID not in self._channels:
            ch = Channel(
                channel_id=GENERAL_CHANNEL_ID,
                name="#general",
                topic="Main chat",
                is_private=False,
                owner_nick="",
                created_at=int(time.time() * 1000),
            )
            self._channels[GENERAL_CHANNEL_ID] = ch
            log.info("Created built-in #general channel")
        return self._channels[GENERAL_CHANNEL_ID]

    def _validate_channel_id(self, channel_id: str) -> bool:
        """Check if channel_id is syntactically valid."""
        return bool(_CHANNEL_ID_RE.match(channel_id))

    def _user_channel_count(self, nick: str) -> int:
        """Count how many channels a user has joined."""
        return sum(
            1 for ch in self._channels.values() if ch.has_member(nick)
        )

    def _send_to_member(self, nick: str, env: PbEnvelope) -> None:
        """Encode and send an envelope to a single user."""
        wire = WireCodec.encode_text(env)
        self._send_fn(wire, nick)

    def _broadcast_to_channel(
        self, channel_id: str, env: PbEnvelope, exclude: str = "",
    ) -> int:
        """Send an envelope to all members of a channel, optionally excluding one.

        Returns the number of members notified.
        """
        ch = self._channels.get(channel_id)
        if not ch:
            return 0
        wire = WireCodec.encode_text(env)
        count = 0
        for nick in ch.members:
            if nick != exclude:
                self._send_fn(wire, nick)
                count += 1
        return count

    def _make_member_update(
        self,
        channel_id: str,
        nick: str,
        update_type: int,
        new_role: int = CHANNEL_MEMBER,
        reason: str = "",
    ) -> PbEnvelope:
        """Build a PbChannelMemberUpdate envelope."""
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.channel_member_update.channel_id = channel_id
        env.channel_member_update.nick = nick
        env.channel_member_update.update_type = update_type
        env.channel_member_update.new_role = new_role
        if reason:
            env.channel_member_update.reason = reason
        env.timestamp = int(time.time() * 1000)
        return env

    def _add_history(self, ch: Channel, env: PbEnvelope,
                     from_nick: str, is_encrypted: bool = False) -> None:
        """Append a message to the channel's history buffer."""
        depth = self.config.history_depth
        if depth <= 0:
            return
        if ch.is_private and not self.config.private_history:
            return

        entry = HistoryEntry(
            timestamp=env.timestamp or int(time.time() * 1000),
            from_nick=from_nick,
            pb_bytes=env.SerializeToString(),
            is_encrypted=is_encrypted,
        )
        ch.history.append(entry)

        # Trim to depth
        if len(ch.history) > depth:
            ch.history = ch.history[-depth:]

    def _prune_history_ttl(self, ch: Channel) -> None:
        """Remove history entries older than history_ttl."""
        if self.config.history_ttl <= 0:
            return
        cutoff = int(time.time() * 1000) - (self.config.history_ttl * 1000)
        ch.history = [e for e in ch.history if e.timestamp >= cutoff]

    # ------------------------------------------------------------------
    # Public API — channel lifecycle
    # ------------------------------------------------------------------

    def get_channel(self, channel_id: str) -> Channel | None:
        """Get a channel by ID, or None."""
        return self._channels.get(channel_id)

    def get_all_channels(self) -> list[Channel]:
        """Get all channels."""
        return list(self._channels.values())

    def get_channel_count(self) -> int:
        """Total number of channels."""
        return len(self._channels)

    def get_user_channels(self, nick: str) -> list[Channel]:
        """Get all channels a user has joined."""
        return [ch for ch in self._channels.values() if ch.has_member(nick)]

    # ------------------------------------------------------------------
    # Dispatch — called from hub_plugin._route_hub()
    # ------------------------------------------------------------------

    def handle_channel_action(self, sender: str, env: PbEnvelope) -> None:
        """Dispatch a PbChannel action from a client."""
        if not self.config.enabled:
            self._status_fn(sender, PbStatus.ERROR, 60,
                            "Channels are not enabled on this hub")
            return

        pb_ch = env.channel
        action = pb_ch.action

        if action == PbChannel.LIST_CHANNELS:
            self._handle_list(sender)
        elif action == PbChannel.CREATE:
            self._handle_create(
                sender, pb_ch.channel_id, pb_ch.name,
                pb_ch.topic, pb_ch.is_private,
            )
        elif action == PbChannel.DELETE:
            self._handle_delete(sender, pb_ch.channel_id)
        elif action == PbChannel.JOIN:
            self._handle_join(sender, pb_ch.channel_id)
        elif action == PbChannel.LEAVE:
            self._handle_leave(sender, pb_ch.channel_id)
        elif action == PbChannel.SET_TOPIC:
            self._handle_set_topic(sender, pb_ch.channel_id, pb_ch.topic)
        elif action == PbChannel.KICK:
            self._handle_kick(sender, pb_ch.channel_id,
                              pb_ch.target_nick, "")
        elif action == PbChannel.SET_ROLE:
            self._handle_set_role(sender, pb_ch.channel_id,
                                  pb_ch.target_nick, pb_ch.target_role)
        else:
            self._status_fn(sender, PbStatus.ERROR, 61,
                            f"Unknown channel action: {action}")

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _handle_list(self, sender: str) -> None:
        """Send the channel list to a user."""
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, to_nick=sender,
        )
        for ch in self._channels.values():
            info = env.channel_list.channels.add()
            info.channel_id = ch.channel_id
            info.name = ch.name
            info.topic = ch.topic
            info.member_count = ch.member_count
            info.is_private = ch.is_private
            info.owner_nick = ch.owner_nick
            info.created_at = ch.created_at
        env.timestamp = int(time.time() * 1000)
        self._send_to_member(sender, env)
        log.debug(f"Channel list sent to {sender} "
                  f"({len(self._channels)} channels)")

    def _handle_create(
        self,
        sender: str,
        channel_id: str,
        name: str,
        topic: str,
        is_private: bool,
    ) -> None:
        """Create a new channel."""
        # Validate channel_id
        if not channel_id:
            self._status_fn(sender, PbStatus.ERROR, 62,
                            "Channel ID is required")
            return

        channel_id = channel_id.lower()

        if not self._validate_channel_id(channel_id):
            self._status_fn(sender, PbStatus.ERROR, 62,
                            f"Invalid channel ID '{channel_id}' — "
                            "must be lowercase alphanumeric/hyphens, 1-32 chars")
            return

        if channel_id in self._channels:
            self._status_fn(sender, PbStatus.ERROR, 63,
                            f"Channel '{channel_id}' already exists")
            return

        # Hub limit
        if len(self._channels) >= self.config.max_per_hub:
            self._status_fn(sender, PbStatus.ERROR, 64,
                            f"Hub channel limit ({self.config.max_per_hub}) reached")
            return

        # Permission check
        user_class = self._get_user_class(sender)
        if is_private:
            if not self.config.private_enabled:
                self._status_fn(sender, PbStatus.ERROR, 65,
                                "Private channels are disabled on this hub")
                return
            if user_class < self.config.private_create_min_class:
                self._status_fn(sender, PbStatus.ERROR, 66,
                                "Insufficient privileges to create private channels")
                return
        else:
            if user_class < self.config.create_min_class:
                self._status_fn(sender, PbStatus.ERROR, 66,
                                "Insufficient privileges to create channels")
                return

        # Name length
        display_name = name or f"#{channel_id}"
        if len(display_name) > self.config.name_max_length:
            self._status_fn(sender, PbStatus.ERROR, 67,
                            f"Channel name too long (max {self.config.name_max_length})")
            return

        # Topic length
        if topic and len(topic) > self.config.topic_max_length:
            self._status_fn(sender, PbStatus.ERROR, 68,
                            f"Topic too long (max {self.config.topic_max_length})")
            return

        # Per-user channel limit (creating also joins)
        if self._user_channel_count(sender) >= self.config.max_per_user:
            self._status_fn(sender, PbStatus.ERROR, 73,
                            f"You have joined the maximum number of channels "
                            f"({self.config.max_per_user})")
            return

        # Create
        now_ms = int(time.time() * 1000)
        ch = Channel(
            channel_id=channel_id,
            name=display_name,
            topic=topic,
            is_private=is_private,
            owner_nick=sender,
            created_at=now_ms,
        )
        ch.add_member(sender, ChannelRole.OWNER)
        self._channels[channel_id] = ch
        self.stats["channels_created"] += 1
        log.info(f"Channel #{channel_id} created by {sender} "
                 f"(private={is_private})")

        # Send PbChannelCreated to creator
        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, to_nick=sender,
        )
        env.channel_created.channel_id = channel_id
        env.channel_created.owner_nick = sender
        env.channel_created.is_private = is_private
        env.timestamp = now_ms
        self._send_to_member(sender, env)

        # Broadcast channel existence to all NMDCpb users (public only)
        if not is_private:
            bcast = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
            bcast.channel_created.channel_id = channel_id
            bcast.channel_created.owner_nick = sender
            bcast.channel_created.is_private = False
            bcast.timestamp = now_ms
            self._broadcast_to_channel(GENERAL_CHANNEL_ID, bcast,
                                       exclude=sender)

    def _handle_delete(self, sender: str, channel_id: str) -> None:
        """Delete a channel."""
        if channel_id == GENERAL_CHANNEL_ID:
            self._status_fn(sender, PbStatus.ERROR, 69,
                            "#general cannot be deleted")
            return

        ch = self._channels.get(channel_id)
        if not ch:
            self._status_fn(sender, PbStatus.ERROR, 70,
                            f"Channel '{channel_id}' not found")
            return

        # Permission: owner or hub admin (class >= 5)
        user_class = self._get_user_class(sender)
        if not ch.is_owner(sender) and user_class < 5:
            self._status_fn(sender, PbStatus.ERROR, 71,
                            "Only the channel owner or hub admins can delete channels")
            return

        # Notify all members
        update = self._make_member_update(
            channel_id, sender,
            PbChannelMemberUpdate.LEFT,
            reason="Channel deleted",
        )
        self._broadcast_to_channel(channel_id, update)

        del self._channels[channel_id]
        self.stats["channels_deleted"] += 1
        log.info(f"Channel #{channel_id} deleted by {sender}")

    def _handle_join(self, sender: str, channel_id: str) -> None:
        """Join a channel."""
        ch = self._channels.get(channel_id)
        if not ch:
            self._status_fn(sender, PbStatus.ERROR, 70,
                            f"Channel '{channel_id}' not found")
            return

        if ch.has_member(sender):
            # Already a member — silently succeed (or re-send history)
            return

        # Private channels: must be invited
        if ch.is_private:
            if sender not in ch.pending_invites:
                self._status_fn(sender, PbStatus.ERROR, 72,
                                "Private channel — invitation required")
                return
            del ch.pending_invites[sender]

        # Per-user channel limit
        if self._user_channel_count(sender) >= self.config.max_per_user:
            self._status_fn(sender, PbStatus.ERROR, 73,
                            f"You have joined the maximum number of channels "
                            f"({self.config.max_per_user})")
            return

        # Per-channel member limit
        max_members = (self.config.private_max_members if ch.is_private
                       else self.config.max_members)
        if ch.member_count >= max_members:
            self._status_fn(sender, PbStatus.ERROR, 74,
                            f"Channel is full ({max_members} members)")
            return

        # Add member
        ch.add_member(sender, ChannelRole.MEMBER)
        self.stats["channel_joins"] += 1
        log.info(f"{sender} joined #{channel_id}")

        # Notify existing members
        update = self._make_member_update(
            channel_id, sender, PbChannelMemberUpdate.JOINED,
        )
        self._broadcast_to_channel(channel_id, update, exclude=sender)

        # Send history to the joining user
        self._send_history(sender, ch)

        # Private: trigger key rotation so existing members re-key
        if ch.is_private and len(ch.members) > 1:
            self._trigger_key_rotation(ch, sender, "member_joined")

    def _handle_leave(self, sender: str, channel_id: str) -> None:
        """Leave a channel."""
        if channel_id == GENERAL_CHANNEL_ID:
            self._status_fn(sender, PbStatus.ERROR, 75,
                            "Cannot leave #general")
            return

        ch = self._channels.get(channel_id)
        if not ch:
            self._status_fn(sender, PbStatus.ERROR, 70,
                            f"Channel '{channel_id}' not found")
            return

        old_role = ch.remove_member(sender)
        if old_role is None:
            return  # Not a member — silently ignore

        self.stats["channel_leaves"] += 1
        log.info(f"{sender} left #{channel_id}")

        # Notify remaining members
        update = self._make_member_update(
            channel_id, sender, PbChannelMemberUpdate.LEFT,
        )
        self._broadcast_to_channel(channel_id, update)

        # If owner left, transfer ownership or delete
        if old_role == ChannelRole.OWNER and ch.members:
            self._transfer_ownership(ch)
        elif not ch.members:
            # Empty channel — auto-delete (except #general)
            if channel_id != GENERAL_CHANNEL_ID:
                del self._channels[channel_id]
                self.stats["channels_deleted"] += 1
                log.info(f"Channel #{channel_id} auto-deleted (empty)")

        # For private channels: trigger sender key rotation
        if ch.is_private and ch.members:
            self._trigger_key_rotation(ch, sender, "member_left")

    def _handle_set_topic(
        self, sender: str, channel_id: str, topic: str,
    ) -> None:
        """Set the channel topic."""
        ch = self._channels.get(channel_id)
        if not ch:
            self._status_fn(sender, PbStatus.ERROR, 70,
                            f"Channel '{channel_id}' not found")
            return

        # Permission: owner, admin, or hub operator (class >= 3)
        user_class = self._get_user_class(sender)
        if not ch.is_admin_or_owner(sender) and user_class < 3:
            self._status_fn(sender, PbStatus.ERROR, 76,
                            "Insufficient privileges to set topic")
            return

        if len(topic) > self.config.topic_max_length:
            self._status_fn(sender, PbStatus.ERROR, 68,
                            f"Topic too long (max {self.config.topic_max_length})")
            return

        ch.topic = topic
        log.info(f"{sender} set topic of #{channel_id}: {topic[:60]}")

        # Broadcast topic change as a PbChannel SET_TOPIC to all members
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.channel.action = PbChannel.SET_TOPIC
        env.channel.channel_id = channel_id
        env.channel.topic = topic
        env.from_nick = sender
        env.timestamp = int(time.time() * 1000)
        self._broadcast_to_channel(channel_id, env)

    def _handle_kick(
        self, sender: str, channel_id: str, target_nick: str, reason: str,
    ) -> None:
        """Kick a user from a channel."""
        if not target_nick:
            self._status_fn(sender, PbStatus.ERROR, 77,
                            "Kick requires target_nick")
            return

        ch = self._channels.get(channel_id)
        if not ch:
            self._status_fn(sender, PbStatus.ERROR, 70,
                            f"Channel '{channel_id}' not found")
            return

        # Permission: owner, admin, or hub operator (class >= 3)
        user_class = self._get_user_class(sender)
        if not ch.is_admin_or_owner(sender) and user_class < 3:
            self._status_fn(sender, PbStatus.ERROR, 78,
                            "Insufficient privileges to kick users")
            return

        # Cannot kick the owner
        if ch.is_owner(target_nick):
            self._status_fn(sender, PbStatus.ERROR, 79,
                            "Cannot kick the channel owner")
            return

        old_role = ch.remove_member(target_nick)
        if old_role is None:
            self._status_fn(sender, PbStatus.ERROR, 80,
                            f"{target_nick} is not in #{channel_id}")
            return

        self.stats["channel_kicks"] += 1
        log.info(f"{sender} kicked {target_nick} from #{channel_id}"
                 + (f": {reason}" if reason else ""))

        # Notify all members (including the kicked user)
        update = self._make_member_update(
            channel_id, target_nick,
            PbChannelMemberUpdate.KICKED,
            reason=reason,
        )
        self._broadcast_to_channel(channel_id, update)
        # Also notify the kicked user directly
        self._send_to_member(target_nick, update)

        # Private: trigger key rotation
        if ch.is_private and ch.members:
            self._trigger_key_rotation(ch, target_nick, "member_kicked")

    def _handle_set_role(
        self, sender: str, channel_id: str, target_nick: str, pb_role: int,
    ) -> None:
        """Change a member's role in a channel."""
        if not target_nick:
            self._status_fn(sender, PbStatus.ERROR, 77,
                            "SET_ROLE requires target_nick")
            return

        ch = self._channels.get(channel_id)
        if not ch:
            self._status_fn(sender, PbStatus.ERROR, 70,
                            f"Channel '{channel_id}' not found")
            return

        # Only owners can change roles (or hub admins class >= 5)
        user_class = self._get_user_class(sender)
        if not ch.is_owner(sender) and user_class < 5:
            self._status_fn(sender, PbStatus.ERROR, 81,
                            "Only the channel owner or hub admins can change roles")
            return

        if not ch.has_member(target_nick):
            self._status_fn(sender, PbStatus.ERROR, 80,
                            f"{target_nick} is not in #{channel_id}")
            return

        new_role = _PB_ROLE_MAP.get(pb_role, ChannelRole.MEMBER)

        # Cannot demote yourself if you're the owner
        if target_nick == sender and ch.is_owner(sender):
            self._status_fn(sender, PbStatus.ERROR, 82,
                            "Owner cannot change their own role — "
                            "transfer ownership first")
            return

        # If promoting to OWNER, demote current owner to ADMIN
        if new_role == ChannelRole.OWNER:
            old_owner = ch.owner_nick
            if old_owner and old_owner in ch.members:
                ch.members[old_owner] = ChannelRole.ADMIN
            ch.owner_nick = target_nick

        ch.members[target_nick] = new_role
        log.info(f"{sender} set {target_nick}'s role in #{channel_id} "
                 f"to {new_role.name}")

        # Notify
        update = self._make_member_update(
            channel_id, target_nick,
            PbChannelMemberUpdate.ROLE_CHANGED,
            new_role=pb_role,
        )
        self._broadcast_to_channel(channel_id, update)

    # ------------------------------------------------------------------
    # Channel invitations (private channels)
    # ------------------------------------------------------------------

    def handle_channel_invite(self, sender: str, env: PbEnvelope) -> None:
        """Process a PbChannelInvite."""
        inv = env.channel_invite
        channel_id = inv.channel_id
        target_nick = inv.target_nick

        ch = self._channels.get(channel_id)
        if not ch:
            self._status_fn(sender, PbStatus.ERROR, 70,
                            f"Channel '{channel_id}' not found")
            return

        if not ch.is_private:
            self._status_fn(sender, PbStatus.ERROR, 83,
                            "Public channels don't require invitations — "
                            "use JOIN directly")
            return

        # Permission: owner or admin
        if not ch.is_admin_or_owner(sender):
            self._status_fn(sender, PbStatus.ERROR, 84,
                            "Only channel owners/admins can invite")
            return

        # Record pending invite
        ch.pending_invites[target_nick] = sender
        self.stats["channel_invites_sent"] += 1

        # Forward invite to target
        fwd = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, to_nick=target_nick,
        )
        fwd.channel_invite.channel_id = channel_id
        fwd.channel_invite.target_nick = target_nick
        fwd.channel_invite.inviter_nick = sender
        fwd.timestamp = int(time.time() * 1000)
        self._send_to_member(target_nick, fwd)
        log.info(f"{sender} invited {target_nick} to #{channel_id}")

    def handle_channel_invite_response(
        self, sender: str, env: PbEnvelope,
    ) -> None:
        """Process a PbChannelInviteResponse."""
        resp = env.channel_invite_response
        channel_id = resp.channel_id

        ch = self._channels.get(channel_id)
        if not ch:
            self._status_fn(sender, PbStatus.ERROR, 70,
                            f"Channel '{channel_id}' not found")
            return

        if sender not in ch.pending_invites:
            self._status_fn(sender, PbStatus.ERROR, 85,
                            "No pending invitation for this channel")
            return

        inviter = ch.pending_invites[sender]

        if resp.accepted:
            # Process join (which will clear the pending invite)
            self._handle_join(sender, channel_id)
        else:
            del ch.pending_invites[sender]
            # Notify inviter
            self._status_fn(inviter, PbStatus.INFO, 86,
                            f"{sender} declined the invitation to "
                            f"#{channel_id}")

    # ------------------------------------------------------------------
    # Channel message routing
    # ------------------------------------------------------------------

    def route_channel_chat(
        self, sender: str, env: PbEnvelope,
    ) -> bool:
        """Route a PbChat with channel_id to channel members.

        Returns True if the message was handled (had a channel_id),
        False if it should fall through to normal broadcast routing.
        """
        if not env.HasField("chat"):
            return False

        channel_id = env.chat.channel_id
        if not channel_id:
            return False

        ch = self._channels.get(channel_id)
        if not ch:
            self._status_fn(sender, PbStatus.ERROR, 70,
                            f"Channel '{channel_id}' not found")
            return True

        if not ch.has_member(sender):
            self._status_fn(sender, PbStatus.ERROR, 87,
                            f"You are not a member of #{channel_id}")
            return True

        # Private channels must use encrypted channel messages
        if ch.is_private:
            self._status_fn(sender, PbStatus.ERROR, 90,
                            "Private channels require encrypted messages "
                            "(use channel_encrypted)")
            return True

        # Check readonly
        role = ch.get_role(sender)
        if role == ChannelRole.READONLY:
            self._status_fn(sender, PbStatus.ERROR, 88,
                            "You are read-only in this channel")
            return True

        # Broadcast to channel members (exclude sender)
        env.from_nick = sender
        if not env.timestamp:
            env.timestamp = int(time.time() * 1000)

        self._broadcast_to_channel(channel_id, env, exclude=sender)
        self.stats["channel_messages_routed"] += 1

        # Add to history
        self._add_history(ch, env, sender)

        return True

    def route_channel_encrypted(
        self, sender: str, env: PbEnvelope,
    ) -> bool:
        """Route a PbChannelEncrypted message to channel members.

        Returns True if handled.
        """
        if not env.HasField("channel_encrypted"):
            return False

        enc = env.channel_encrypted
        channel_id = enc.channel_id
        if not channel_id:
            return False

        ch = self._channels.get(channel_id)
        if not ch:
            self._status_fn(sender, PbStatus.ERROR, 70,
                            f"Channel '{channel_id}' not found")
            return True

        if not ch.is_private:
            self._status_fn(sender, PbStatus.ERROR, 89,
                            "Encrypted messages only in private channels")
            return True

        if not ch.has_member(sender):
            self._status_fn(sender, PbStatus.ERROR, 87,
                            f"You are not a member of #{channel_id}")
            return True

        env.from_nick = sender
        if not env.timestamp:
            env.timestamp = int(time.time() * 1000)

        self._broadcast_to_channel(channel_id, env, exclude=sender)
        self.stats["channel_messages_routed"] += 1

        # Add encrypted message to history (opaque blob)
        self._add_history(ch, env, sender, is_encrypted=True)

        return True

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _send_history(self, nick: str, ch: Channel) -> None:
        """Send recent history to a user who just joined a channel."""
        self._prune_history_ttl(ch)

        if not ch.history:
            return

        env = WireCodec.make_envelope(
            route=PbEnvelope.DIRECT, to_nick=nick,
        )
        env.channel_history.channel_id = ch.channel_id

        for entry in ch.history:
            if entry.is_encrypted:
                # Replay encrypted messages via channel_history.encrypted_messages
                try:
                    stored = PbEnvelope()
                    stored.ParseFromString(entry.pb_bytes)
                    if stored.HasField("channel_encrypted"):
                        enc_msg = env.channel_history.encrypted_messages.add()
                        enc_msg.CopyFrom(stored.channel_encrypted)
                except Exception:
                    pass
            else:
                # Replay plaintext chat messages
                try:
                    stored = PbEnvelope()
                    stored.ParseFromString(entry.pb_bytes)
                    if stored.HasField("chat"):
                        chat_msg = env.channel_history.messages.add()
                        chat_msg.CopyFrom(stored.chat)
                except Exception:
                    pass

        env.timestamp = int(time.time() * 1000)
        self._send_to_member(nick, env)
        self.stats["channel_history_replays"] += 1
        log.debug(f"Sent {len(ch.history)} history entries to {nick} "
                  f"in #{ch.channel_id}")

    # ------------------------------------------------------------------
    # User lifecycle hooks
    # ------------------------------------------------------------------

    def on_user_login(self, nick: str) -> None:
        """Called when a NMDCpb user logs in — auto-join #general, send channel list."""
        general = self._ensure_general()
        if not general.has_member(nick):
            general.add_member(nick, ChannelRole.MEMBER)
            log.debug(f"{nick} auto-joined #general")

            # Notify existing #general members
            update = self._make_member_update(
                GENERAL_CHANNEL_ID, nick, PbChannelMemberUpdate.JOINED,
            )
            self._broadcast_to_channel(GENERAL_CHANNEL_ID, update,
                                       exclude=nick)

        # Send channel list
        self._handle_list(nick)

        # Send #general history
        self._send_history(nick, general)

    def on_user_logout(self, nick: str) -> None:
        """Called when a NMDCpb user logs out — remove from all channels."""
        for ch in list(self._channels.values()):
            if not ch.has_member(nick):
                continue

            old_role = ch.remove_member(nick)

            # Notify remaining members
            update = self._make_member_update(
                ch.channel_id, nick, PbChannelMemberUpdate.LEFT,
            )
            self._broadcast_to_channel(ch.channel_id, update)

            # Ownership transfer if needed
            if old_role == ChannelRole.OWNER and ch.members:
                self._transfer_ownership(ch)

            # Auto-delete empty channels (except #general)
            if not ch.members and ch.channel_id != GENERAL_CHANNEL_ID:
                del self._channels[ch.channel_id]
                self.stats["channels_deleted"] += 1
                log.info(f"Channel #{ch.channel_id} auto-deleted (empty)")

            # Private channel: trigger key rotation on leave
            if ch.is_private and ch.members:
                self._trigger_key_rotation(ch, nick, "member_left")

    # ------------------------------------------------------------------
    # Sender key rotation (private channels)
    # ------------------------------------------------------------------

    def _trigger_key_rotation(
        self, ch: Channel, trigger_nick: str, reason: str,
    ) -> None:
        """Broadcast PbSenderKeyRotation to all remaining members."""
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.sender_key_rotation.channel_id = ch.channel_id
        env.sender_key_rotation.reason = reason
        env.sender_key_rotation.trigger_nick = trigger_nick
        env.timestamp = int(time.time() * 1000)
        self._broadcast_to_channel(ch.channel_id, env)
        log.info(f"Sender key rotation triggered in #{ch.channel_id} "
                 f"(reason={reason}, trigger={trigger_nick})")

    def route_sender_key_rotation(
        self, sender: str, env: PbEnvelope,
    ) -> bool:
        """Route a PbSenderKeyRotation message to channel members.

        Hub-originated rotations (from _trigger_key_rotation) are sent
        directly via _broadcast_to_channel.  This method handles the
        edge case where a *client* sends a sender_key_rotation BROADCAST
        (e.g. requesting manual rotation).  We validate membership and
        re-broadcast.

        Returns True if handled.
        """
        if not env.HasField("sender_key_rotation"):
            return False

        skr = env.sender_key_rotation
        channel_id = skr.channel_id
        if not channel_id:
            return False

        ch = self._channels.get(channel_id)
        if not ch:
            self._status_fn(sender, PbStatus.ERROR, 70,
                            f"Channel '{channel_id}' not found")
            return True

        if not ch.is_private:
            self._status_fn(sender, PbStatus.ERROR, 89,
                            "Key rotation only applies to private channels")
            return True

        if not ch.has_member(sender):
            self._status_fn(sender, PbStatus.ERROR, 87,
                            f"You are not a member of #{channel_id}")
            return True

        # Re-broadcast the rotation request to all members
        env.from_nick = sender
        if not env.timestamp:
            env.timestamp = int(time.time() * 1000)
        self._broadcast_to_channel(channel_id, env, exclude=sender)
        log.info(f"Client {sender} triggered key rotation in #{channel_id}")
        return True

    def force_rotate_keys(self, channel_id: str, admin_nick: str) -> str:
        """Admin-initiated key rotation for a private channel.

        Returns a status message string.
        """
        ch = self._channels.get(channel_id)
        if not ch:
            return f"Channel '{channel_id}' not found"

        if not ch.is_private:
            return f"#{channel_id} is not a private channel"

        if not ch.members:
            return f"#{channel_id} has no members"

        self._trigger_key_rotation(ch, admin_nick, "manual")
        return f"Key rotation triggered in #{channel_id}"

    # ------------------------------------------------------------------
    # Ownership transfer
    # ------------------------------------------------------------------

    def _transfer_ownership(self, ch: Channel) -> None:
        """Transfer ownership to the highest-ranked remaining member."""
        # Priority: existing admins, then oldest member
        new_owner = None
        for nick, role in ch.members.items():
            if role == ChannelRole.ADMIN:
                new_owner = nick
                break
        if not new_owner:
            # Just pick the first member
            new_owner = next(iter(ch.members))

        ch.members[new_owner] = ChannelRole.OWNER
        ch.owner_nick = new_owner
        log.info(f"Ownership of #{ch.channel_id} transferred to {new_owner}")

        # Notify
        update = self._make_member_update(
            ch.channel_id, new_owner,
            PbChannelMemberUpdate.ROLE_CHANGED,
            new_role=CHANNEL_OWNER,
        )
        self._broadcast_to_channel(ch.channel_id, update)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def prune_expired_history(self) -> int:
        """Prune expired history across all channels. Returns entries removed."""
        total = 0
        for ch in self._channels.values():
            before = len(ch.history)
            self._prune_history_ttl(ch)
            total += before - len(ch.history)
        return total

    def get_stats_summary(self) -> str:
        """Return a formatted stats summary."""
        s = self.stats
        return (
            f"Channels: {len(self._channels)}\n"
            f"  Created: {s['channels_created']}\n"
            f"  Deleted: {s['channels_deleted']}\n"
            f"  Joins:   {s['channel_joins']}\n"
            f"  Leaves:  {s['channel_leaves']}\n"
            f"  Kicks:   {s['channel_kicks']}\n"
            f"  Messages routed: {s['channel_messages_routed']}\n"
            f"  Invites: {s['channel_invites_sent']}\n"
            f"  History replays: {s['channel_history_replays']}"
        )
