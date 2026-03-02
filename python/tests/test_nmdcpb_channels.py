"""
Unit and integration tests for NMDCpb Channel Manager.

Tests the channel lifecycle, membership, permissions, history,
#general auto-join, and message routing.
"""

import time
import unittest

from verlihub.client.nmdcpb.nmdcpb_pb2 import (
    PbEnvelope,
    PbChat,
    PbStatus,
    PbChannel,
    PbChannelList,
    PbChannelCreated,
    PbChannelMemberUpdate,
    PbChannelInvite,
    PbChannelInviteResponse,
    PbChannelHistory,
    PbChannelEncrypted,
    PbSenderKeyRotation,
    CHANNEL_MEMBER,
    CHANNEL_ADMIN,
    CHANNEL_OWNER,
    CHANNEL_READONLY,
)
from verlihub.client.nmdcpb.wire import WireCodec
from verlihub.client.nmdcpb.channel_manager import (
    ChannelManager,
    ChannelConfig,
    ChannelRole,
    Channel,
    GENERAL_CHANNEL_ID,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class ChannelTestHelper:
    """Captures messages sent to users during channel operations."""

    def __init__(self):
        self.sent: dict[str, list[str]] = {}   # nick → [wire_data]
        self.statuses: dict[str, list[tuple]] = {}  # nick → [(sev, code, msg)]
        self.user_classes: dict[str, int] = {}   # nick → class

    def send_fn(self, data: str, nick: str) -> bool:
        self.sent.setdefault(nick, []).append(data)
        return True

    def status_fn(self, nick: str, severity: int, code: int, message: str) -> None:
        self.statuses.setdefault(nick, []).append((severity, code, message))

    def get_user_class(self, nick: str) -> int:
        return self.user_classes.get(nick, 1)

    def clear(self):
        self.sent.clear()
        self.statuses.clear()

    def decode_last(self, nick: str) -> PbEnvelope | None:
        """Decode the last wire message sent to nick."""
        msgs = self.sent.get(nick, [])
        if not msgs:
            return None
        return WireCodec.decode(msgs[-1])

    def decode_all(self, nick: str) -> list[PbEnvelope]:
        """Decode all wire messages sent to nick."""
        result = []
        for wire in self.sent.get(nick, []):
            env = WireCodec.decode(wire)
            if env:
                result.append(env)
        return result

    def has_status(self, nick: str, code: int) -> bool:
        """Check if a status with given code was sent."""
        return any(s[1] == code for s in self.statuses.get(nick, []))

    def last_status_code(self, nick: str) -> int | None:
        statuses = self.statuses.get(nick, [])
        return statuses[-1][1] if statuses else None


def make_manager(helper: ChannelTestHelper, **kwargs) -> ChannelManager:
    """Create a ChannelManager wired to the test helper."""
    cfg = ChannelConfig(**kwargs)
    return ChannelManager(
        config=cfg,
        send_fn=helper.send_fn,
        status_fn=helper.status_fn,
        get_user_class_fn=helper.get_user_class,
    )


# ---------------------------------------------------------------------------
# Tests: Channel creation and #general
# ---------------------------------------------------------------------------

class TestChannelGeneral(unittest.TestCase):
    """Tests for #general channel auto-creation and behavior."""

    def setUp(self):
        self.h = ChannelTestHelper()
        self.cm = make_manager(self.h)

    def test_general_exists_on_init(self):
        ch = self.cm.get_channel(GENERAL_CHANNEL_ID)
        self.assertIsNotNone(ch)
        self.assertEqual(ch.name, "#general")
        self.assertFalse(ch.is_private)
        self.assertEqual(ch.topic, "Main chat")

    def test_user_login_auto_joins_general(self):
        self.cm.on_user_login("alice")
        ch = self.cm.get_channel(GENERAL_CHANNEL_ID)
        self.assertTrue(ch.has_member("alice"))
        self.assertEqual(ch.member_count, 1)

    def test_user_login_sends_channel_list(self):
        self.cm.on_user_login("alice")
        envs = self.h.decode_all("alice")
        # Should receive at least a channel list
        list_envs = [e for e in envs if e.HasField("channel_list")]
        self.assertTrue(len(list_envs) >= 1)
        cl = list_envs[0].channel_list
        ids = [ch.channel_id for ch in cl.channels]
        self.assertIn(GENERAL_CHANNEL_ID, ids)

    def test_cannot_leave_general(self):
        self.cm.on_user_login("alice")
        self.h.clear()
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.LEAVE
        env.channel.channel_id = GENERAL_CHANNEL_ID
        self.cm.handle_channel_action("alice", env)
        self.assertTrue(self.h.has_status("alice", 75))
        # Still a member
        ch = self.cm.get_channel(GENERAL_CHANNEL_ID)
        self.assertTrue(ch.has_member("alice"))

    def test_cannot_delete_general(self):
        self.cm.on_user_login("alice")
        self.h.clear()
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.DELETE
        env.channel.channel_id = GENERAL_CHANNEL_ID
        self.cm.handle_channel_action("alice", env)
        self.assertTrue(self.h.has_status("alice", 69))

    def test_multiple_logins_notify_existing(self):
        self.cm.on_user_login("alice")
        self.h.clear()
        self.cm.on_user_login("bob")
        # alice should get a member update about bob joining
        envs = self.h.decode_all("alice")
        member_updates = [e for e in envs
                          if e.HasField("channel_member_update")]
        self.assertTrue(len(member_updates) >= 1)
        up = member_updates[0].channel_member_update
        self.assertEqual(up.nick, "bob")
        self.assertEqual(up.update_type, PbChannelMemberUpdate.JOINED)


# ---------------------------------------------------------------------------
# Tests: Channel creation
# ---------------------------------------------------------------------------

class TestChannelCreation(unittest.TestCase):

    def setUp(self):
        self.h = ChannelTestHelper()
        self.cm = make_manager(self.h)
        self.cm.on_user_login("alice")
        self.h.clear()

    def _create(self, sender, channel_id, name="", topic="",
                is_private=False):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = channel_id
        env.channel.name = name
        env.channel.topic = topic
        env.channel.is_private = is_private
        self.cm.handle_channel_action(sender, env)

    def test_create_public_channel(self):
        self._create("alice", "tech", "#tech", "Technical discussion")
        ch = self.cm.get_channel("tech")
        self.assertIsNotNone(ch)
        self.assertEqual(ch.name, "#tech")
        self.assertEqual(ch.topic, "Technical discussion")
        self.assertFalse(ch.is_private)
        self.assertEqual(ch.owner_nick, "alice")
        self.assertTrue(ch.has_member("alice"))
        self.assertEqual(ch.get_role("alice"), ChannelRole.OWNER)

    def test_create_sends_channel_created(self):
        self._create("alice", "tech")
        env = self.h.decode_last("alice")
        self.assertTrue(env.HasField("channel_created"))
        self.assertEqual(env.channel_created.channel_id, "tech")
        self.assertEqual(env.channel_created.owner_nick, "alice")

    def test_create_duplicate_fails(self):
        self._create("alice", "tech")
        self.h.clear()
        self._create("alice", "tech")
        self.assertTrue(self.h.has_status("alice", 63))

    def test_create_invalid_id(self):
        self._create("alice", "UPPER CASE!!!")
        self.assertTrue(self.h.has_status("alice", 62))

    def test_create_empty_id(self):
        self._create("alice", "")
        self.assertTrue(self.h.has_status("alice", 62))

    def test_create_default_name(self):
        self._create("alice", "photos")
        ch = self.cm.get_channel("photos")
        self.assertEqual(ch.name, "#photos")

    def test_hub_limit(self):
        h = ChannelTestHelper()
        cm = make_manager(h, max_per_hub=2)
        cm.on_user_login("alice")
        h.clear()
        # #general already counts as 1
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "ch1"
        cm.handle_channel_action("alice", env)
        self.assertIsNotNone(cm.get_channel("ch1"))
        h.clear()
        env2 = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env2.channel.action = PbChannel.CREATE
        env2.channel.channel_id = "ch2"
        cm.handle_channel_action("alice", env2)
        self.assertTrue(h.has_status("alice", 64))

    def test_create_min_class(self):
        h = ChannelTestHelper()
        h.user_classes["bob"] = 0  # guest
        cm = make_manager(h, create_min_class=1)
        cm.on_user_login("bob")
        h.clear()
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "test"
        cm.handle_channel_action("bob", env)
        self.assertTrue(h.has_status("bob", 66))

    def test_create_private_channel(self):
        self._create("alice", "secret", is_private=True)
        ch = self.cm.get_channel("secret")
        self.assertIsNotNone(ch)
        self.assertTrue(ch.is_private)

    def test_create_private_disabled(self):
        h = ChannelTestHelper()
        cm = make_manager(h, private_enabled=False)
        cm.on_user_login("alice")
        h.clear()
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "secret"
        env.channel.is_private = True
        cm.handle_channel_action("alice", env)
        self.assertTrue(h.has_status("alice", 65))

    def test_name_too_long(self):
        self._create("alice", "ok", name="x" * 100)
        self.assertTrue(self.h.has_status("alice", 67))

    def test_topic_too_long(self):
        self._create("alice", "ok", topic="x" * 300)
        self.assertTrue(self.h.has_status("alice", 68))


# ---------------------------------------------------------------------------
# Tests: Join / Leave / Kick
# ---------------------------------------------------------------------------

class TestChannelMembership(unittest.TestCase):

    def setUp(self):
        self.h = ChannelTestHelper()
        self.cm = make_manager(self.h)
        self.cm.on_user_login("alice")
        self.cm.on_user_login("bob")
        # alice creates #tech
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "tech"
        self.cm.handle_channel_action("alice", env)
        self.h.clear()

    def _join(self, nick, channel_id):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.JOIN
        env.channel.channel_id = channel_id
        self.cm.handle_channel_action(nick, env)

    def _leave(self, nick, channel_id):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.LEAVE
        env.channel.channel_id = channel_id
        self.cm.handle_channel_action(nick, env)

    def _kick(self, sender, target, channel_id):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.KICK
        env.channel.channel_id = channel_id
        env.channel.target_nick = target
        self.cm.handle_channel_action(sender, env)

    def test_join_public_channel(self):
        self._join("bob", "tech")
        ch = self.cm.get_channel("tech")
        self.assertTrue(ch.has_member("bob"))
        self.assertEqual(ch.get_role("bob"), ChannelRole.MEMBER)

    def test_join_notifies_existing_members(self):
        self._join("bob", "tech")
        envs = self.h.decode_all("alice")
        updates = [e for e in envs if e.HasField("channel_member_update")]
        self.assertTrue(len(updates) >= 1)
        self.assertEqual(updates[0].channel_member_update.nick, "bob")

    def test_join_unknown_channel(self):
        self._join("bob", "nonexistent")
        self.assertTrue(self.h.has_status("bob", 70))

    def test_join_already_member(self):
        self._join("bob", "tech")
        self.h.clear()
        self._join("bob", "tech")  # Should silently succeed
        self.assertFalse(self.h.has_status("bob", 70))

    def test_per_user_channel_limit(self):
        h = ChannelTestHelper()
        cm = make_manager(h, max_per_user=2)
        cm.on_user_login("bob")  # auto-joins #general (1)
        # Create and join one channel
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "ch1"
        cm.handle_channel_action("bob", env)  # (2)
        # Create another
        env2 = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env2.channel.action = PbChannel.CREATE
        env2.channel.channel_id = "ch2"
        h.clear()
        cm.handle_channel_action("bob", env2)
        self.assertTrue(h.has_status("bob", 73))

    def test_channel_member_limit(self):
        h = ChannelTestHelper()
        cm = make_manager(h, max_members=2)
        cm.on_user_login("alice")
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "tiny"
        cm.handle_channel_action("alice", env)
        cm.on_user_login("bob")
        env2 = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env2.channel.action = PbChannel.JOIN
        env2.channel.channel_id = "tiny"
        cm.handle_channel_action("bob", env2)
        # Channel now has 2 members (alice + bob)
        cm.on_user_login("charlie")
        h.clear()
        env3 = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env3.channel.action = PbChannel.JOIN
        env3.channel.channel_id = "tiny"
        cm.handle_channel_action("charlie", env3)
        self.assertTrue(h.has_status("charlie", 74))

    def test_leave_channel(self):
        self._join("bob", "tech")
        self.h.clear()
        self._leave("bob", "tech")
        ch = self.cm.get_channel("tech")
        self.assertFalse(ch.has_member("bob"))

    def test_leave_notifies_remaining(self):
        self._join("bob", "tech")
        self.h.clear()
        self._leave("bob", "tech")
        envs = self.h.decode_all("alice")
        updates = [e for e in envs if e.HasField("channel_member_update")]
        self.assertTrue(len(updates) >= 1)
        self.assertEqual(updates[0].channel_member_update.update_type,
                         PbChannelMemberUpdate.LEFT)

    def test_kick(self):
        self._join("bob", "tech")
        self.h.clear()
        self._kick("alice", "bob", "tech")
        ch = self.cm.get_channel("tech")
        self.assertFalse(ch.has_member("bob"))
        self.assertEqual(self.cm.stats["channel_kicks"], 1)

    def test_kick_requires_permission(self):
        self._join("bob", "tech")
        self.h.clear()
        self._kick("bob", "alice", "tech")  # bob is MEMBER, not admin
        self.assertTrue(self.h.has_status("bob", 78))

    def test_cannot_kick_owner(self):
        self._join("bob", "tech")
        # Promote bob to admin first
        self.cm.get_channel("tech").members["bob"] = ChannelRole.ADMIN
        self.h.clear()
        self._kick("bob", "alice", "tech")  # can't kick owner
        self.assertTrue(self.h.has_status("bob", 79))

    def test_kick_non_member(self):
        self._kick("alice", "bob", "tech")  # bob hasn't joined
        self.assertTrue(self.h.has_status("alice", 80))

    def test_empty_channel_auto_deleted(self):
        self._join("bob", "tech")
        self._leave("alice", "tech")
        self._leave("bob", "tech")
        self.assertIsNone(self.cm.get_channel("tech"))

    def test_ownership_transfer_on_leave(self):
        self._join("bob", "tech")
        self._leave("alice", "tech")
        ch = self.cm.get_channel("tech")
        self.assertIsNotNone(ch)
        self.assertEqual(ch.owner_nick, "bob")
        self.assertEqual(ch.get_role("bob"), ChannelRole.OWNER)


# ---------------------------------------------------------------------------
# Tests: Topic and Role management
# ---------------------------------------------------------------------------

class TestChannelManagement(unittest.TestCase):

    def setUp(self):
        self.h = ChannelTestHelper()
        self.cm = make_manager(self.h)
        self.cm.on_user_login("alice")
        self.cm.on_user_login("bob")
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "tech"
        self.cm.handle_channel_action("alice", env)
        env2 = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env2.channel.action = PbChannel.JOIN
        env2.channel.channel_id = "tech"
        self.cm.handle_channel_action("bob", env2)
        self.h.clear()

    def test_set_topic_by_owner(self):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.SET_TOPIC
        env.channel.channel_id = "tech"
        env.channel.topic = "New topic"
        self.cm.handle_channel_action("alice", env)
        ch = self.cm.get_channel("tech")
        self.assertEqual(ch.topic, "New topic")

    def test_set_topic_by_member_fails(self):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.SET_TOPIC
        env.channel.channel_id = "tech"
        env.channel.topic = "Unauthorized"
        self.cm.handle_channel_action("bob", env)
        self.assertTrue(self.h.has_status("bob", 76))

    def test_set_topic_by_hub_operator(self):
        self.h.user_classes["bob"] = 3  # Hub operator
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.SET_TOPIC
        env.channel.channel_id = "tech"
        env.channel.topic = "Op topic"
        self.cm.handle_channel_action("bob", env)
        ch = self.cm.get_channel("tech")
        self.assertEqual(ch.topic, "Op topic")

    def test_set_role(self):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.SET_ROLE
        env.channel.channel_id = "tech"
        env.channel.target_nick = "bob"
        env.channel.target_role = CHANNEL_ADMIN
        self.cm.handle_channel_action("alice", env)
        ch = self.cm.get_channel("tech")
        self.assertEqual(ch.get_role("bob"), ChannelRole.ADMIN)

    def test_set_role_by_non_owner_fails(self):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.SET_ROLE
        env.channel.channel_id = "tech"
        env.channel.target_nick = "alice"
        env.channel.target_role = CHANNEL_READONLY
        self.cm.handle_channel_action("bob", env)
        self.assertTrue(self.h.has_status("bob", 81))

    def test_owner_cannot_change_own_role(self):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.SET_ROLE
        env.channel.channel_id = "tech"
        env.channel.target_nick = "alice"
        env.channel.target_role = CHANNEL_MEMBER
        self.cm.handle_channel_action("alice", env)
        self.assertTrue(self.h.has_status("alice", 82))

    def test_transfer_ownership(self):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.SET_ROLE
        env.channel.channel_id = "tech"
        env.channel.target_nick = "bob"
        env.channel.target_role = CHANNEL_OWNER
        self.cm.handle_channel_action("alice", env)
        ch = self.cm.get_channel("tech")
        self.assertEqual(ch.owner_nick, "bob")
        self.assertEqual(ch.get_role("bob"), ChannelRole.OWNER)
        # alice should be demoted to ADMIN
        self.assertEqual(ch.get_role("alice"), ChannelRole.ADMIN)

    def test_delete_by_owner(self):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.DELETE
        env.channel.channel_id = "tech"
        self.cm.handle_channel_action("alice", env)
        self.assertIsNone(self.cm.get_channel("tech"))

    def test_delete_by_member_fails(self):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.DELETE
        env.channel.channel_id = "tech"
        self.cm.handle_channel_action("bob", env)
        self.assertTrue(self.h.has_status("bob", 71))
        self.assertIsNotNone(self.cm.get_channel("tech"))

    def test_delete_by_hub_admin(self):
        self.h.user_classes["bob"] = 5
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.DELETE
        env.channel.channel_id = "tech"
        self.cm.handle_channel_action("bob", env)
        self.assertIsNone(self.cm.get_channel("tech"))

    def test_list_channels(self):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.LIST_CHANNELS
        self.cm.handle_channel_action("bob", env)
        resp = self.h.decode_last("bob")
        self.assertTrue(resp.HasField("channel_list"))
        ids = [ch.channel_id for ch in resp.channel_list.channels]
        self.assertIn(GENERAL_CHANNEL_ID, ids)
        self.assertIn("tech", ids)


# ---------------------------------------------------------------------------
# Tests: Channel message routing
# ---------------------------------------------------------------------------

class TestChannelMessageRouting(unittest.TestCase):

    def setUp(self):
        self.h = ChannelTestHelper()
        self.cm = make_manager(self.h)
        self.cm.on_user_login("alice")
        self.cm.on_user_login("bob")
        self.cm.on_user_login("charlie")
        # Create #tech, alice+bob join, charlie stays out
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "tech"
        self.cm.handle_channel_action("alice", env)
        env2 = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env2.channel.action = PbChannel.JOIN
        env2.channel.channel_id = "tech"
        self.cm.handle_channel_action("bob", env2)
        self.h.clear()

    def test_channel_chat_routed_to_members(self):
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.chat.text = "Hello #tech!"
        env.chat.channel_id = "tech"
        handled = self.cm.route_channel_chat("alice", env)
        self.assertTrue(handled)
        # bob should receive it
        self.assertIn("bob", self.h.sent)
        # charlie should NOT (not in #tech)
        self.assertNotIn("charlie", self.h.sent)
        # alice (sender) should NOT get their own message
        self.assertNotIn("alice", self.h.sent)

    def test_channel_chat_no_channel_id_falls_through(self):
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.chat.text = "Normal broadcast"
        handled = self.cm.route_channel_chat("alice", env)
        self.assertFalse(handled)

    def test_non_member_cannot_send(self):
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.chat.text = "Hacking in"
        env.chat.channel_id = "tech"
        handled = self.cm.route_channel_chat("charlie", env)
        self.assertTrue(handled)  # Still "handled" (error returned)
        self.assertTrue(self.h.has_status("charlie", 87))
        self.assertNotIn("alice", self.h.sent)

    def test_readonly_cannot_send(self):
        ch = self.cm.get_channel("tech")
        ch.members["bob"] = ChannelRole.READONLY
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.chat.text = "Silenced"
        env.chat.channel_id = "tech"
        handled = self.cm.route_channel_chat("bob", env)
        self.assertTrue(handled)
        self.assertTrue(self.h.has_status("bob", 88))

    def test_general_chat_goes_to_all_pb_users(self):
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.chat.text = "Hello everyone!"
        env.chat.channel_id = GENERAL_CHANNEL_ID
        handled = self.cm.route_channel_chat("alice", env)
        self.assertTrue(handled)
        # Both bob and charlie are in #general
        self.assertIn("bob", self.h.sent)
        self.assertIn("charlie", self.h.sent)

    def test_stats_tracked(self):
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.chat.text = "Test"
        env.chat.channel_id = "tech"
        self.cm.route_channel_chat("alice", env)
        self.assertEqual(self.cm.stats["channel_messages_routed"], 1)


# ---------------------------------------------------------------------------
# Tests: Private channels and invitations
# ---------------------------------------------------------------------------

class TestPrivateChannels(unittest.TestCase):

    def setUp(self):
        self.h = ChannelTestHelper()
        self.cm = make_manager(self.h)
        self.cm.on_user_login("alice")
        self.cm.on_user_login("bob")
        # alice creates #secret (private)
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "secret"
        env.channel.is_private = True
        self.cm.handle_channel_action("alice", env)
        self.h.clear()

    def test_join_private_without_invite_fails(self):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.JOIN
        env.channel.channel_id = "secret"
        self.cm.handle_channel_action("bob", env)
        self.assertTrue(self.h.has_status("bob", 72))

    def test_invite_and_accept(self):
        # alice invites bob
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel_invite.channel_id = "secret"
        env.channel_invite.target_nick = "bob"
        self.cm.handle_channel_invite("alice", env)
        # bob should receive the invite
        envs = self.h.decode_all("bob")
        invites = [e for e in envs if e.HasField("channel_invite")]
        self.assertEqual(len(invites), 1)
        self.assertEqual(invites[0].channel_invite.inviter_nick, "alice")

        # bob accepts
        self.h.clear()
        resp = WireCodec.make_envelope(route=PbEnvelope.HUB)
        resp.channel_invite_response.channel_id = "secret"
        resp.channel_invite_response.accepted = True
        self.cm.handle_channel_invite_response("bob", resp)
        ch = self.cm.get_channel("secret")
        self.assertTrue(ch.has_member("bob"))

    def test_invite_and_decline(self):
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel_invite.channel_id = "secret"
        env.channel_invite.target_nick = "bob"
        self.cm.handle_channel_invite("alice", env)
        self.h.clear()
        resp = WireCodec.make_envelope(route=PbEnvelope.HUB)
        resp.channel_invite_response.channel_id = "secret"
        resp.channel_invite_response.accepted = False
        self.cm.handle_channel_invite_response("bob", resp)
        ch = self.cm.get_channel("secret")
        self.assertFalse(ch.has_member("bob"))

    def test_member_cannot_invite(self):
        # First add bob properly
        ch = self.cm.get_channel("secret")
        ch.add_member("bob", ChannelRole.MEMBER)
        self.h.clear()
        # bob tries to invite charlie
        self.cm.on_user_login("charlie")
        self.h.clear()
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel_invite.channel_id = "secret"
        env.channel_invite.target_nick = "charlie"
        self.cm.handle_channel_invite("bob", env)
        self.assertTrue(self.h.has_status("bob", 84))

    def test_encrypted_message_routing(self):
        # Add bob to the private channel
        ch = self.cm.get_channel("secret")
        ch.add_member("bob", ChannelRole.MEMBER)
        self.h.clear()
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.channel_encrypted.channel_id = "secret"
        env.channel_encrypted.sender_key_id = 42
        env.channel_encrypted.nonce = 1
        env.channel_encrypted.ciphertext = b"encrypted_data"
        env.channel_encrypted.from_nick = "alice"
        handled = self.cm.route_channel_encrypted("alice", env)
        self.assertTrue(handled)
        self.assertIn("bob", self.h.sent)

    def test_encrypted_in_public_channel_fails(self):
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.channel_encrypted.channel_id = GENERAL_CHANNEL_ID
        env.channel_encrypted.ciphertext = b"bad"
        handled = self.cm.route_channel_encrypted("alice", env)
        self.assertTrue(handled)
        self.assertTrue(self.h.has_status("alice", 89))

    def test_key_rotation_on_leave(self):
        ch = self.cm.get_channel("secret")
        ch.add_member("bob", ChannelRole.MEMBER)
        self.h.clear()
        # bob leaves
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.LEAVE
        env.channel.channel_id = "secret"
        self.cm.handle_channel_action("bob", env)
        # alice should receive a SenderKeyRotation
        envs = self.h.decode_all("alice")
        rotations = [e for e in envs if e.HasField("sender_key_rotation")]
        self.assertTrue(len(rotations) >= 1)
        self.assertEqual(rotations[0].sender_key_rotation.channel_id, "secret")
        self.assertEqual(rotations[0].sender_key_rotation.trigger_nick, "bob")


# ---------------------------------------------------------------------------
# Tests: Channel history
# ---------------------------------------------------------------------------

class TestChannelHistory(unittest.TestCase):

    def setUp(self):
        self.h = ChannelTestHelper()
        self.cm = make_manager(self.h, history_depth=5)
        self.cm.on_user_login("alice")
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "tech"
        self.cm.handle_channel_action("alice", env)
        self.h.clear()

    def test_history_sent_on_join(self):
        # alice sends messages
        for i in range(3):
            env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
            env.chat.text = f"Message {i}"
            env.chat.channel_id = "tech"
            env.timestamp = int(time.time() * 1000)
            self.cm.route_channel_chat("alice", env)
        self.h.clear()
        # bob joins
        self.cm.on_user_login("bob")
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.JOIN
        env.channel.channel_id = "tech"
        self.cm.handle_channel_action("bob", env)
        envs = self.h.decode_all("bob")
        history_envs = [e for e in envs if e.HasField("channel_history")]
        self.assertTrue(len(history_envs) >= 1)
        hist = history_envs[0].channel_history
        self.assertEqual(hist.channel_id, "tech")
        self.assertEqual(len(hist.messages), 3)

    def test_history_capped_at_depth(self):
        for i in range(10):
            env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
            env.chat.text = f"Msg {i}"
            env.chat.channel_id = "tech"
            env.timestamp = int(time.time() * 1000)
            self.cm.route_channel_chat("alice", env)
        ch = self.cm.get_channel("tech")
        self.assertLessEqual(len(ch.history), 5)

    def test_history_disabled(self):
        h = ChannelTestHelper()
        cm = make_manager(h, history_depth=0)
        cm.on_user_login("alice")
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "test"
        cm.handle_channel_action("alice", env)
        msg = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        msg.chat.text = "test"
        msg.chat.channel_id = "test"
        msg.timestamp = int(time.time() * 1000)
        cm.route_channel_chat("alice", msg)
        ch = cm.get_channel("test")
        self.assertEqual(len(ch.history), 0)


# ---------------------------------------------------------------------------
# Tests: User logout cleanup
# ---------------------------------------------------------------------------

class TestUserLogout(unittest.TestCase):

    def setUp(self):
        self.h = ChannelTestHelper()
        self.cm = make_manager(self.h)
        self.cm.on_user_login("alice")
        self.cm.on_user_login("bob")
        # Both in #general; alice also in #tech
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "tech"
        self.cm.handle_channel_action("alice", env)
        env2 = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env2.channel.action = PbChannel.JOIN
        env2.channel.channel_id = "tech"
        self.cm.handle_channel_action("bob", env2)
        self.h.clear()

    def test_logout_removes_from_all_channels(self):
        self.cm.on_user_logout("alice")
        general = self.cm.get_channel(GENERAL_CHANNEL_ID)
        self.assertFalse(general.has_member("alice"))
        tech = self.cm.get_channel("tech")
        # alice was owner, bob should be promoted
        self.assertIsNotNone(tech)
        self.assertFalse(tech.has_member("alice"))
        self.assertEqual(tech.owner_nick, "bob")

    def test_logout_notifies_remaining_members(self):
        self.cm.on_user_logout("alice")
        envs = self.h.decode_all("bob")
        updates = [e for e in envs if e.HasField("channel_member_update")]
        # Should get updates for both #general and #tech
        self.assertTrue(len(updates) >= 2)

    def test_logout_empty_channel_deleted(self):
        # Create a channel only alice is in
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "solo"
        self.cm.handle_channel_action("alice", env)
        self.h.clear()
        self.cm.on_user_logout("alice")
        self.assertIsNone(self.cm.get_channel("solo"))


# ---------------------------------------------------------------------------
# Tests: Disabled channels
# ---------------------------------------------------------------------------

class TestChannelsDisabled(unittest.TestCase):

    def test_disabled_rejects_actions(self):
        h = ChannelTestHelper()
        cm = make_manager(h, enabled=False)
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.LIST_CHANNELS
        cm.handle_channel_action("alice", env)
        self.assertTrue(h.has_status("alice", 60))


# ---------------------------------------------------------------------------
# Tests: Stats
# ---------------------------------------------------------------------------

class TestChannelStats(unittest.TestCase):

    def test_stats_summary(self):
        h = ChannelTestHelper()
        cm = make_manager(h)
        cm.on_user_login("alice")
        summary = cm.get_stats_summary()
        self.assertIn("Channels:", summary)
        self.assertIn("Created:", summary)

    def test_channel_count(self):
        h = ChannelTestHelper()
        cm = make_manager(h)
        cm.on_user_login("alice")
        self.assertEqual(cm.get_channel_count(), 1)  # #general
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "extra"
        cm.handle_channel_action("alice", env)
        self.assertEqual(cm.get_channel_count(), 2)

    def test_user_channels(self):
        h = ChannelTestHelper()
        cm = make_manager(h)
        cm.on_user_login("alice")
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "ch1"
        cm.handle_channel_action("alice", env)
        chs = cm.get_user_channels("alice")
        ids = [c.channel_id for c in chs]
        self.assertIn(GENERAL_CHANNEL_ID, ids)
        self.assertIn("ch1", ids)


# ---------------------------------------------------------------------------
# Tests: Key rotation on join + P2P enforcement
# ---------------------------------------------------------------------------

class TestKeyRotationOnJoin(unittest.TestCase):
    """Verify key rotation is triggered when a member joins a private channel."""

    def setUp(self):
        self.h = ChannelTestHelper()
        self.cm = make_manager(self.h)
        self.cm.on_user_login("alice")
        # Create a private channel
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "secret"
        env.channel.is_private = True
        self.cm.handle_channel_action("alice", env)

    def test_rotation_triggered_on_join(self):
        """When a new member joins a private channel, key rotation is sent."""
        # Invite bob and accept
        self.cm.on_user_login("bob")
        ch = self.cm.get_channel("secret")
        ch.pending_invites["bob"] = "alice"
        self.h.clear()
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.JOIN
        env.channel.channel_id = "secret"
        self.cm.handle_channel_action("bob", env)
        # alice should receive SenderKeyRotation with reason=member_joined
        envs = self.h.decode_all("alice")
        rotations = [e for e in envs if e.HasField("sender_key_rotation")]
        self.assertTrue(len(rotations) >= 1)
        skr = rotations[0].sender_key_rotation
        self.assertEqual(skr.channel_id, "secret")
        self.assertEqual(skr.reason, "member_joined")
        self.assertEqual(skr.trigger_nick, "bob")

    def test_no_rotation_on_first_member(self):
        """First member (owner) joining doesn't trigger rotation."""
        # alice is the only member after creation, no rotation should exist
        # The creation of the channel auto-adds alice; no rotation needed
        envs = self.h.decode_all("alice")
        rotations = [e for e in envs if e.HasField("sender_key_rotation")]
        self.assertEqual(len(rotations), 0)

    def test_no_rotation_on_public_join(self):
        """Joining a public channel does not trigger key rotation."""
        self.cm.on_user_login("bob")
        self.h.clear()
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.JOIN
        env.channel.channel_id = GENERAL_CHANNEL_ID
        self.cm.handle_channel_action("bob", env)
        envs = self.h.decode_all("alice")
        rotations = [e for e in envs if e.HasField("sender_key_rotation")]
        self.assertEqual(len(rotations), 0)

    def test_rotation_on_kick(self):
        """Kicking a member from private channel triggers rotation."""
        self.cm.on_user_login("bob")
        ch = self.cm.get_channel("secret")
        ch.pending_invites["bob"] = "alice"
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.JOIN
        env.channel.channel_id = "secret"
        self.cm.handle_channel_action("bob", env)
        self.h.clear()
        # alice kicks bob
        env2 = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env2.channel.action = PbChannel.KICK
        env2.channel.channel_id = "secret"
        env2.channel.target_nick = "bob"
        self.cm.handle_channel_action("alice", env2)
        envs = self.h.decode_all("alice")
        rotations = [e for e in envs if e.HasField("sender_key_rotation")]
        self.assertTrue(len(rotations) >= 1)
        self.assertEqual(rotations[0].sender_key_rotation.reason,
                         "member_kicked")


class TestPrivateChannelP2PEnforcement(unittest.TestCase):
    """Private channels must reject plain PbChat messages."""

    def setUp(self):
        self.h = ChannelTestHelper()
        self.cm = make_manager(self.h)
        self.cm.on_user_login("alice")
        self.cm.on_user_login("bob")
        # Create a private channel with both members
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "secret"
        env.channel.is_private = True
        self.cm.handle_channel_action("alice", env)
        ch = self.cm.get_channel("secret")
        ch.add_member("bob", ChannelRole.MEMBER)
        self.h.clear()

    def test_plaintext_chat_rejected_in_private_channel(self):
        """Plain PbChat in a private channel should be rejected."""
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.chat.text = "This should fail"
        env.chat.channel_id = "secret"
        handled = self.cm.route_channel_chat("alice", env)
        self.assertTrue(handled)
        self.assertTrue(self.h.has_status("alice", 90))
        # bob should NOT receive the message
        self.assertNotIn("bob", self.h.sent)

    def test_encrypted_message_still_works_in_private(self):
        """PbChannelEncrypted in private channel should succeed."""
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.channel_encrypted.channel_id = "secret"
        env.channel_encrypted.sender_key_id = 1
        env.channel_encrypted.nonce = 1
        env.channel_encrypted.ciphertext = b"encrypted"
        handled = self.cm.route_channel_encrypted("alice", env)
        self.assertTrue(handled)
        self.assertIn("bob", self.h.sent)

    def test_plaintext_chat_still_works_in_public(self):
        """Plain PbChat in a public channel should still work."""
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.chat.text = "Hello public"
        env.chat.channel_id = GENERAL_CHANNEL_ID
        handled = self.cm.route_channel_chat("alice", env)
        self.assertTrue(handled)
        self.assertIn("bob", self.h.sent)


# ---------------------------------------------------------------------------
# Tests: Sender key rotation routing
# ---------------------------------------------------------------------------

class TestSenderKeyRotationRouting(unittest.TestCase):
    """Test route_sender_key_rotation in ChannelManager."""

    def setUp(self):
        self.h = ChannelTestHelper()
        self.cm = make_manager(self.h)
        self.cm.on_user_login("alice")
        self.cm.on_user_login("bob")
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "secret"
        env.channel.is_private = True
        self.cm.handle_channel_action("alice", env)
        ch = self.cm.get_channel("secret")
        ch.add_member("bob", ChannelRole.MEMBER)
        self.h.clear()

    def test_rotation_routed_to_members(self):
        """Client-sent rotation is re-broadcast to channel members."""
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.sender_key_rotation.channel_id = "secret"
        env.sender_key_rotation.reason = "periodic"
        env.sender_key_rotation.trigger_nick = "alice"
        handled = self.cm.route_sender_key_rotation("alice", env)
        self.assertTrue(handled)
        # bob should get it, alice should not (excluded as sender)
        self.assertIn("bob", self.h.sent)

    def test_rotation_on_public_channel_rejected(self):
        """Key rotation on public channel is rejected."""
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.sender_key_rotation.channel_id = GENERAL_CHANNEL_ID
        env.sender_key_rotation.reason = "periodic"
        handled = self.cm.route_sender_key_rotation("alice", env)
        self.assertTrue(handled)
        self.assertTrue(self.h.has_status("alice", 89))

    def test_rotation_non_member_rejected(self):
        """Non-members can't trigger rotation."""
        self.cm.on_user_login("charlie")
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.sender_key_rotation.channel_id = "secret"
        env.sender_key_rotation.reason = "periodic"
        handled = self.cm.route_sender_key_rotation("charlie", env)
        self.assertTrue(handled)
        self.assertTrue(self.h.has_status("charlie", 87))

    def test_no_rotation_field_returns_false(self):
        """Message without sender_key_rotation returns False (not handled)."""
        env = WireCodec.make_envelope(route=PbEnvelope.BROADCAST)
        env.chat.text = "not a rotation"
        handled = self.cm.route_sender_key_rotation("alice", env)
        self.assertFalse(handled)


# ---------------------------------------------------------------------------
# Tests: Admin force key rotation
# ---------------------------------------------------------------------------

class TestForceRotateKeys(unittest.TestCase):

    def setUp(self):
        self.h = ChannelTestHelper()
        self.cm = make_manager(self.h)
        self.cm.on_user_login("alice")
        self.cm.on_user_login("bob")
        env = WireCodec.make_envelope(route=PbEnvelope.HUB)
        env.channel.action = PbChannel.CREATE
        env.channel.channel_id = "secret"
        env.channel.is_private = True
        self.cm.handle_channel_action("alice", env)
        ch = self.cm.get_channel("secret")
        ch.add_member("bob", ChannelRole.MEMBER)
        self.h.clear()

    def test_force_rotate_sends_rotation(self):
        result = self.cm.force_rotate_keys("secret", "admin")
        self.assertIn("rotation triggered", result)
        # Both members should receive the rotation
        all_envs = self.h.decode_all("alice") + self.h.decode_all("bob")
        rotations = [e for e in all_envs if e.HasField("sender_key_rotation")]
        self.assertTrue(len(rotations) >= 1)
        self.assertEqual(rotations[0].sender_key_rotation.reason, "manual")

    def test_force_rotate_unknown_channel(self):
        result = self.cm.force_rotate_keys("nonexist", "admin")
        self.assertIn("not found", result)

    def test_force_rotate_public_channel(self):
        result = self.cm.force_rotate_keys(GENERAL_CHANNEL_ID, "admin")
        self.assertIn("not a private channel", result)


if __name__ == "__main__":
    unittest.main()
