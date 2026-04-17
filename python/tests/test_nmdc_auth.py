"""
Tests for NMDC client authentication callbacks.

Tests the HubEventHandler methods OnValidateNick and OnCheckPassword
which are called from the C++ I/O thread during NMDC handshake.

Covers:
- Registered user requires password (OnValidateNick returns user_class > 0)
- Unregistered nick allowed as guest (returns 0) when allow_unregistered=1
- Unregistered nick rejected when allow_unregistered=0
- Disabled user rejected (authorised=False → returns -1)
- Wrong password rejected (OnCheckPassword returns -1)
- Correct password accepted (OnCheckPassword returns user_class)
- DB unavailable → reject (not allow as guest)
- No event loop → reject
- Empty/no password handling when require_password toggle is off
- bcrypt hash verification end-to-end
"""

import asyncio
import threading
from unittest.mock import MagicMock, patch

import bcrypt
import pytest
import pytest_asyncio

from verlihub.models import RegUser, UserClass
from verlihub.models.database import Database
from verlihub.api.auth import hash_password
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

# The core module requires the C++ SWIG bindings.
import verlihub.core as verlihub_core


# =============================================================================
# Helper: run a handler method from a worker thread so the event loop
# stays free to process the DB coroutine scheduled by _sync_db_lookup.
# =============================================================================


async def _call_from_thread(fn, *args):
    """Call fn(*args) in a worker thread; the running loop stays free."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, *args)


# =============================================================================
# Helper: build a HubEventHandler wired to a real async DB
# =============================================================================


def _make_handler_with_loop(loop: asyncio.AbstractEventLoop):
    """Create a HubEventHandler with the given event loop set."""
    handler = verlihub_core.HubEventHandler()
    handler.set_event_loop(loop)
    # Provide a fake hub context ref that returns config values
    handler._hub_context_ref = None
    return handler


def _make_handler_no_loop():
    """Create a HubEventHandler with NO event loop (simulates startup race)."""
    handler = verlihub_core.HubEventHandler()
    handler._event_loop = None
    handler._hub_context_ref = None
    return handler


# =============================================================================
# OnValidateNick — registered users
# =============================================================================


class TestOnValidateNickRegistered:
    """Test that registered users are recognised and require password."""

    @pytest.mark.asyncio
    async def test_registered_user_requires_password(self, db: Database, db_session: AsyncSession):
        """A registered user should return user_class > 0 (password required)."""
        user = RegUser(
            nick="alice",
            login_pwd=hash_password("secret"),
            user_class=UserClass.REGISTERED,
            authorised=True,
        )
        db_session.add(user)
        await db_session.commit()

        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)

        result = await _call_from_thread(handler.OnValidateNick, "alice", "127.0.0.1")
        assert result >= 1, "Registered user should require password (return >= 1)"

    @pytest.mark.asyncio
    async def test_admin_user_returns_admin_class(self, db: Database, db_session: AsyncSession):
        """Admin user should return class 5."""
        user = RegUser(
            nick="hubadmin",
            login_pwd=hash_password("adminpass"),
            user_class=UserClass.ADMIN,
            authorised=True,
        )
        db_session.add(user)
        await db_session.commit()

        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)

        result = await _call_from_thread(handler.OnValidateNick, "hubadmin", "10.0.0.1")
        assert result == UserClass.ADMIN

    @pytest.mark.asyncio
    async def test_disabled_user_rejected(self, db: Database, db_session: AsyncSession):
        """User with authorised=False should be rejected (-1)."""
        user = RegUser(
            nick="banned",
            login_pwd=hash_password("pass"),
            user_class=UserClass.REGISTERED,
            authorised=False,
        )
        db_session.add(user)
        await db_session.commit()

        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)

        result = await _call_from_thread(handler.OnValidateNick, "banned", "127.0.0.1")
        assert result == -1, "Disabled user should be rejected"


# =============================================================================
# OnValidateNick — unregistered users (guest access)
# =============================================================================


class TestOnValidateNickGuest:
    """Test guest access for unregistered nicks."""

    @pytest.mark.asyncio
    async def test_unregistered_nick_allowed_as_guest(self, db: Database, db_session: AsyncSession):
        """Unregistered nick should be allowed as guest (return 0) by default."""
        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)
        # Default: allow_unregistered=1
        handler._hub_context_ref = MagicMock()
        handler._hub_context_ref.get_config.return_value = "1"

        result = await _call_from_thread(handler.OnValidateNick, "newguest", "192.168.1.1")
        assert result == 0, "Unregistered nick should be allowed as guest"

    @pytest.mark.asyncio
    async def test_unregistered_nick_rejected_when_disabled(self, db: Database, db_session: AsyncSession):
        """Unregistered nick rejected when allow_unregistered=0."""
        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)
        handler._hub_context_ref = MagicMock()
        handler._hub_context_ref.get_config.return_value = "0"

        result = await _call_from_thread(handler.OnValidateNick, "newguest", "192.168.1.1")
        assert result == -1, "Unregistered nick should be rejected when disabled"


# =============================================================================
# OnValidateNick — DB unavailable (startup race window)
# =============================================================================


class TestOnValidateNickDbUnavailable:
    """Test that connections are rejected when DB is not ready."""

    @pytest.mark.asyncio
    async def test_no_event_loop_rejects(self, db: Database, db_session: AsyncSession):
        """Without an event loop, should reject (not allow as guest)."""
        handler = _make_handler_no_loop()

        # No event loop → _sync_db_lookup returns None immediately (no deadlock)
        result = await _call_from_thread(handler.OnValidateNick, "anyuser", "127.0.0.1")
        assert result == -1, "Should reject when no event loop (DB unavailable)"

    def test_db_not_initialized_rejects(self):
        """With no database initialized, should reject."""
        handler = verlihub_core.HubEventHandler()
        handler._event_loop = None
        handler._hub_context_ref = None

        result = handler.OnValidateNick("anyuser", "127.0.0.1")
        assert result == -1, "Should reject when DB not initialized"


# =============================================================================
# OnCheckPassword — correct / wrong password
# =============================================================================


class TestOnCheckPassword:
    """Test password verification in OnCheckPassword."""

    @pytest.mark.asyncio
    async def test_correct_password_accepted(self, db: Database, db_session: AsyncSession):
        """Correct bcrypt password should return user_class."""
        password = "my_secure_password"
        user = RegUser(
            nick="charlie",
            login_pwd=hash_password(password),
            user_class=UserClass.VIP,
            authorised=True,
        )
        db_session.add(user)
        await db_session.commit()

        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)

        result = await _call_from_thread(handler.OnCheckPassword, "charlie", password)
        assert result == UserClass.VIP, f"Expected VIP (2), got {result}"

    @pytest.mark.asyncio
    async def test_wrong_password_rejected(self, db: Database, db_session: AsyncSession):
        """Wrong password should return -1."""
        user = RegUser(
            nick="dave",
            login_pwd=hash_password("correct_password"),
            user_class=UserClass.REGISTERED,
            authorised=True,
        )
        db_session.add(user)
        await db_session.commit()

        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)

        result = await _call_from_thread(handler.OnCheckPassword, "dave", "WRONG_PASSWORD")
        assert result == -1, "Wrong password should return -1"

    @pytest.mark.asyncio
    async def test_nonexistent_user_rejected(self, db: Database, db_session: AsyncSession):
        """Password check for nonexistent user should return -1."""
        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)

        result = await _call_from_thread(handler.OnCheckPassword, "nobody", "anypassword")
        assert result == -1

    @pytest.mark.asyncio
    async def test_disabled_user_rejected(self, db: Database, db_session: AsyncSession):
        """Disabled user should fail password check even with correct password."""
        password = "mypass"
        user = RegUser(
            nick="disabled",
            login_pwd=hash_password(password),
            user_class=UserClass.REGISTERED,
            authorised=False,
        )
        db_session.add(user)
        await db_session.commit()

        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)

        result = await _call_from_thread(handler.OnCheckPassword, "disabled", password)
        assert result == -1, "Disabled user should fail password check"

    @pytest.mark.asyncio
    async def test_empty_password_hash_rejected(self, db: Database, db_session: AsyncSession):
        """User with empty password hash should be rejected (require_password=1 default)."""
        user = RegUser(
            nick="nopass",
            login_pwd="",
            user_class=UserClass.REGISTERED,
            authorised=True,
        )
        db_session.add(user)
        await db_session.commit()

        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)
        # Default require_password = "1"
        handler._hub_context_ref = MagicMock()
        handler._hub_context_ref.get_config.return_value = "1"

        result = await _call_from_thread(handler.OnCheckPassword, "nopass", "anything")
        assert result == -1, "Empty hash with require_password=1 should reject"

    @pytest.mark.asyncio
    async def test_empty_password_hash_allowed_when_not_required(self, db: Database, db_session: AsyncSession):
        """User with empty password hash allowed if require_password=0."""
        user = RegUser(
            nick="nopass2",
            login_pwd="",
            user_class=UserClass.OPERATOR,
            authorised=True,
        )
        db_session.add(user)
        await db_session.commit()

        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)
        handler._hub_context_ref = MagicMock()
        # Return "0" specifically for require_password
        handler._hub_context_ref.get_config.return_value = "0"

        result = await _call_from_thread(handler.OnCheckPassword, "nopass2", "")
        assert result == UserClass.OPERATOR


# =============================================================================
# Full NMDC auth flow (ValidateNick → CheckPassword)
# =============================================================================


class TestNmdcAuthFlow:
    """End-to-end NMDC authentication flow simulation."""

    @pytest.mark.asyncio
    async def test_registered_user_full_flow(self, db: Database, db_session: AsyncSession):
        """Simulate: $ValidateNick → $GetPass → $MyPass → accepted."""
        password = "hub_password_42"
        user = RegUser(
            nick="eve",
            login_pwd=hash_password(password),
            user_class=UserClass.OPERATOR,
            authorised=True,
        )
        db_session.add(user)
        await db_session.commit()

        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)

        # Step 1: ValidateNick should return class > 0 (needs password)
        validate_result = await _call_from_thread(handler.OnValidateNick, "eve", "10.0.0.1")
        assert validate_result >= 1, "Should require password"

        # Step 2: CheckPassword with correct password
        check_result = await _call_from_thread(handler.OnCheckPassword, "eve", password)
        assert check_result == UserClass.OPERATOR, "Should return user class on success"

    @pytest.mark.asyncio
    async def test_registered_user_wrong_password_flow(self, db: Database, db_session: AsyncSession):
        """Simulate: $ValidateNick → $GetPass → wrong $MyPass → rejected."""
        user = RegUser(
            nick="frank",
            login_pwd=hash_password("real_password"),
            user_class=UserClass.REGISTERED,
            authorised=True,
        )
        db_session.add(user)
        await db_session.commit()

        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)

        # Step 1: ValidateNick
        validate_result = await _call_from_thread(handler.OnValidateNick, "frank", "10.0.0.1")
        assert validate_result >= 1

        # Step 2: Wrong password
        check_result = await _call_from_thread(handler.OnCheckPassword, "frank", "WRONG")
        assert check_result == -1, "Wrong password should be rejected"

    @pytest.mark.asyncio
    async def test_guest_user_no_password_needed(self, db: Database, db_session: AsyncSession):
        """Simulate: unregistered nick → allowed as guest (no password phase)."""
        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)
        handler._hub_context_ref = MagicMock()
        handler._hub_context_ref.get_config.return_value = "1"  # allow_unregistered

        validate_result = await _call_from_thread(handler.OnValidateNick, "guest123", "192.168.1.50")
        assert validate_result == 0, "Guest should get class 0 (no password needed)"

    @pytest.mark.asyncio
    async def test_multiple_users_different_classes(self, db: Database, db_session: AsyncSession):
        """Multiple users with different classes should all work correctly."""
        users_data = [
            ("reg_user", "pass1", UserClass.REGISTERED),
            ("vip_user", "pass2", UserClass.VIP),
            ("op_user", "pass3", UserClass.OPERATOR),
            ("admin_user", "pass4", UserClass.ADMIN),
            ("master_user", "pass5", UserClass.MASTER),
        ]
        for nick, password, cls in users_data:
            db_session.add(RegUser(
                nick=nick,
                login_pwd=hash_password(password),
                user_class=cls,
                authorised=True,
            ))
        await db_session.commit()

        loop = asyncio.get_running_loop()
        handler = _make_handler_with_loop(loop)

        for nick, password, expected_class in users_data:
            # Validate should return the class (or at least >= 1)
            v = await _call_from_thread(handler.OnValidateNick, nick, "1.2.3.4")
            assert v >= 1, f"{nick} should require password"

            # Correct password
            c = await _call_from_thread(handler.OnCheckPassword, nick, password)
            assert c == expected_class, f"{nick} expected class {expected_class}, got {c}"

            # Wrong password
            w = await _call_from_thread(handler.OnCheckPassword, nick, "WRONG")
            assert w == -1, f"{nick} wrong password should return -1"


# =============================================================================
# bcrypt hash verification
# =============================================================================


class TestBcryptVerification:
    """Verify bcrypt hashing works correctly with the auth system."""

    def test_hash_password_produces_bcrypt(self):
        hashed = hash_password("test123")
        assert hashed.startswith("$2b$")

    def test_hash_password_different_each_time(self):
        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        # Different salts
        assert h1 != h2

    def test_bcrypt_checkpw_works(self):
        password = "secure_pass_42"
        hashed = hash_password(password)
        assert bcrypt.checkpw(
            password.encode("utf-8"),
            hashed.encode("utf-8"),
        )

    def test_bcrypt_wrong_password_fails(self):
        hashed = hash_password("correct")
        assert not bcrypt.checkpw(
            b"wrong",
            hashed.encode("utf-8"),
        )


# =============================================================================
# _db_available helper
# =============================================================================


class TestDbAvailable:
    """Test the _db_available() helper on HubEventHandler."""

    @pytest.mark.asyncio
    async def test_db_available_with_db_and_loop(self, db: Database):
        """Should return True when DB is initialized and loop is set."""
        handler = verlihub_core.HubEventHandler()
        handler.set_event_loop(asyncio.get_running_loop())

        assert handler._db_available() is True

    def test_db_unavailable_no_loop(self):
        """Should return False when no event loop is set."""
        handler = verlihub_core.HubEventHandler()
        handler._event_loop = None
        # Even if DB is initialized, no loop = not available
        assert handler._db_available() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
