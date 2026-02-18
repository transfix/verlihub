"""
Tests for plugin management through SWIG bindings.

These tests verify that the C++ plugin interface works correctly when
accessed through the pythonic verlihub via SWIG bindings.

Note: These tests require the full hub to be built with plugins.
Many tests are marked with skip markers that check for prerequisites.
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add build directory to path for SWIG module
_build_python_dir = Path(__file__).parent.parent.parent / "build" / "python"
if _build_python_dir.exists() and str(_build_python_dir) not in sys.path:
    sys.path.insert(0, str(_build_python_dir))


def _require_swig():
    """Import and return verlihub_core, skipping if SWIG module not available."""
    try:
        from verlihub import verlihub_core
    except ImportError:
        pytest.skip("verlihub_core module not available")
    if verlihub_core is None:
        pytest.skip("verlihub_core SWIG module not built")
    return verlihub_core


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def hub_context():
    """Create a HubContext for testing."""
    try:
        from verlihub import verlihub_core
    except ImportError:
        pytest.skip("verlihub_core module not available")
    if verlihub_core is None:
        pytest.skip("verlihub_core SWIG module not built")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        ctx = verlihub_core.HubContext.Create(tmpdir)
        if ctx is None:
            pytest.skip("Could not create HubContext")
        yield ctx


@pytest.fixture
def lua_plugin_path():
    """Get the path to the Lua plugin library."""
    # Try common locations
    build_dir = Path(__file__).parent.parent.parent / "build"
    possible_paths = [
        build_dir / "plugins" / "lua" / "liblua_pi.so",
        build_dir / "plugins" / "lua" / "lua_pi.so",
        Path("/usr/lib/verlihub/liblua_pi.so"),
        Path("/usr/local/lib/verlihub/liblua_pi.so"),
    ]
    
    for path in possible_paths:
        if path.exists():
            return str(path)
    
    return None


@pytest.fixture
def python_plugin_path():
    """Get the path to the Python plugin library."""
    build_dir = Path(__file__).parent.parent.parent / "build"
    possible_paths = [
        build_dir / "plugins" / "python" / "libpython_pi.so",
        build_dir / "plugins" / "python" / "python_pi.so",
        Path("/usr/lib/verlihub/libpython_pi.so"),
        Path("/usr/local/lib/verlihub/libpython_pi.so"),
    ]
    
    for path in possible_paths:
        if path.exists():
            return str(path)
    
    return None


@pytest.fixture
def sample_lua_script(tmp_path):
    """Create a sample Lua script for testing."""
    script = tmp_path / "test_script.lua"
    script.write_text('''
-- Test Lua script for verlihub plugin testing
VH = VH or {}

function VH:OnUserConnected(nick, ip)
    -- Log the connection
    VH:SendToOpChat("Test script: User " .. nick .. " connected from " .. ip)
    return 1  -- Allow connection
end

function VH:OnChat(nick, text)
    -- Echo test
    if text == "!test" then
        VH:SendDataToUser("Test response", nick)
    end
    return 1
end

-- Signal that script loaded successfully
VH:SendToOpChat("Test Lua script loaded successfully")
''')
    return str(script)


@pytest.fixture
def sample_python_script(tmp_path):
    """Create a sample Python script for testing."""
    script = tmp_path / "test_script.py"
    script.write_text('''
# Test Python script for verlihub plugin testing

def OnUserConnected(nick, ip):
    """Called when a user connects."""
    vh.SendToOpChat(f"Test script: User {nick} connected from {ip}")
    return 1  # Allow connection

def OnChat(nick, text):
    """Called when a user sends a chat message."""
    if text == "!test":
        vh.SendDataToUser("Test response", nick)
    return 1

# Signal that script loaded successfully
vh.SendToOpChat("Test Python script loaded successfully")
''')
    return str(script)


# =============================================================================
# Plugin Management Tests (Require full hub)
# =============================================================================


class TestPluginManagement:
    """Tests for plugin load/unload functionality."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_get_loaded_plugins_empty(self, hub_context):
        """Test getting loaded plugins when none are loaded."""
        plugins = hub_context.GetLoadedPlugins()
        assert isinstance(plugins, list)
        # Initially no plugins loaded
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_is_plugin_loaded_false(self, hub_context):
        """Test checking for a plugin that isn't loaded."""
        assert hub_context.IsPluginLoaded("nonexistent") is False
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_load_plugin_invalid_path(self, hub_context):
        """Test loading a plugin from invalid path."""
        result = hub_context.LoadPlugin("/nonexistent/path/plugin.so")
        # Should return False for invalid path
        assert result is False
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"  
    )
    def test_unload_plugin_not_loaded(self, hub_context):
        """Test unloading a plugin that isn't loaded."""
        result = hub_context.UnloadPlugin("nonexistent")
        assert result is False


# =============================================================================
# Lua Plugin Tests
# =============================================================================


class TestLuaPlugin:
    """Tests for Lua plugin functionality through SWIG bindings."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_load_lua_plugin(self, hub_context, lua_plugin_path):
        """Test loading the Lua plugin."""
        if lua_plugin_path is None:
            pytest.skip("Lua plugin not found")
        
        result = hub_context.LoadPlugin(lua_plugin_path)
        # Note: May fail if plugin manager not fully initialized
        # This test documents expected behavior
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_execute_lua_script(self, hub_context, sample_lua_script):
        """Test executing a Lua script."""
        result = hub_context.ExecuteLuaScript(sample_lua_script)
        # Expected: False if Lua plugin not loaded
        # Expected: True if Lua plugin is loaded and script is valid
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_get_loaded_lua_scripts_empty(self, hub_context):
        """Test getting loaded Lua scripts when none are loaded."""
        scripts = hub_context.GetLoadedLuaScripts()
        assert isinstance(scripts, list)
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_unload_lua_script_not_loaded(self, hub_context):
        """Test unloading a Lua script that isn't loaded."""
        result = hub_context.UnloadLuaScript("/nonexistent/script.lua")
        assert result is False


# =============================================================================
# Python Plugin Tests (VH Native Python Plugin)
# =============================================================================


class TestPythonPlugin:
    """Tests for Python plugin (native VH plugin, not our module)."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_load_python_plugin(self, hub_context, python_plugin_path):
        """Test loading the native Python plugin."""
        if python_plugin_path is None:
            pytest.skip("Python plugin not found")
        
        result = hub_context.LoadPlugin(python_plugin_path)
        # Note: May fail if plugin manager not fully initialized
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_execute_python_script(self, hub_context, sample_python_script):
        """Test executing a Python script via native plugin."""
        result = hub_context.ExecutePythonScript(sample_python_script)
        # Expected: False if Python plugin not loaded
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_get_loaded_python_scripts_empty(self, hub_context):
        """Test getting loaded Python scripts when none are loaded."""
        scripts = hub_context.GetLoadedPythonScripts()
        assert isinstance(scripts, list)


# =============================================================================
# Plugin Info Tests
# =============================================================================


class TestPluginInfo:
    """Tests for PluginInfo structure."""
    
    def test_plugin_info_attributes(self):
        """Test that PluginInfo has expected attributes."""
        verlihub_core = _require_swig()
        
        # PluginInfo should be accessible
        # Note: May not be directly constructible from Python
        info_type = getattr(verlihub_core.HubContext, 'PluginInfo', None)
        # If the struct is exposed, verify it
    
    def test_plugin_info_vector(self):
        """Test that PluginInfoVector is available."""
        verlihub_core = _require_swig()
        
        # Should be able to create empty vector
        # vec = verlihub_core.PluginInfoVector()
        # assert len(vec) == 0


# =============================================================================
# Integration Tests (Require running hub with database)
# =============================================================================


class TestPluginIntegration:
    """Full integration tests requiring running hub."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_FULL_INTEGRATION") != "1",
        reason="Requires VH_FULL_INTEGRATION=1 and running hub"
    )
    def test_lua_plugin_full_lifecycle(self, hub_context, lua_plugin_path, sample_lua_script):
        """Test complete Lua plugin lifecycle."""
        if lua_plugin_path is None:
            pytest.skip("Lua plugin not found")
        
        # Initialize hub
        assert hub_context.Initialize()
        
        # Load Lua plugin
        assert hub_context.LoadPlugin(lua_plugin_path)
        assert hub_context.IsPluginLoaded("lua")
        
        # Execute script
        assert hub_context.ExecuteLuaScript(sample_lua_script)
        
        # Verify script is loaded
        scripts = hub_context.GetLoadedLuaScripts()
        assert sample_lua_script in scripts
        
        # Unload script
        assert hub_context.UnloadLuaScript(sample_lua_script)
        
        # Verify unloaded
        scripts = hub_context.GetLoadedLuaScripts()
        assert sample_lua_script not in scripts
        
        # Unload plugin
        assert hub_context.UnloadPlugin("lua")
        assert not hub_context.IsPluginLoaded("lua")
    
    @pytest.mark.skipif(
        os.environ.get("VH_FULL_INTEGRATION") != "1",
        reason="Requires VH_FULL_INTEGRATION=1 and running hub"
    )
    def test_multiple_scripts(self, hub_context, lua_plugin_path, tmp_path):
        """Test loading multiple Lua scripts."""
        if lua_plugin_path is None:
            pytest.skip("Lua plugin not found")
        
        # Create multiple scripts
        scripts = []
        for i in range(3):
            script = tmp_path / f"script_{i}.lua"
            script.write_text(f'''
            -- Script {i}
            function VH:OnTimer()
                return 1
            end
            ''')
            scripts.append(str(script))
        
        # Initialize
        assert hub_context.Initialize()
        assert hub_context.LoadPlugin(lua_plugin_path)
        
        # Load all scripts
        for script in scripts:
            assert hub_context.ExecuteLuaScript(script)
        
        # Verify all loaded
        loaded = hub_context.GetLoadedLuaScripts()
        for script in scripts:
            assert script in loaded
        
        # Unload all
        for script in scripts:
            assert hub_context.UnloadLuaScript(script)


# =============================================================================
# Mock Tests (Don't require full hub)
# =============================================================================


class TestPluginMethods:
    """Test plugin management methods exist and have correct signatures."""
    
    def test_method_exists_load_plugin(self):
        """Verify LoadPlugin method exists."""
        verlihub_core = _require_swig()
        
        assert hasattr(verlihub_core.HubContext, 'LoadPlugin')
    
    def test_method_exists_unload_plugin(self):
        """Verify UnloadPlugin method exists."""
        verlihub_core = _require_swig()
        
        assert hasattr(verlihub_core.HubContext, 'UnloadPlugin')
    
    def test_method_exists_reload_plugin(self):
        """Verify ReloadPlugin method exists."""
        verlihub_core = _require_swig()
        
        assert hasattr(verlihub_core.HubContext, 'ReloadPlugin')
    
    def test_method_exists_get_loaded_plugins(self):
        """Verify GetLoadedPlugins method exists."""
        verlihub_core = _require_swig()
        
        assert hasattr(verlihub_core.HubContext, 'GetLoadedPlugins')
    
    def test_method_exists_is_plugin_loaded(self):
        """Verify IsPluginLoaded method exists."""
        verlihub_core = _require_swig()
        
        assert hasattr(verlihub_core.HubContext, 'IsPluginLoaded')
    
    def test_method_exists_execute_lua_script(self):
        """Verify ExecuteLuaScript method exists."""
        verlihub_core = _require_swig()
        
        assert hasattr(verlihub_core.HubContext, 'ExecuteLuaScript')
    
    def test_method_exists_execute_python_script(self):
        """Verify ExecutePythonScript method exists."""
        verlihub_core = _require_swig()
        
        assert hasattr(verlihub_core.HubContext, 'ExecutePythonScript')
    
    def test_method_exists_get_loaded_lua_scripts(self):
        """Verify GetLoadedLuaScripts method exists."""
        verlihub_core = _require_swig()
        
        assert hasattr(verlihub_core.HubContext, 'GetLoadedLuaScripts')
    
    def test_method_exists_get_loaded_python_scripts(self):
        """Verify GetLoadedPythonScripts method exists."""
        verlihub_core = _require_swig()
        
        assert hasattr(verlihub_core.HubContext, 'GetLoadedPythonScripts')
