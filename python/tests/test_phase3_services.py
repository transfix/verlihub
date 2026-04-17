"""
Tests for Phase 3 Python services:
- TriggerCache (trigger_service)
- RedirectCache (redirect_service)
- ClientDetectionCache (client_detection_service)
- ChatRoomManager (chat_room_service)

These are pure-Python unit tests that don't need the SWIG C extension.
"""
import pytest

from verlihub.trigger_service import TriggerCache
from verlihub.redirect_service import RedirectCache
from verlihub.client_detection_service import ClientDetectionCache
from verlihub.chat_room_service import ChatRoom, ChatRoomManager


# =====================================================================
# TriggerCache tests
# =====================================================================

class TestTriggerCache:
    def setup_method(self):
        self.cache = TriggerCache()

    def test_empty_cache_no_match(self):
        assert self.cache.match("!help", 0) is None

    def test_load_and_match(self):
        triggers = [
            {"command": "!help", "response": "Help text", "min_class": 0, "max_class": 10},
            {"command": "!rules", "response": "Rules text", "min_class": 1, "max_class": 5},
        ]
        self.cache.load(triggers)
        assert self.cache.count == 2

        result = self.cache.match("!help", 0)
        assert result is not None
        assert result["response"] == "Help text"

    def test_match_case_insensitive(self):
        self.cache.load([
            {"command": "!Help", "response": "OK", "min_class": 0, "max_class": 10},
        ])
        assert self.cache.match("!help extra args", 0) is not None

    def test_class_restriction_too_low(self):
        self.cache.load([
            {"command": "!admin", "response": "Admin only", "min_class": 5, "max_class": 10},
        ])
        assert self.cache.match("!admin", 3) is None
        assert self.cache.match("!admin", 5) is not None

    def test_class_restriction_too_high(self):
        self.cache.load([
            {"command": "!guest", "response": "Guest only", "min_class": 0, "max_class": 2},
        ])
        assert self.cache.match("!guest", 3) is None
        assert self.cache.match("!guest", 2) is not None

    def test_add_single(self):
        self.cache.add({"command": "!test", "response": "t", "min_class": 0, "max_class": 10})
        assert self.cache.count == 1
        assert self.cache.match("!test", 0) is not None

    def test_remove(self):
        self.cache.add({"command": "!rm", "response": "x", "min_class": 0, "max_class": 10})
        assert self.cache.count == 1
        self.cache.remove("!rm")
        assert self.cache.count == 0

    def test_clear(self):
        self.cache.load([
            {"command": "!a", "response": "a", "min_class": 0, "max_class": 10},
            {"command": "!b", "response": "b", "min_class": 0, "max_class": 10},
        ])
        assert self.cache.count == 2
        self.cache.clear()
        assert self.cache.count == 0

    def test_empty_command_ignored(self):
        self.cache.add({"command": "", "response": "nope", "min_class": 0, "max_class": 10})
        assert self.cache.count == 0

    def test_empty_text_no_match(self):
        self.cache.add({"command": "!x", "response": "y", "min_class": 0, "max_class": 10})
        assert self.cache.match("", 0) is None

    def test_whitespace_command_trimmed(self):
        self.cache.add({"command": "  !spaced  ", "response": "ok", "min_class": 0, "max_class": 10})
        assert self.cache.match("!spaced", 0) is not None


# =====================================================================
# RedirectCache tests
# =====================================================================

class TestRedirectCache:
    def setup_method(self):
        self.cache = RedirectCache()

    def test_empty_no_match(self):
        assert self.cache.match(1) is None

    def test_load_and_match(self):
        rules = [
            {"address": "hub1.example.com", "flag": 1, "enable": True},
            {"address": "hub2.example.com", "flag": 2, "enable": True},
        ]
        self.cache.load(rules)
        assert self.cache.count == 2
        assert self.cache.match(1) == "hub1.example.com"
        assert self.cache.match(2) == "hub2.example.com"

    def test_flag_bitmask(self):
        self.cache.load([
            {"address": "backup.example.com", "flag": 0b110, "enable": True},
        ])
        # flag 2 (0b010) should match
        assert self.cache.match(2) == "backup.example.com"
        # flag 4 (0b100) should also match
        assert self.cache.match(4) == "backup.example.com"
        # flag 1 (0b001) should NOT match
        assert self.cache.match(1) is None

    def test_disabled_not_matched(self):
        self.cache.load([
            {"address": "disabled.example.com", "flag": 1, "enable": False},
        ])
        assert self.cache.match(1) is None

    def test_first_match_wins(self):
        self.cache.load([
            {"address": "first.example.com", "flag": 1, "enable": True},
            {"address": "second.example.com", "flag": 1, "enable": True},
        ])
        assert self.cache.match(1) == "first.example.com"

    def test_clear(self):
        self.cache.load([{"address": "x", "flag": 1, "enable": True}])
        self.cache.clear()
        assert self.cache.count == 0


# =====================================================================
# ClientDetectionCache tests
# =====================================================================

class TestClientDetectionCache:
    def setup_method(self):
        self.cache = ClientDetectionCache()

    def test_empty_no_ban(self):
        assert self.cache.check("DC++", 1.0) is None

    def test_tag_id_match(self):
        self.cache.load([{
            "name": "", "tag_id": "++", "min_version": 0, "max_version": 0,
            "ban": True, "enable": True,
        }])
        result = self.cache.check("DC++", 1.0)
        assert result is not None
        assert result["ban"] is True

    def test_name_exact_match(self):
        self.cache.load([{
            "name": "StrgDC++", "tag_id": "", "min_version": 0, "max_version": 0,
            "ban": True, "enable": True,
        }])
        assert self.cache.check("StrgDC++", 1.0) is not None
        # Different name shouldn't match
        assert self.cache.check("DC++", 1.0) is None

    def test_name_match_case_insensitive(self):
        self.cache.load([{
            "name": "badclient", "tag_id": "", "min_version": 0, "max_version": 0,
            "ban": True, "enable": True,
        }])
        assert self.cache.check("BadClient", 1.0) is not None

    def test_version_range_min(self):
        self.cache.load([{
            "name": "OldDC", "tag_id": "", "min_version": 2.0, "max_version": 0,
            "ban": True, "enable": True,
        }])
        assert self.cache.check("OldDC", 1.9) is None
        assert self.cache.check("OldDC", 2.0) is not None

    def test_version_range_max(self):
        self.cache.load([{
            "name": "NewDC", "tag_id": "", "min_version": 0, "max_version": 3.0,
            "ban": True, "enable": True,
        }])
        assert self.cache.check("NewDC", 3.1) is None
        assert self.cache.check("NewDC", 3.0) is not None

    def test_disabled_rule_skipped(self):
        self.cache.load([{
            "name": "BadClient", "tag_id": "", "min_version": 0, "max_version": 0,
            "ban": True, "enable": False,
        }])
        assert self.cache.check("BadClient", 1.0) is None

    def test_non_ban_rule_returns_none(self):
        self.cache.load([{
            "name": "AllowedClient", "tag_id": "", "min_version": 0, "max_version": 0,
            "ban": False, "enable": True,
        }])
        # Rule matches but ban=False, so not returned
        assert self.cache.check("AllowedClient", 1.0) is None

    def test_clear(self):
        self.cache.load([{
            "name": "X", "tag_id": "", "min_version": 0, "max_version": 0,
            "ban": True, "enable": True,
        }])
        assert self.cache.count == 1
        self.cache.clear()
        assert self.cache.count == 0


# =====================================================================
# ChatRoom & ChatRoomManager tests
# =====================================================================

class TestChatRoom:
    def test_create(self):
        room = ChatRoom("TestRoom", creator="admin", topic="Hello")
        assert room.name == "TestRoom"
        assert room.creator == "admin"
        assert room.topic == "Hello"
        assert room.min_class == 0
        assert room.member_count == 0

    def test_add_member(self):
        room = ChatRoom("R")
        assert room.add_member("Alice") is True
        assert room.add_member("Alice") is False  # duplicate
        assert room.member_count == 1

    def test_remove_member(self):
        room = ChatRoom("R")
        room.add_member("Alice")
        assert room.remove_member("Alice") is True
        assert room.remove_member("Alice") is False
        assert room.member_count == 0

    def test_is_member(self):
        room = ChatRoom("R")
        room.add_member("Bob")
        assert room.is_member("Bob") is True
        assert room.is_member("Eve") is False

    def test_to_dict(self):
        room = ChatRoom("R", creator="op", topic="T", min_class=3)
        room.add_member("B")
        room.add_member("A")
        d = room.to_dict()
        assert d["name"] == "R"
        assert d["members"] == ["A", "B"]  # sorted
        assert d["member_count"] == 2


class TestChatRoomManager:
    def setup_method(self):
        self.mgr = ChatRoomManager()

    def test_create_room(self):
        room = self.mgr.create_room("lobby", creator="admin")
        assert room is not None
        assert room.name == "lobby"
        assert self.mgr.room_count == 1

    def test_create_duplicate_returns_none(self):
        self.mgr.create_room("lobby")
        assert self.mgr.create_room("lobby") is None
        assert self.mgr.room_count == 1

    def test_case_insensitive_names(self):
        self.mgr.create_room("Lobby")
        assert self.mgr.create_room("lobby") is None
        assert self.mgr.get_room("LOBBY") is not None

    def test_delete_room(self):
        self.mgr.create_room("temp")
        assert self.mgr.delete_room("temp") is True
        assert self.mgr.delete_room("temp") is False
        assert self.mgr.room_count == 0

    def test_list_rooms(self):
        self.mgr.create_room("a")
        self.mgr.create_room("b")
        rooms = self.mgr.list_rooms()
        assert len(rooms) == 2
        names = {r["name"] for r in rooms}
        assert "a" in names
        assert "b" in names

    def test_join_room(self):
        self.mgr.create_room("chat")
        err = self.mgr.join_room("chat", "Alice")
        assert err is None

    def test_join_nonexistent_room(self):
        err = self.mgr.join_room("nope", "Alice")
        assert err is not None
        assert "does not exist" in err

    def test_join_duplicate(self):
        self.mgr.create_room("chat")
        self.mgr.join_room("chat", "Alice")
        err = self.mgr.join_room("chat", "Alice")
        assert err is not None
        assert "Already" in err

    def test_join_class_restriction(self):
        self.mgr.create_room("vip", min_class=3)
        err = self.mgr.join_room("vip", "guest", user_class=1)
        assert err is not None
        assert "Insufficient" in err
        err = self.mgr.join_room("vip", "op", user_class=3)
        assert err is None

    def test_leave_room(self):
        self.mgr.create_room("chat")
        self.mgr.join_room("chat", "Alice")
        err = self.mgr.leave_room("chat", "Alice")
        assert err is None

    def test_leave_not_member(self):
        self.mgr.create_room("chat")
        err = self.mgr.leave_room("chat", "Bob")
        assert err is not None
        assert "Not a member" in err

    def test_leave_nonexistent(self):
        err = self.mgr.leave_room("fake", "Bob")
        assert err is not None
        assert "does not exist" in err

    def test_on_user_disconnect(self):
        self.mgr.create_room("a")
        self.mgr.create_room("b")
        self.mgr.join_room("a", "Alice")
        self.mgr.join_room("b", "Alice")
        self.mgr.on_user_disconnect("Alice")
        assert self.mgr.get_room("a").member_count == 0
        assert self.mgr.get_room("b").member_count == 0

    def test_get_room_members(self):
        self.mgr.create_room("r")
        self.mgr.join_room("r", "Bob")
        self.mgr.join_room("r", "Alice")
        members = self.mgr.get_room_members("r")
        assert members == ["Alice", "Bob"]  # sorted

    def test_get_room_members_nonexistent(self):
        assert self.mgr.get_room_members("nope") is None

    def test_route_message(self):
        self.mgr.create_room("r")
        self.mgr.join_room("r", "Alice")
        self.mgr.join_room("r", "Bob")
        self.mgr.join_room("r", "Charlie")
        recipients = self.mgr.route_message("r", "Alice", "Hello")
        assert "Alice" not in recipients
        assert sorted(recipients) == ["Bob", "Charlie"]

    def test_route_message_not_member(self):
        self.mgr.create_room("r")
        self.mgr.join_room("r", "Alice")
        assert self.mgr.route_message("r", "Eve", "Hello") is None

    def test_route_message_nonexistent(self):
        assert self.mgr.route_message("fake", "Alice", "Hello") is None
