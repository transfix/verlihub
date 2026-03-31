"""
Tests for verlihub.core module (HubEventHandler + HubContext wrapper).

Since core.py requires the SWIG-built ``verlihub_core`` C extension which
isn't available in pure-Python test environments, we inject a *fake* module
into ``sys.modules`` before importing ``verlihub.core``.
"""
import asyncio
import importlib
import signal
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ===================================================================
# Fake SWIG module
# ===================================================================

class FakeIHubEventCallback:
    """Stand-in for verlihub_core.IHubEventCallback."""
    pass


class FakeCppHubContext:
    """Stand-in for the C++ HubContext pointer returned by Create()."""

    def __init__(self):
        self._running = False
        self._event_cb = None
        self._user_count = 0
        self._total_share = 0
        self._hub_name = "FakeHub"
        self._hub_topic = "Test Topic"
        self._config = {}

    # — Methods that the Python wrapper calls —

    def SetEventCallback(self, cb):
        self._event_cb = cb

    def IsRunning(self):
        return self._running

    def GetUserCount(self):
        return self._user_count

    def GetTotalShare(self):
        return self._total_share

    def GetHubName(self):
        return self._hub_name

    def GetHubTopic(self):
        return self._hub_topic

    def SetHubTopic(self, v):
        self._hub_topic = v

    def Initialize(self):
        return True

    def Start(self, port=0, listen_ip=""):
        self._running = True
        return True

    def Stop(self):
        self._running = False

    def RequestShutdown(self, code=0):
        self._running = False

    def RequestReload(self):
        pass

    def GetUserNicks(self):
        return ["Alice", "Bob"]

    def FindUser(self, nick):
        return nick if nick in ("Alice", "Bob") else None

    def SendToUser(self, nick, msg):
        return True

    def SendToAll(self, msg):
        return True

    def SendToClass(self, msg, lo, hi):
        return True

    def SendToOpChat(self, msg, from_nick=""):
        return True

    def KickUser(self, op, nick, reason):
        return True

    def ForceMove(self, nick, address):
        return nick in ("Alice", "Bob")

    def DisconnectUser(self, nick):
        return nick in ("Alice", "Bob")

    def GetProtocolStats(self):
        """Return a fake stats snapshot object."""
        class _Snap:
            messages_in = 100
            messages_out = 200
            chat_count = 50
            pm_count = 30
            search_count = 20
            myinfo_count = 10
            ctm_count = 5
            sr_count = 3
            mcto_count = 2
            flood_blocked = 1
            ban_blocked = 0
        return _Snap()

    def LookupGeoIP(self, ip):
        class _Info:
            country_code = "US"
            country_name = "United States"
            city = "New York"
            available = True
        return _Info()

    def SetFloodConfig(self, flood_type, period_ms, max_tokens):
        self._flood_config = self._flood_config if hasattr(self, '_flood_config') else {}
        self._flood_config[flood_type] = (period_ms, max_tokens)

    def GetFloodConfig(self, flood_type):
        if hasattr(self, '_flood_config') and flood_type in self._flood_config:
            return self._flood_config[flood_type]
        return (5000, 3)

    def GetConfig(self, section, key, default=""):
        return self._config.get(f"{section}.{key}", default)

    def SetConfig(self, section, key, value):
        self._config[f"{section}.{key}"] = value
        return True

    @classmethod
    def Create(cls, config_path):
        return cls()


def _build_fake_module():
    """Build a fake ``verlihub_core`` module object."""
    import types
    mod = types.ModuleType("verlihub.verlihub_core")
    mod.IHubEventCallback = FakeIHubEventCallback
    mod.HubContext = FakeCppHubContext
    return mod


# ===================================================================
# Fixture: import verlihub.core with the fake SWIG backend
# ===================================================================

@pytest.fixture(scope="module")
def core_module():
    """
    Import ``verlihub.core`` with a fake SWIG backend injected into
    ``sys.modules`` so the real C extension is not required.
    """
    fake = _build_fake_module()

    # Stash originals
    saved = {}
    for key in ("verlihub.verlihub_core", "verlihub.core"):
        saved[key] = sys.modules.pop(key, None)

    # Inject fake SWIG module
    sys.modules["verlihub.verlihub_core"] = fake

    # Also patch verlihub package attribute so ``from verlihub import verlihub_core`` works
    import verlihub
    old_attr = getattr(verlihub, "verlihub_core", None)
    verlihub.verlihub_core = fake

    try:
        mod = importlib.import_module("verlihub.core")
        yield mod
    finally:
        # Restore
        for key, val in saved.items():
            if val is not None:
                sys.modules[key] = val
            else:
                sys.modules.pop(key, None)
        if old_attr is not None:
            verlihub.verlihub_core = old_attr
        else:
            verlihub.verlihub_core = None


# ===================================================================
# HubEventHandler
# ===================================================================

class TestHubEventHandler:

    def test_register_and_dispatch(self, core_module):
        handler = core_module.HubEventHandler()
        results = []
        handler.register("user_connect", lambda nick, ip: results.append((nick, ip)))

        ret = handler.OnUserConnect("Alice", "10.0.0.1")
        assert ret is True
        assert results == [("Alice", "10.0.0.1")]

    def test_dispatch_returns_false_blocks(self, core_module):
        """If a handler returns False, the dispatch result is False."""
        handler = core_module.HubEventHandler()
        handler.register("chat_message", lambda nick, msg: False)

        ret = handler.OnChatMessage("Alice", "hello")
        assert ret is False

    def test_handler_exception_is_logged(self, core_module):
        handler = core_module.HubEventHandler()
        handler.register("user_connect", lambda nick, ip: (_ for _ in ()).throw(ValueError("boom")))

        # Should not raise — exception is caught and logged
        ret = handler.OnUserConnect("Alice", "10.0.0.1")
        assert isinstance(ret, bool)

    def test_unregister(self, core_module):
        handler = core_module.HubEventHandler()
        cb = lambda nick, ip: True
        handler.register("user_connect", cb)
        handler.unregister("user_connect", cb)

        # After unregister, dispatch returns True with no handlers
        assert handler.OnUserConnect("Alice", "1.2.3.4") is True

    def test_unregister_missing_handler(self, core_module):
        handler = core_module.HubEventHandler()
        handler.unregister("user_connect", lambda: None)  # Not registered — no error

    def test_register_unknown_event_raises(self, core_module):
        handler = core_module.HubEventHandler()
        with pytest.raises(ValueError, match="Unknown event type"):
            handler.register("nonexistent_event", lambda: None)

    def test_on_user_disconnect(self, core_module):
        handler = core_module.HubEventHandler()
        called = []
        handler.register("user_disconnect", lambda nick: called.append(nick))
        handler.OnUserDisconnect("Bob")
        assert called == ["Bob"]

    def test_on_user_login(self, core_module):
        handler = core_module.HubEventHandler()
        called = []
        handler.register("user_login", lambda nick, cls: called.append((nick, cls)))
        ret = handler.OnUserLogin("Alice", 3)
        assert ret is True
        assert called == [("Alice", 3)]

    def test_on_user_logout(self, core_module):
        handler = core_module.HubEventHandler()
        called = []
        handler.register("user_logout", lambda nick: called.append(nick))
        handler.OnUserLogout("Alice")
        assert called == ["Alice"]

    def test_on_private_message(self, core_module):
        handler = core_module.HubEventHandler()
        called = []
        handler.register("private_message", lambda f, t, m: called.append((f, t, m)))
        ret = handler.OnPrivateMessage("Alice", "Bob", "hi")
        assert ret is True
        assert called == [("Alice", "Bob", "hi")]

    def test_on_search(self, core_module):
        handler = core_module.HubEventHandler()
        called = []
        handler.register("search", lambda nick, q: called.append((nick, q)))
        ret = handler.OnSearch("Alice", "mp3")
        assert ret is True

    def test_on_timer(self, core_module):
        handler = core_module.HubEventHandler()
        called = []
        handler.register("timer", lambda ts: called.append(ts))
        handler.OnTimer(12345)
        assert called == [12345]

    def test_on_hub_started(self, core_module):
        handler = core_module.HubEventHandler()
        called = []
        handler.register("hub_started", lambda: called.append(True))
        handler.OnHubStarted()
        assert called == [True]

    def test_on_hub_stopping(self, core_module):
        handler = core_module.HubEventHandler()
        called = []
        handler.register("hub_stopping", lambda: called.append(True))
        handler.OnHubStopping()
        assert called == [True]


# ===================================================================
# HubContext wrapper
# ===================================================================

class TestHubContext:

    def test_create(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        assert ctx is not None

    def test_create_failure(self, core_module):
        original = FakeCppHubContext.Create

        @classmethod
        def fail_create(cls, path):
            return None

        FakeCppHubContext.Create = fail_create
        try:
            ctx = core_module.HubContext.create("/tmp/test-vhcore")
            assert ctx is None
        finally:
            FakeCppHubContext.Create = original

    def test_properties(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        assert ctx.is_running is False
        assert ctx.user_count == 0
        assert ctx.total_share == 0
        assert ctx.hub_name == "FakeHub"
        assert ctx.hub_topic == "Test Topic"

    def test_set_hub_topic(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        ctx.hub_topic = "New Topic"
        assert ctx.hub_topic == "New Topic"

    def test_initialize_and_start(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        assert ctx.initialize() is True
        assert ctx.start(port=411, listen_ip="0.0.0.0") is True
        assert ctx.is_running is True

    def test_stop(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        ctx.start()
        assert ctx.is_running is True
        ctx.stop()
        assert ctx.is_running is False

    def test_request_shutdown(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        ctx.start()
        ctx.request_shutdown(15)
        assert ctx.is_running is False

    def test_user_operations(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        assert ctx.get_user_nicks() == ["Alice", "Bob"]
        assert ctx.find_user("Alice") is True
        assert ctx.find_user("Ghost") is False
        assert ctx.send_to_user("Alice", "hello") is True
        assert ctx.send_to_all("broadcast") is True
        assert ctx.send_to_class("msg", 1, 5) is True
        assert ctx.send_chat_as("Admin", "Hello from admin") is True
        assert ctx.kick_user("op", "Alice", "reason") is True

    def test_config(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        assert ctx.set_config("hub", "name", "MyHub") is True
        assert ctx.get_config("hub", "name") == "MyHub"
        assert ctx.get_config("hub", "missing", "default") == "default"

    def test_events_property(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        events = ctx.events
        assert events is not None
        assert hasattr(events, "register")

    def test_cpp_property(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        assert ctx.cpp is not None

    def test_context_manager(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        ctx.start()
        with ctx:
            assert ctx.is_running is True
        assert ctx.is_running is False

    def test_context_manager_not_running(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        with ctx:
            pass  # __exit__ should be fine even if not running

    async def test_wait_for_shutdown(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")

        async def _stopper():
            await asyncio.sleep(0.05)
            ctx.stop()

        asyncio.get_event_loop().create_task(_stopper())
        await ctx.wait_for_shutdown()


# ===================================================================
# create_hub async context manager
# ===================================================================

class TestCreateHub:

    async def test_create_hub(self, core_module):
        async with core_module.create_hub("/tmp/test-vhcore") as ctx:
            assert ctx is not None
            ctx.start()
            assert ctx.is_running is True
        # After exit, hub should be stopped
        assert ctx.is_running is False

    async def test_create_hub_failure(self, core_module):
        original = FakeCppHubContext.Create

        @classmethod
        def fail_create(cls, path):
            return None

        FakeCppHubContext.Create = fail_create
        try:
            with pytest.raises(RuntimeError, match="Failed to create"):
                async with core_module.create_hub("/tmp/fail"):
                    pass
        finally:
            FakeCppHubContext.Create = original


# ===================================================================
# setup_signal_handlers
# ===================================================================

class TestSignalHandlers:

    def test_setup_signal_handlers(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        # Should install SIGTERM, SIGINT, SIGHUP handlers
        core_module.setup_signal_handlers(ctx)

        # Verify the handlers were set
        assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL
        assert signal.getsignal(signal.SIGINT) is not signal.SIG_DFL
        assert signal.getsignal(signal.SIGHUP) is not signal.SIG_DFL

        # Restore default handlers
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)


# ===================================================================
# Phase 3: New Event Handlers
# ===================================================================

class TestPhase3EventHandlers:

    def test_on_ext_json(self, core_module):
        handler = core_module.HubEventHandler()
        called = []
        handler.register("ext_json", lambda nick, json: called.append((nick, json)))
        ret = handler.OnExtJSON("Alice", '{"type":"info"}')
        assert ret is True
        assert called == [("Alice", '{"type":"info"}')]

    def test_on_ext_json_blocks(self, core_module):
        handler = core_module.HubEventHandler()
        handler.register("ext_json", lambda nick, json: False)
        assert handler.OnExtJSON("Alice", "{}") is False

    def test_on_my_hub_url(self, core_module):
        handler = core_module.HubEventHandler()
        called = []
        handler.register("my_hub_url", lambda nick, url: called.append((nick, url)))
        ret = handler.OnMyHubURL("Bob", "dchub://example.com:411")
        assert ret is True
        assert called == [("Bob", "dchub://example.com:411")]

    def test_on_my_hub_url_blocks(self, core_module):
        handler = core_module.HubEventHandler()
        handler.register("my_hub_url", lambda nick, url: False)
        assert handler.OnMyHubURL("Bob", "dchub://x") is False

    def test_on_user_in_update(self, core_module):
        handler = core_module.HubEventHandler()
        called = []
        handler.register("user_in_update", lambda nick, data: called.append((nick, data)))
        ret = handler.OnUserINUpdate("Alice", "$Speed:100$")
        assert ret is True
        assert called == [("Alice", "$Speed:100$")]

    def test_on_user_in_update_blocks(self, core_module):
        handler = core_module.HubEventHandler()
        handler.register("user_in_update", lambda nick, data: False)
        assert handler.OnUserINUpdate("Alice", "data") is False

    def test_register_new_event_types(self, core_module):
        """Verify the new event types are in the handler's registry."""
        handler = core_module.HubEventHandler()
        for event in ("ext_json", "my_hub_url", "user_in_update"):
            handler.register(event, lambda *a: True)  # Should not raise


# ===================================================================
# Phase 4: HubContext Wrapper Methods
# ===================================================================

class TestPhase4WrapperMethods:

    def test_force_move_found(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        assert ctx.force_move("Alice", "dchub://other:411") is True

    def test_force_move_not_found(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        assert ctx.force_move("Ghost", "dchub://other:411") is False

    def test_disconnect_user_found(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        assert ctx.disconnect_user("Bob") is True

    def test_disconnect_user_not_found(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        assert ctx.disconnect_user("Ghost") is False

    def test_send_to_opchat(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        assert ctx.send_to_opchat("test message") is True
        assert ctx.send_to_opchat("test", from_nick="Admin") is True

    def test_get_protocol_stats(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        stats = ctx.get_protocol_stats()
        assert isinstance(stats, dict)
        assert stats["messages_in"] == 100
        assert stats["messages_out"] == 200
        assert stats["chat_count"] == 50
        assert stats["flood_blocked"] == 1
        assert "mcto_count" in stats

    def test_lookup_geoip(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        info = ctx.lookup_geoip("8.8.8.8")
        assert isinstance(info, dict)
        assert info["country_code"] == "US"
        assert info["available"] is True

    def test_set_and_get_flood_config(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        ctx.set_flood_config(0, 3000, 5)
        result = ctx.get_flood_config(0)
        assert result == (3000, 5)

    def test_get_flood_config_default(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        result = ctx.get_flood_config(99)
        assert result == (5000, 3)

    def test_send_pm_as(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        assert ctx.send_pm_as("Bot", "Alice", "hello") is True

    def test_send_chat_as(self, core_module):
        ctx = core_module.HubContext.create("/tmp/test-vhcore")
        assert ctx.send_chat_as("Admin", "Hello everyone") is True
