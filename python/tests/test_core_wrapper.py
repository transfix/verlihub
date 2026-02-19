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

    def KickUser(self, op, nick, reason):
        return True

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
