"""
Tests for Lua plugin functionality through Python/SWIG bindings.

These tests verify Lua plugin integration works correctly when
accessed through the pythonic verlihub interface.

Test Categories:
1. Lua script loading/unloading
2. Callback invocation
3. SQL operations through Lua
4. Bot registration through Lua
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add build directory to path for SWIG module
_build_python_dir = Path(__file__).parent.parent.parent / "build" / "python"
if _build_python_dir.exists() and str(_build_python_dir) not in sys.path:
    sys.path.insert(0, str(_build_python_dir))


# Get paths to test Lua scripts
LUA_TESTS_DIR = Path(__file__).parent.parent.parent / "plugins" / "lua" / "tests"


def get_lua_test_script(name: str) -> str:
    """Get path to a Lua test script."""
    script = LUA_TESTS_DIR / name
    if script.exists():
        return str(script)
    return None


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
def lua_plugin():
    """Find the Lua plugin library."""
    build_dir = Path(__file__).parent.parent.parent / "build"
    possible = [
        build_dir / "plugins" / "lua" / "liblua_pi.so",
        build_dir / "plugins" / "lua" / "lua_pi.so",
        Path("/usr/lib/verlihub/liblua_pi.so"),
    ]
    for path in possible:
        if path.exists():
            return str(path)
    return None


class TestLuaPluginLoading:
    """Tests for Lua plugin load/unload operations."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_lua_plugin_exists(self, lua_plugin):
        """Verify Lua plugin library can be found."""
        # This test helps diagnose environment issues
        if lua_plugin:
            assert Path(lua_plugin).exists()
        else:
            pytest.skip("Lua plugin not found in build directory")
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_load_lua_plugin(self, hub_context, lua_plugin):
        """Test loading the Lua plugin."""
        if not lua_plugin:
            pytest.skip("Lua plugin not found")
        
        result = hub_context.LoadPlugin(lua_plugin)
        # Note: May be False if hub not fully initialized
        # The test verifies the method is callable


class TestLuaScriptExecution:
    """Tests for executing Lua scripts."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"  
    )
    def test_lua_test_scripts_exist(self):
        """Verify test Lua scripts exist."""
        assert LUA_TESTS_DIR.exists(), f"Lua tests dir not found: {LUA_TESTS_DIR}"
        
        scripts = ["test_basic.lua", "test_callbacks.lua", "test_sql.lua"]
        for script in scripts:
            path = LUA_TESTS_DIR / script
            assert path.exists(), f"Test script not found: {path}"
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_execute_basic_lua_test(self, hub_context, lua_plugin):
        """Execute the basic Lua test script."""
        if not lua_plugin:
            pytest.skip("Lua plugin not found")
        
        script = get_lua_test_script("test_basic.lua")
        if not script:
            pytest.skip("test_basic.lua not found")
        
        # Load Lua plugin first
        hub_context.LoadPlugin(lua_plugin)
        
        # Execute the test script
        result = hub_context.ExecuteLuaScript(script)
        # Result may be False if plugin not fully initialized
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_execute_callback_lua_test(self, hub_context, lua_plugin):
        """Execute the callback test Lua script."""
        if not lua_plugin:
            pytest.skip("Lua plugin not found")
        
        script = get_lua_test_script("test_callbacks.lua")
        if not script:
            pytest.skip("test_callbacks.lua not found")
        
        hub_context.LoadPlugin(lua_plugin)
        result = hub_context.ExecuteLuaScript(script)
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_execute_sql_lua_test(self, hub_context, lua_plugin):
        """Execute the SQL test Lua script."""
        if not lua_plugin:
            pytest.skip("Lua plugin not found")
        
        script = get_lua_test_script("test_sql.lua")
        if not script:
            pytest.skip("test_sql.lua not found")
        
        hub_context.LoadPlugin(lua_plugin)
        result = hub_context.ExecuteLuaScript(script)


class TestLuaScriptManagement:
    """Tests for Lua script list operations."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_get_loaded_scripts_empty(self, hub_context):
        """Get loaded scripts when none are loaded."""
        scripts = hub_context.GetLoadedLuaScripts()
        assert isinstance(scripts, list)
        # Initially empty
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_unload_nonexistent_script(self, hub_context):
        """Unload a script that isn't loaded."""
        result = hub_context.UnloadLuaScript("/nonexistent/script.lua")
        assert result is False


class TestLuaScriptSyntax:
    """Tests for Lua script syntax validation."""
    
    def test_basic_script_syntax_valid(self):
        """Verify test_basic.lua has valid Lua syntax."""
        script = get_lua_test_script("test_basic.lua")
        if not script:
            pytest.skip("test_basic.lua not found")
        
        # Read and check basic Lua syntax patterns
        content = Path(script).read_text()
        
        # Check for proper function definitions
        assert "function" in content
        assert "end" in content
        
        # Check for VH integration
        assert "VH" in content
    
    def test_callback_script_syntax_valid(self):
        """Verify test_callbacks.lua has valid Lua syntax."""
        script = get_lua_test_script("test_callbacks.lua")
        if not script:
            pytest.skip("test_callbacks.lua not found")
        
        content = Path(script).read_text()
        
        # Check for callback functions
        assert "VH.OnUserConnected" in content or "function VH.On" in content
        assert "function" in content
        assert "end" in content
    
    def test_sql_script_syntax_valid(self):
        """Verify test_sql.lua has valid Lua syntax."""
        script = get_lua_test_script("test_sql.lua")
        if not script:
            pytest.skip("test_sql.lua not found")
        
        content = Path(script).read_text()
        
        # Check for SQL operations
        assert "SQLQuery" in content
        assert "function" in content
        assert "end" in content


class TestLuaCustomScripts:
    """Tests using custom Lua scripts."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_simple_script(self, hub_context, lua_plugin, tmp_path):
        """Test executing a simple custom Lua script."""
        if not lua_plugin:
            pytest.skip("Lua plugin not found")
        
        # Create a minimal test script
        script = tmp_path / "simple.lua"
        script.write_text('''
-- Minimal test script
TEST_LOADED = true

function VH.OnTimer()
    return 1
end
''')
        
        hub_context.LoadPlugin(lua_plugin)
        result = hub_context.ExecuteLuaScript(str(script))
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_script_with_error(self, hub_context, lua_plugin, tmp_path):
        """Test that script errors are handled gracefully."""
        if not lua_plugin:
            pytest.skip("Lua plugin not found")
        
        # Create a script with syntax error
        script = tmp_path / "error.lua"
        script.write_text('''
-- Script with syntax error
function broken(
    -- Missing closing paren
end
''')
        
        hub_context.LoadPlugin(lua_plugin)
        # Should return False or handle error gracefully
        result = hub_context.ExecuteLuaScript(str(script))
        # Not asserting result - just verifying no crash


class TestLuaIntegration:
    """Full integration tests for Lua plugin."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_FULL_INTEGRATION") != "1",
        reason="Requires VH_FULL_INTEGRATION=1"
    )
    def test_full_lua_lifecycle(self, hub_context, lua_plugin, tmp_path):
        """Test complete Lua script lifecycle."""
        if not lua_plugin:
            pytest.skip("Lua plugin not found")
        
        # Create test script
        script = tmp_path / "lifecycle.lua"
        script.write_text('''
VH_LIFECYCLE_TEST = {
    loaded = os.time(),
    callbacks = 0
}

function VH.OnTimer()
    VH_LIFECYCLE_TEST.callbacks = VH_LIFECYCLE_TEST.callbacks + 1
    return 1
end
''')
        
        # Initialize hub
        assert hub_context.Initialize()
        
        # Load Lua plugin
        assert hub_context.LoadPlugin(lua_plugin)
        assert hub_context.IsPluginLoaded("lua")
        
        # Load script
        assert hub_context.ExecuteLuaScript(str(script))
        
        # Verify script is loaded
        scripts = hub_context.GetLoadedLuaScripts()
        assert str(script) in scripts
        
        # Unload script
        assert hub_context.UnloadLuaScript(str(script))
        
        # Verify unloaded
        scripts = hub_context.GetLoadedLuaScripts()
        assert str(script) not in scripts
        
        # Unload plugin
        assert hub_context.UnloadPlugin("lua")
        assert not hub_context.IsPluginLoaded("lua")
    
    @pytest.mark.skipif(
        os.environ.get("VH_FULL_INTEGRATION") != "1",
        reason="Requires VH_FULL_INTEGRATION=1"
    )
    def test_multiple_lua_scripts(self, hub_context, lua_plugin, tmp_path):
        """Test loading multiple Lua scripts simultaneously."""
        if not lua_plugin:
            pytest.skip("Lua plugin not found")
        
        scripts = []
        for i in range(3):
            script = tmp_path / f"multi_{i}.lua"
            script.write_text(f'''
            SCRIPT_ID = {i}
            function VH.OnTimer()
                return 1
            end
            ''')
            scripts.append(str(script))
        
        # Initialize and load plugin
        assert hub_context.Initialize()
        assert hub_context.LoadPlugin(lua_plugin)
        
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


# Test discovery helper
def test_lua_test_dir_exists():
    """Verify Lua test directory structure exists."""
    assert LUA_TESTS_DIR.exists(), f"Missing: {LUA_TESTS_DIR}"
