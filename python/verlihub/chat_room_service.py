"""
Chat room service — virtual chat rooms via NMDC PM routing.

Implements Python-side chat rooms that work by routing private messages
between room members. The built-in OpChat is handled via the C++
SendToOpChat() method. User-created rooms use PM routing.

Each room has:
- A unique name (acts as a virtual bot nick)
- A set of member nicks
- An optional topic
- A minimum class to join
"""
from __future__ import annotations

import logging
from typing import Optional

_log = logging.getLogger(__name__)


class ChatRoom:
    """A single virtual chat room."""

    __slots__ = ("name", "topic", "min_class", "members", "creator")

    def __init__(
        self,
        name: str,
        *,
        creator: str = "",
        topic: str = "",
        min_class: int = 0,
    ) -> None:
        self.name = name
        self.creator = creator
        self.topic = topic
        self.min_class = min_class
        self.members: set[str] = set()

    def add_member(self, nick: str) -> bool:
        if nick in self.members:
            return False
        self.members.add(nick)
        return True

    def remove_member(self, nick: str) -> bool:
        if nick not in self.members:
            return False
        self.members.discard(nick)
        return True

    def is_member(self, nick: str) -> bool:
        return nick in self.members

    @property
    def member_count(self) -> int:
        return len(self.members)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "creator": self.creator,
            "topic": self.topic,
            "min_class": self.min_class,
            "members": sorted(self.members),
            "member_count": self.member_count,
        }


class ChatRoomManager:
    """Manages all virtual chat rooms."""

    def __init__(self) -> None:
        self._rooms: dict[str, ChatRoom] = {}

    def create_room(
        self,
        name: str,
        *,
        creator: str = "",
        topic: str = "",
        min_class: int = 0,
    ) -> Optional[ChatRoom]:
        """Create a new chat room. Returns None if name already taken."""
        key = name.lower()
        if key in self._rooms:
            return None
        room = ChatRoom(name, creator=creator, topic=topic, min_class=min_class)
        self._rooms[key] = room
        _log.info("Created chat room '%s' by %s", name, creator or "system")
        return room

    def delete_room(self, name: str) -> bool:
        """Delete a chat room. Returns True if it existed."""
        key = name.lower()
        if key not in self._rooms:
            return False
        del self._rooms[key]
        _log.info("Deleted chat room '%s'", name)
        return True

    def get_room(self, name: str) -> Optional[ChatRoom]:
        return self._rooms.get(name.lower())

    def list_rooms(self) -> list[dict]:
        return [room.to_dict() for room in self._rooms.values()]

    def join_room(self, name: str, nick: str, user_class: int = 0) -> Optional[str]:
        """Join a room. Returns error message or None on success."""
        room = self.get_room(name)
        if room is None:
            return f"Room '{name}' does not exist"
        if user_class < room.min_class:
            return f"Insufficient class to join '{name}'"
        if not room.add_member(nick):
            return f"Already a member of '{name}'"
        return None

    def leave_room(self, name: str, nick: str) -> Optional[str]:
        """Leave a room. Returns error message or None on success."""
        room = self.get_room(name)
        if room is None:
            return f"Room '{name}' does not exist"
        if not room.remove_member(nick):
            return f"Not a member of '{name}'"
        return None

    def on_user_disconnect(self, nick: str) -> None:
        """Remove a user from all rooms (called on disconnect)."""
        for room in self._rooms.values():
            room.members.discard(nick)

    def get_room_members(self, name: str) -> Optional[list[str]]:
        room = self.get_room(name)
        if room is None:
            return None
        return sorted(room.members)

    def route_message(self, room_name: str, from_nick: str, message: str) -> Optional[list[str]]:
        """Get list of nicks to receive a room message (excluding sender).

        Returns None if room doesn't exist or sender isn't a member.
        """
        room = self.get_room(room_name)
        if room is None:
            return None
        if not room.is_member(from_nick):
            return None
        return [nick for nick in room.members if nick != from_nick]

    @property
    def room_count(self) -> int:
        return len(self._rooms)


# Module-level singleton
_manager = ChatRoomManager()


def get_chat_room_manager() -> ChatRoomManager:
    return _manager
