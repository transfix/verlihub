#!/usr/bin/env python3
"""
Standalone tests for the Verlihub Hook Dispatcher

Tests the dispatcher module functionality for single-interpreter mode:
- Script registration and unregistration
- Hook dispatching to multiple scripts  
- Priority ordering
- Enable/disable functionality
- Statistics tracking
- Thread safety

These tests run without needing a full Verlihub instance, making them
suitable for CI/CD pipelines and quick development iteration.

Usage:
    python test_dispatcher.py
    python -m pytest test_dispatcher.py -v
"""

import sys
import os
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Add scripts directory to path - try multiple locations
# Docker: /scripts
# Local: relative to this file
scripts_paths = [
    '/scripts',  # Docker mount
    str(Path(__file__).parent.parent.parent / "plugins" / "python" / "scripts"),  # Local relative
    os.environ.get('SCRIPTS_PATH', ''),  # Environment override
]
for path in scripts_paths:
    if path and os.path.exists(path) and path not in sys.path:
        sys.path.insert(0, path)

# Mock vh module before importing dispatcher
sys.modules['vh'] = MagicMock()


class TestDispatcherRegistration(unittest.TestCase):
    """Test script registration and unregistration"""
    
    def setUp(self):
        """Reset dispatcher state before each test"""
        # Import here to get fresh module state
        import dispatcher
        # Reset internal state
        dispatcher._scripts.clear()
        dispatcher._hooks.clear()
        dispatcher._next_script_id = 1
        dispatcher._stats["total_scripts"] = 0
        dispatcher._stats["active_scripts"] = 0
        dispatcher._stats["total_calls"].clear()
        dispatcher._stats["failed_calls"].clear()
        dispatcher._stats["disabled_scripts"].clear()
        self.dispatcher = dispatcher
    
    def test_register_script_basic(self):
        """Test basic script registration"""
        def my_handler(nick, message):
            return 1
        
        script_id = self.dispatcher.register_script(
            script_name="TestScript",
            hooks={"OnParsedMsgChat": my_handler}
        )
        
        self.assertEqual(script_id, 1)
        self.assertEqual(self.dispatcher._stats["total_scripts"], 1)
        self.assertEqual(self.dispatcher._stats["active_scripts"], 1)
    
    def test_register_multiple_scripts(self):
        """Test registering multiple scripts"""
        def handler1(): return 1
        def handler2(): return 1
        def handler3(): return 1
        
        id1 = self.dispatcher.register_script("Script1", {"OnTimer": handler1})
        id2 = self.dispatcher.register_script("Script2", {"OnTimer": handler2})
        id3 = self.dispatcher.register_script("Script3", {"OnTimer": handler3})
        
        self.assertEqual(id1, 1)
        self.assertEqual(id2, 2)
        self.assertEqual(id3, 3)
        self.assertEqual(self.dispatcher._stats["total_scripts"], 3)
    
    def test_register_with_multiple_hooks(self):
        """Test registering script with multiple hooks"""
        def on_timer(msec): return 1
        def on_chat(nick, msg): return 1
        def on_login(nick): return 1
        
        script_id = self.dispatcher.register_script(
            script_name="MultiHookScript",
            hooks={
                "OnTimer": on_timer,
                "OnParsedMsgChat": on_chat,
                "OnUserLogin": on_login
            }
        )
        
        self.assertEqual(script_id, 1)
        self.assertIn("OnTimer", self.dispatcher._hooks)
        self.assertIn("OnParsedMsgChat", self.dispatcher._hooks)
        self.assertIn("OnUserLogin", self.dispatcher._hooks)
    
    def test_unregister_script(self):
        """Test script unregistration"""
        cleanup_called = []
        
        def my_cleanup():
            cleanup_called.append(True)
        
        def my_handler(): return 1
        
        script_id = self.dispatcher.register_script(
            script_name="TestScript",
            hooks={"OnTimer": my_handler},
            cleanup=my_cleanup
        )
        
        result = self.dispatcher.unregister_script(script_id)
        
        self.assertTrue(result)
        self.assertEqual(len(cleanup_called), 1)
        self.assertEqual(self.dispatcher._stats["active_scripts"], 0)
        self.assertNotIn(script_id, self.dispatcher._scripts)
    
    def test_unregister_nonexistent_script(self):
        """Test unregistering a script that doesn't exist"""
        result = self.dispatcher.unregister_script(999)
        self.assertFalse(result)


class TestDispatcherHookDispatching(unittest.TestCase):
    """Test hook dispatching to registered handlers"""
    
    def setUp(self):
        import dispatcher
        dispatcher._scripts.clear()
        dispatcher._hooks.clear()
        dispatcher._next_script_id = 1
        dispatcher._stats["total_scripts"] = 0
        dispatcher._stats["active_scripts"] = 0
        dispatcher._stats["total_calls"].clear()
        dispatcher._stats["failed_calls"].clear()
        dispatcher._stats["disabled_scripts"].clear()
        self.dispatcher = dispatcher
    
    def test_dispatch_to_single_handler(self):
        """Test dispatching to a single handler"""
        call_log = []
        
        def on_chat(nick, message):
            call_log.append((nick, message))
            return 1
        
        self.dispatcher.register_script("ChatLogger", {"OnParsedMsgChat": on_chat})
        
        result = self.dispatcher.OnParsedMsgChat("user1", "Hello world")
        
        self.assertEqual(result, 1)
        self.assertEqual(len(call_log), 1)
        self.assertEqual(call_log[0], ("user1", "Hello world"))
    
    def test_dispatch_to_multiple_handlers(self):
        """Test dispatching to multiple handlers"""
        call_order = []
        
        def handler1(nick, msg):
            call_order.append("handler1")
            return 1
        
        def handler2(nick, msg):
            call_order.append("handler2")
            return 1
        
        def handler3(nick, msg):
            call_order.append("handler3")
            return 1
        
        self.dispatcher.register_script("Script1", {"OnParsedMsgChat": handler1})
        self.dispatcher.register_script("Script2", {"OnParsedMsgChat": handler2})
        self.dispatcher.register_script("Script3", {"OnParsedMsgChat": handler3})
        
        result = self.dispatcher.OnParsedMsgChat("user", "test")
        
        self.assertEqual(result, 1)
        self.assertEqual(len(call_order), 3)
    
    def test_dispatch_respects_priority(self):
        """Test that handlers are called in priority order"""
        call_order = []
        
        def high_priority(nick, msg):
            call_order.append("high")
            return 1
        
        def medium_priority(nick, msg):
            call_order.append("medium")
            return 1
        
        def low_priority(nick, msg):
            call_order.append("low")
            return 1
        
        # Register in reverse order but with different priorities
        self.dispatcher.register_script("LowPrio", {"OnParsedMsgChat": low_priority}, priority=200)
        self.dispatcher.register_script("HighPrio", {"OnParsedMsgChat": high_priority}, priority=10)
        self.dispatcher.register_script("MedPrio", {"OnParsedMsgChat": medium_priority}, priority=100)
        
        self.dispatcher.OnParsedMsgChat("user", "test")
        
        self.assertEqual(call_order, ["high", "medium", "low"])
    
    def test_dispatch_handler_returning_zero_stops_chain(self):
        """Test that returning 0 stops the dispatch chain"""
        call_order = []
        
        def blocking_handler(nick, msg):
            call_order.append("blocker")
            return 0  # Block further processing
        
        def should_not_run(nick, msg):
            call_order.append("should_not_run")
            return 1
        
        self.dispatcher.register_script("Blocker", {"OnParsedMsgChat": blocking_handler}, priority=10)
        self.dispatcher.register_script("ShouldNotRun", {"OnParsedMsgChat": should_not_run}, priority=100)
        
        result = self.dispatcher.OnParsedMsgChat("user", "test")
        
        self.assertEqual(result, 0)
        self.assertEqual(call_order, ["blocker"])
    
    def test_dispatch_handler_exception_continues_chain(self):
        """Test that exceptions don't stop the chain"""
        call_order = []
        
        def failing_handler(nick, msg):
            call_order.append("failing")
            raise ValueError("Test error")
        
        def working_handler(nick, msg):
            call_order.append("working")
            return 1
        
        self.dispatcher.register_script("Failing", {"OnParsedMsgChat": failing_handler}, priority=10)
        self.dispatcher.register_script("Working", {"OnParsedMsgChat": working_handler}, priority=100)
        
        result = self.dispatcher.OnParsedMsgChat("user", "test")
        
        self.assertEqual(result, 1)
        self.assertEqual(call_order, ["failing", "working"])


class TestDispatcherEnableDisable(unittest.TestCase):
    """Test enable/disable functionality"""
    
    def setUp(self):
        import dispatcher
        dispatcher._scripts.clear()
        dispatcher._hooks.clear()
        dispatcher._next_script_id = 1
        dispatcher._stats["total_scripts"] = 0
        dispatcher._stats["active_scripts"] = 0
        dispatcher._stats["total_calls"].clear()
        dispatcher._stats["failed_calls"].clear()
        dispatcher._stats["disabled_scripts"].clear()
        self.dispatcher = dispatcher
    
    def test_disable_script(self):
        """Test disabling a script"""
        call_count = [0]
        
        def my_handler(nick, msg):
            call_count[0] += 1
            return 1
        
        script_id = self.dispatcher.register_script("TestScript", {"OnParsedMsgChat": my_handler})
        
        # Should work when enabled
        self.dispatcher.OnParsedMsgChat("user", "test")
        self.assertEqual(call_count[0], 1)
        
        # Disable and verify it doesn't run
        self.dispatcher.disable_script(script_id)
        self.dispatcher.OnParsedMsgChat("user", "test")
        self.assertEqual(call_count[0], 1)  # Still 1, didn't run
    
    def test_enable_script(self):
        """Test re-enabling a disabled script"""
        call_count = [0]
        
        def my_handler(nick, msg):
            call_count[0] += 1
            return 1
        
        script_id = self.dispatcher.register_script("TestScript", {"OnParsedMsgChat": my_handler})
        
        self.dispatcher.disable_script(script_id)
        self.dispatcher.OnParsedMsgChat("user", "test")
        self.assertEqual(call_count[0], 0)
        
        self.dispatcher.enable_script(script_id)
        self.dispatcher.OnParsedMsgChat("user", "test")
        self.assertEqual(call_count[0], 1)


class TestDispatcherStatistics(unittest.TestCase):
    """Test statistics tracking"""
    
    def setUp(self):
        import dispatcher
        dispatcher._scripts.clear()
        dispatcher._hooks.clear()
        dispatcher._next_script_id = 1
        dispatcher._stats["total_scripts"] = 0
        dispatcher._stats["active_scripts"] = 0
        dispatcher._stats["total_calls"].clear()
        dispatcher._stats["failed_calls"].clear()
        dispatcher._stats["disabled_scripts"].clear()
        self.dispatcher = dispatcher
    
    def test_track_total_calls(self):
        """Test tracking of total hook calls"""
        def my_handler(nick, msg): return 1
        
        self.dispatcher.register_script("TestScript", {"OnParsedMsgChat": my_handler})
        
        for _ in range(5):
            self.dispatcher.OnParsedMsgChat("user", "test")
        
        stats = self.dispatcher.get_stats()
        self.assertEqual(stats["total_calls"]["OnParsedMsgChat"], 5)
    
    def test_track_failed_calls(self):
        """Test tracking of failed hook calls"""
        def failing_handler(nick, msg):
            raise ValueError("Test error")
        
        self.dispatcher.register_script("FailingScript", {"OnParsedMsgChat": failing_handler})
        
        for _ in range(3):
            self.dispatcher.OnParsedMsgChat("user", "test")
        
        stats = self.dispatcher.get_stats()
        self.assertEqual(stats["failed_calls"]["OnParsedMsgChat"], 3)


class TestDispatcherThreadSafety(unittest.TestCase):
    """Test thread safety of the dispatcher"""
    
    def setUp(self):
        import dispatcher
        dispatcher._scripts.clear()
        dispatcher._hooks.clear()
        dispatcher._next_script_id = 1
        dispatcher._stats["total_scripts"] = 0
        dispatcher._stats["active_scripts"] = 0
        dispatcher._stats["total_calls"].clear()
        dispatcher._stats["failed_calls"].clear()
        dispatcher._stats["disabled_scripts"].clear()
        self.dispatcher = dispatcher
    
    def test_concurrent_registration(self):
        """Test concurrent script registration"""
        registered_ids = []
        lock = threading.Lock()
        
        def register_script(name):
            def handler(): return 1
            script_id = self.dispatcher.register_script(name, {"OnTimer": handler})
            with lock:
                registered_ids.append(script_id)
        
        threads = []
        for i in range(10):
            t = threading.Thread(target=register_script, args=(f"Script{i}",))
            threads.append(t)
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        # All IDs should be unique
        self.assertEqual(len(registered_ids), 10)
        self.assertEqual(len(set(registered_ids)), 10)
    
    def test_concurrent_dispatching(self):
        """Test concurrent hook dispatching"""
        call_count = [0]
        count_lock = threading.Lock()
        
        def my_handler(msec):
            with count_lock:
                call_count[0] += 1
            return 1
        
        self.dispatcher.register_script("TestScript", {"OnTimer": my_handler})
        
        def dispatch_calls():
            for _ in range(100):
                self.dispatcher.OnTimer(0)
        
        threads = []
        for _ in range(5):
            t = threading.Thread(target=dispatch_calls)
            threads.append(t)
        
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        self.assertEqual(call_count[0], 500)


class TestDispatcherAllHooks(unittest.TestCase):
    """Test all supported hooks are dispatchable"""
    
    def setUp(self):
        import dispatcher
        dispatcher._scripts.clear()
        dispatcher._hooks.clear()
        dispatcher._next_script_id = 1
        dispatcher._stats["total_scripts"] = 0
        dispatcher._stats["active_scripts"] = 0
        dispatcher._stats["total_calls"].clear()
        dispatcher._stats["failed_calls"].clear()
        dispatcher._stats["disabled_scripts"].clear()
        self.dispatcher = dispatcher
    
    def test_ontimer(self):
        """Test OnTimer hook"""
        called = []
        def handler(msec): 
            called.append(msec)
            return 1
        self.dispatcher.register_script("Test", {"OnTimer": handler})
        self.dispatcher.OnTimer(1000)
        self.assertEqual(called, [1000])
    
    def test_onparsedmsgchat(self):
        """Test OnParsedMsgChat hook"""
        called = []
        def handler(nick, msg): 
            called.append((nick, msg))
            return 1
        self.dispatcher.register_script("Test", {"OnParsedMsgChat": handler})
        self.dispatcher.OnParsedMsgChat("user", "hello")
        self.assertEqual(called, [("user", "hello")])
    
    def test_onuserlogin(self):
        """Test OnUserLogin hook"""
        called = []
        def handler(nick): 
            called.append(nick)
            return 1
        self.dispatcher.register_script("Test", {"OnUserLogin": handler})
        self.dispatcher.OnUserLogin("newuser")
        self.assertEqual(called, ["newuser"])
    
    def test_onuserlogout(self):
        """Test OnUserLogout hook"""
        called = []
        def handler(nick): 
            called.append(nick)
            return 1
        self.dispatcher.register_script("Test", {"OnUserLogout": handler})
        self.dispatcher.OnUserLogout("leavinguser")
        self.assertEqual(called, ["leavinguser"])
    
    def test_onnewconn(self):
        """Test OnNewConn hook"""
        called = []
        def handler(ip): 
            called.append(ip)
            return 1
        self.dispatcher.register_script("Test", {"OnNewConn": handler})
        self.dispatcher.OnNewConn("192.168.1.1")
        self.assertEqual(called, ["192.168.1.1"])


class TestDispatcherScriptInfo(unittest.TestCase):
    """Test script info and listing functions"""
    
    def setUp(self):
        import dispatcher
        dispatcher._scripts.clear()
        dispatcher._hooks.clear()
        dispatcher._next_script_id = 1
        dispatcher._stats["total_scripts"] = 0
        dispatcher._stats["active_scripts"] = 0
        dispatcher._stats["total_calls"].clear()
        dispatcher._stats["failed_calls"].clear()
        dispatcher._stats["disabled_scripts"].clear()
        self.dispatcher = dispatcher
    
    def test_get_script_info(self):
        """Test getting script info"""
        def handler(): return 1
        script_id = self.dispatcher.register_script("InfoTest", {"OnTimer": handler}, priority=50)
        
        info = self.dispatcher.get_script_info(script_id)
        
        self.assertIsNotNone(info)
        self.assertEqual(info["name"], "InfoTest")
        self.assertEqual(info["priority"], 50)
        self.assertTrue(info["enabled"])
    
    def test_list_scripts(self):
        """Test listing all scripts"""
        def handler(): return 1
        
        self.dispatcher.register_script("Script1", {"OnTimer": handler})
        self.dispatcher.register_script("Script2", {"OnTimer": handler})
        self.dispatcher.register_script("Script3", {"OnTimer": handler})
        
        scripts = self.dispatcher.list_scripts()
        
        self.assertEqual(len(scripts), 3)
        names = [s["name"] for s in scripts.values()]
        self.assertIn("Script1", names)
        self.assertIn("Script2", names)
        self.assertIn("Script3", names)


def run_tests():
    """Run all dispatcher tests"""
    print("\n" + "=" * 70)
    print("VERLIHUB HOOK DISPATCHER - STANDALONE UNIT TESTS")
    print("=" * 70)
    print("\nThese tests validate the dispatcher module for single-interpreter mode")
    print("without requiring a running Verlihub instance.\n")
    
    # Run tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestDispatcherRegistration))
    suite.addTests(loader.loadTestsFromTestCase(TestDispatcherHookDispatching))
    suite.addTests(loader.loadTestsFromTestCase(TestDispatcherEnableDisable))
    suite.addTests(loader.loadTestsFromTestCase(TestDispatcherStatistics))
    suite.addTests(loader.loadTestsFromTestCase(TestDispatcherThreadSafety))
    suite.addTests(loader.loadTestsFromTestCase(TestDispatcherAllHooks))
    suite.addTests(loader.loadTestsFromTestCase(TestDispatcherScriptInfo))
    
    # Run with verbosity
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Print summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures:  {len(result.failures)}")
    print(f"Errors:    {len(result.errors)}")
    print(f"Skipped:   {len(result.skipped)}")
    
    return len(result.failures) == 0 and len(result.errors) == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
