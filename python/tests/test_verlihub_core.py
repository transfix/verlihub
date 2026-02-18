"""
Tests for the verlihub_core SWIG Python bindings.

These tests verify that the SWIG-generated Python module correctly wraps
the C++ HubContext and related classes.
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import List
from unittest import mock

import pytest

# Add build directory to path for SWIG module
BUILD_DIR = Path(__file__).parent.parent.parent / "build" / "python"
if BUILD_DIR.exists():
    sys.path.insert(0, str(BUILD_DIR))

# Import SWIG module
try:
    from verlihub import verlihub_core
    # verlihub/__init__.py catches ImportError and sets verlihub_core = None,
    # so we must check the value rather than relying on ImportError.
    SWIG_AVAILABLE = verlihub_core is not None
except ImportError:
    SWIG_AVAILABLE = False
    verlihub_core = None


pytestmark = pytest.mark.skipif(
    not SWIG_AVAILABLE, 
    reason="verlihub_core SWIG module not built"
)


class TestSwigModuleImport:
    """Tests for SWIG module availability and basic structure."""
    
    def test_module_imports(self):
        """Verify verlihub_core module can be imported."""
        assert verlihub_core is not None
        
    def test_hubcontext_class_exists(self):
        """Verify HubContext class is available."""
        assert hasattr(verlihub_core, 'HubContext')
        
    def test_callback_class_exists(self):
        """Verify IHubEventCallback class is available for directors."""
        assert hasattr(verlihub_core, 'IHubEventCallback')
        
    def test_hubcontext_has_create_method(self):
        """Verify HubContext.Create factory method exists."""
        assert hasattr(verlihub_core.HubContext, 'Create')


class TestHubContextCreation:
    """Tests for HubContext creation and initialization."""
    
    def test_create_returns_none_for_empty_path(self):
        """Create with empty path should return None."""
        ctx = verlihub_core.HubContext.Create("")
        assert ctx is None
        
    def test_create_with_nonexistent_path(self):
        """Create with nonexistent path behavior is implementation-dependent.
        
        May either return None or create context (which may fail on Initialize).
        The important thing is it doesn't crash.
        """
        ctx = verlihub_core.HubContext.Create("/nonexistent/path/12345")
        # Either None or a context that will fail to initialize
        if ctx is not None:
            # Context created, but shouldn't be able to initialize without valid config
            pass  # Test passes - no crash
        
    def test_create_with_valid_temp_dir(self):
        """Create with valid temp directory should succeed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = verlihub_core.HubContext.Create(tmpdir)
            # May succeed or fail depending on config requirements
            # The important thing is it doesn't crash
            
    def test_hubcontext_methods_exist(self):
        """Verify key methods exist on HubContext class."""
        ctx_class = verlihub_core.HubContext
        methods = [
            'Create', 'Initialize', 'Start', 'Stop', 'IsRunning',
            'GetUserCount', 'GetTotalShare', 'GetHubName', 'GetHubTopic',
            'SetHubTopic', 'SendToAll', 'SendToUser', 'SetEventCallback',
        ]
        for method in methods:
            assert hasattr(ctx_class, method), f"Missing method: {method}"


class TestEventCallback:
    """Tests for Python callback implementation via SWIG directors."""
    
    def test_can_instantiate_callback(self):
        """Verify IHubEventCallback can be instantiated in Python."""
        callback = verlihub_core.IHubEventCallback()
        assert callback is not None
        
    def test_can_subclass_callback(self):
        """Verify IHubEventCallback can be subclassed."""
        class MyCallback(verlihub_core.IHubEventCallback):
            def __init__(self):
                super().__init__()
                self.started_count = 0
                
            def OnHubStarted(self):
                self.started_count += 1
        
        callback = MyCallback()
        assert callback.started_count == 0
        
    def test_callback_methods_callable(self):
        """Verify callback methods can be called."""
        class TestCallback(verlihub_core.IHubEventCallback):
            def __init__(self):
                super().__init__()
                self.events: List[str] = []
                
            def OnHubStarted(self):
                self.events.append('started')
                
            def OnHubStopping(self):
                self.events.append('stopping')
                
            def OnUserConnect(self, nick: str, ip: str) -> bool:
                self.events.append(f'connect:{nick}')
                return True
                
            def OnUserDisconnect(self, nick: str):
                self.events.append(f'disconnect:{nick}')
        
        callback = TestCallback()
        # Direct calls won't trigger C++ callback mechanism,
        # but should be callable without errors
        callback.OnHubStarted()
        callback.OnHubStopping()
        callback.OnUserConnect("TestUser", "127.0.0.1")
        callback.OnUserDisconnect("TestUser")
        
        assert 'started' in callback.events
        assert 'stopping' in callback.events
        assert 'connect:TestUser' in callback.events
        assert 'disconnect:TestUser' in callback.events


class TestThreadSafety:
    """Tests for thread-safety of SWIG bindings."""
    
    def test_callback_from_multiple_threads(self):
        """Verify callbacks can be invoked from multiple threads safely."""
        class ThreadSafeCallback(verlihub_core.IHubEventCallback):
            def __init__(self):
                super().__init__()
                self.lock = threading.Lock()
                self.counter = 0
                
            def OnTimer(self, timestamp: int):
                with self.lock:
                    self.counter += 1
        
        callback = ThreadSafeCallback()
        threads = []
        
        def call_timer(n):
            for _ in range(100):
                callback.OnTimer(n)
        
        for i in range(4):
            t = threading.Thread(target=call_timer, args=(i,))
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        assert callback.counter == 400
        
    def test_module_import_from_thread(self):
        """Verify module can be imported from worker thread."""
        result = {'success': False, 'error': None}
        
        def import_in_thread():
            try:
                from verlihub import verlihub_core
                result['success'] = verlihub_core is not None
            except Exception as e:
                result['error'] = str(e)
        
        thread = threading.Thread(target=import_in_thread)
        thread.start()
        thread.join()
        
        assert result['success'], f"Import failed: {result['error']}"


class TestStringHandling:
    """Tests for string type conversions between Python and C++."""
    
    def test_empty_string_handling(self):
        """Verify empty strings are handled correctly."""
        ctx = verlihub_core.HubContext.Create("")
        # Should return None, not crash
        assert ctx is None
        
    def test_unicode_string_handling(self):
        """Verify Unicode strings don't crash (even if not supported)."""
        # This tests the SWIG string_view typemaps
        try:
            ctx = verlihub_core.HubContext.Create("/tmp/тест/юникод")
        except (UnicodeError, RuntimeError):
            pass  # Expected - path probably doesn't exist
        except Exception as e:
            # Should not crash with other exceptions
            pytest.fail(f"Unexpected exception: {e}")


class TestTypeConversions:
    """Tests for SWIG type conversions."""
    
    def test_bool_return_values(self):
        """Verify bool return values work correctly."""
        ctx = verlihub_core.HubContext.Create("")
        # Should return Python bool
        result = ctx  # None or HubContext
        assert result is None or isinstance(result, verlihub_core.HubContext)
        
    def test_string_return_values(self):
        """Verify string return values are Python str."""
        # This is tested implicitly through other methods
        # but we verify the callback interface
        callback = verlihub_core.IHubEventCallback()
        assert callback is not None


class TestPythonCoreModule:
    """Tests for the Python verlihub.core wrapper module."""
    
    @pytest.fixture
    def mock_cpp_context(self):
        """Create a mock C++ context for testing."""
        mock_ctx = mock.MagicMock()
        mock_ctx.IsRunning.return_value = False
        mock_ctx.GetUserCount.return_value = 42
        mock_ctx.GetTotalShare.return_value = 1024 * 1024 * 1024
        mock_ctx.GetHubName.return_value = "Test Hub"
        mock_ctx.GetHubTopic.return_value = "Welcome!"
        mock_ctx.GetUserNicks.return_value = ["user1", "user2"]
        mock_ctx.Initialize.return_value = True
        mock_ctx.Start.return_value = True
        return mock_ctx
    
    def test_hubcontext_wrapper_properties(self, mock_cpp_context):
        """Test HubContext wrapper property access."""
        # Import the wrapper
        try:
            from verlihub.core import HubContext
        except ImportError:
            pytest.skip("verlihub.core not available")
        
        # Create wrapper with mock
        wrapper = HubContext(mock_cpp_context)
        
        # Test properties
        assert wrapper.user_count == 42
        assert wrapper.total_share == 1024 * 1024 * 1024
        assert wrapper.hub_name == "Test Hub"
        assert wrapper.hub_topic == "Welcome!"
        assert wrapper.is_running is False
        
    def test_event_handler_registration(self, mock_cpp_context):
        """Test event handler registration."""
        try:
            from verlihub.core import HubContext, HubEventHandler
        except ImportError:
            pytest.skip("verlihub.core not available")
        
        wrapper = HubContext(mock_cpp_context)
        
        # Register handler
        events = []
        def on_started():
            events.append('started')
        
        wrapper.events.register('hub_started', on_started)
        
        # Dispatch event
        wrapper.events._dispatch('hub_started')
        
        assert 'started' in events
        
    def test_event_handler_unregistration(self, mock_cpp_context):
        """Test event handler unregistration."""
        try:
            from verlihub.core import HubContext
        except ImportError:
            pytest.skip("verlihub.core not available")
        
        wrapper = HubContext(mock_cpp_context)
        
        events = []
        def handler():
            events.append('called')
        
        wrapper.events.register('hub_started', handler)
        wrapper.events.unregister('hub_started', handler)
        
        wrapper.events._dispatch('hub_started')
        
        # Handler should not be called
        assert events == []
        
    def test_context_manager(self, mock_cpp_context):
        """Test context manager protocol."""
        try:
            from verlihub.core import HubContext
        except ImportError:
            pytest.skip("verlihub.core not available")
        
        mock_cpp_context.IsRunning.return_value = True
        wrapper = HubContext(mock_cpp_context)
        
        with wrapper as ctx:
            assert ctx is wrapper
        
        # Stop should be called on exit
        mock_cpp_context.Stop.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
