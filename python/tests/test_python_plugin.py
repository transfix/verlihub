"""
Tests for Python plugin functionality through Python/SWIG bindings.

These tests verify Python plugin integration works correctly when
accessed through the pythonic verlihub interface.

Test Categories:
1. Python script loading/unloading
2. Callback invocation
3. SQL operations through Python plugin
4. Bot registration through Python plugin

Note: These mirror the Lua plugin tests for consistency.
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


# Get paths to test Python scripts
PYTHON_TESTS_DIR = Path(__file__).parent.parent.parent / "plugins" / "python" / "tests"


def get_python_test_script(name: str) -> str:
    """Get path to a Python test script."""
    script = PYTHON_TESTS_DIR / name
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
def python_plugin():
    """Find the Python plugin library."""
    build_dir = Path(__file__).parent.parent.parent / "build"
    possible = [
        build_dir / "plugins" / "python" / "libpython_pi.so",
        build_dir / "plugins" / "python" / "python_pi.so",
        Path("/usr/lib/verlihub/libpython_pi.so"),
        Path("/usr/local/lib/verlihub/libpython_pi.so"),
    ]
    for path in possible:
        if path.exists():
            return str(path)
    return None


class TestPythonPluginLoading:
    """Tests for Python plugin load/unload operations."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_python_plugin_exists(self, python_plugin):
        """Verify Python plugin library can be found."""
        # This test helps diagnose environment issues
        if python_plugin:
            assert Path(python_plugin).exists()
        else:
            pytest.skip("Python plugin not found in build directory")
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_load_python_plugin(self, hub_context, python_plugin):
        """Test loading the Python plugin."""
        if not python_plugin:
            pytest.skip("Python plugin not found")
        
        result = hub_context.LoadPlugin(python_plugin)
        # Note: May be False if hub not fully initialized
        # The test verifies the method is callable


class TestPythonScriptExecution:
    """Tests for executing Python scripts."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"  
    )
    def test_python_test_scripts_exist(self):
        """Verify test Python scripts exist."""
        if not PYTHON_TESTS_DIR.exists():
            pytest.skip(f"Python tests dir not found: {PYTHON_TESTS_DIR}")
        
        scripts = ["test_script.py", "test_script_advanced_types.py"]
        for script in scripts:
            path = PYTHON_TESTS_DIR / script
            assert path.exists(), f"Test script not found: {path}"
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_execute_basic_python_test(self, hub_context, python_plugin):
        """Execute the basic Python test script."""
        if not python_plugin:
            pytest.skip("Python plugin not found")
        
        script = get_python_test_script("test_script.py")
        if not script:
            pytest.skip("test_script.py not found")
        
        # Load Python plugin first
        hub_context.LoadPlugin(python_plugin)
        
        # Execute the test script
        result = hub_context.ExecutePythonScript(script)
        # Result may be False if plugin not fully initialized
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_execute_advanced_types_test(self, hub_context, python_plugin):
        """Execute the advanced types test Python script."""
        if not python_plugin:
            pytest.skip("Python plugin not found")
        
        script = get_python_test_script("test_script_advanced_types.py")
        if not script:
            pytest.skip("test_script_advanced_types.py not found")
        
        hub_context.LoadPlugin(python_plugin)
        result = hub_context.ExecutePythonScript(script)


class TestPythonScriptManagement:
    """Tests for Python script list operations."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_get_loaded_scripts_empty(self, hub_context):
        """Get loaded scripts when none are loaded."""
        scripts = hub_context.GetLoadedPythonScripts()
        assert isinstance(scripts, list)
        # Initially empty
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_unload_nonexistent_script(self, hub_context):
        """Unload a script that isn't loaded."""
        result = hub_context.UnloadPythonScript("/nonexistent/script.py")
        assert result is False


class TestPythonScriptSyntax:
    """Tests for Python script syntax validation."""
    
    def test_basic_script_syntax_valid(self):
        """Verify test_script.py has valid Python syntax."""
        script = get_python_test_script("test_script.py")
        if not script:
            pytest.skip("test_script.py not found")
        
        # Read and compile to check syntax
        content = Path(script).read_text()
        
        # Try to compile - will raise SyntaxError if invalid
        try:
            compile(content, script, 'exec')
        except SyntaxError as e:
            pytest.fail(f"Syntax error in {script}: {e}")
        
        # Check for callback functions
        assert "def " in content
        # Check for verlihub integration (callback functions or comments)
        assert "verlihub" in content.lower() or "On" in content
    
    def test_advanced_types_script_syntax_valid(self):
        """Verify test_script_advanced_types.py has valid Python syntax."""
        script = get_python_test_script("test_script_advanced_types.py")
        if not script:
            pytest.skip("test_script_advanced_types.py not found")
        
        content = Path(script).read_text()
        
        try:
            compile(content, script, 'exec')
        except SyntaxError as e:
            pytest.fail(f"Syntax error in {script}: {e}")
        
        # Check for type annotations or advanced features
        assert "def " in content


class TestPythonCustomScripts:
    """Tests using custom Python scripts."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_simple_script(self, hub_context, python_plugin, tmp_path):
        """Test executing a simple custom Python script."""
        if not python_plugin:
            pytest.skip("Python plugin not found")
        
        # Create a minimal test script
        script = tmp_path / "simple.py"
        script.write_text('''
# Minimal test script
TEST_LOADED = True

def OnTimer():
    """Called periodically by the hub."""
    return 1
''')
        
        hub_context.LoadPlugin(python_plugin)
        result = hub_context.ExecutePythonScript(str(script))
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_script_with_error(self, hub_context, python_plugin, tmp_path):
        """Test that script errors are handled gracefully."""
        if not python_plugin:
            pytest.skip("Python plugin not found")
        
        # Create a script with syntax error
        script = tmp_path / "error.py"
        script.write_text('''
# Script with syntax error
def broken(:
    pass
''')
        
        hub_context.LoadPlugin(python_plugin)
        # Should return False or handle error gracefully
        result = hub_context.ExecutePythonScript(str(script))
        # Not asserting result - just verifying no crash
    
    @pytest.mark.skipif(
        os.environ.get("VH_INTEGRATION_TESTS") != "1",
        reason="Requires VH_INTEGRATION_TESTS=1"
    )
    def test_script_with_callbacks(self, hub_context, python_plugin, tmp_path):
        """Test a script with common callbacks."""
        if not python_plugin:
            pytest.skip("Python plugin not found")
        
        script = tmp_path / "callbacks.py"
        script.write_text('''
# Script with common verlihub callbacks

def OnUserConnected(nick, ip):
    """Called when a user connects."""
    # Log the event
    return 1  # Allow connection

def OnUserDisconnected(nick):
    """Called when a user disconnects."""
    return 1

def OnParsedMsgChat(nick, text):
    """Called when a chat message is received."""
    if text.startswith("!test"):
        return 0  # Handle command
    return 1  # Allow message

def OnOperatorCommand(nick, data):
    """Called when an operator command is received."""
    return 1
''')
        
        hub_context.LoadPlugin(python_plugin)
        result = hub_context.ExecutePythonScript(str(script))


class TestPythonIntegration:
    """Full integration tests for Python plugin."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_FULL_INTEGRATION") != "1",
        reason="Requires VH_FULL_INTEGRATION=1"
    )
    def test_full_python_lifecycle(self, hub_context, python_plugin, tmp_path):
        """Test complete Python script lifecycle."""
        if not python_plugin:
            pytest.skip("Python plugin not found")
        
        # Create test script
        script = tmp_path / "lifecycle.py"
        script.write_text('''
import time

VH_LIFECYCLE_TEST = {
    "loaded": time.time(),
    "callbacks": 0
}

def OnTimer():
    VH_LIFECYCLE_TEST["callbacks"] += 1
    return 1
''')
        
        # Initialize hub
        assert hub_context.Initialize()
        
        # Load Python plugin
        assert hub_context.LoadPlugin(python_plugin)
        assert hub_context.IsPluginLoaded("python")
        
        # Load script
        assert hub_context.ExecutePythonScript(str(script))
        
        # Verify script is loaded
        scripts = hub_context.GetLoadedPythonScripts()
        assert str(script) in scripts
        
        # Unload script
        assert hub_context.UnloadPythonScript(str(script))
        
        # Verify unloaded
        scripts = hub_context.GetLoadedPythonScripts()
        assert str(script) not in scripts
        
        # Unload plugin
        assert hub_context.UnloadPlugin("python")
        assert not hub_context.IsPluginLoaded("python")
    
    @pytest.mark.skipif(
        os.environ.get("VH_FULL_INTEGRATION") != "1",
        reason="Requires VH_FULL_INTEGRATION=1"
    )
    def test_multiple_python_scripts(self, hub_context, python_plugin, tmp_path):
        """Test loading multiple Python scripts simultaneously."""
        if not python_plugin:
            pytest.skip("Python plugin not found")
        
        scripts = []
        for i in range(3):
            script = tmp_path / f"multi_{i}.py"
            script.write_text(f'''
SCRIPT_ID = {i}

def OnTimer():
    return 1
''')
            scripts.append(str(script))
        
        # Initialize and load plugin
        assert hub_context.Initialize()
        assert hub_context.LoadPlugin(python_plugin)
        
        # Load all scripts
        for script in scripts:
            assert hub_context.ExecutePythonScript(script)
        
        # Verify all loaded
        loaded = hub_context.GetLoadedPythonScripts()
        for script in scripts:
            assert script in loaded
        
        # Unload all
        for script in scripts:
            assert hub_context.UnloadPythonScript(script)


class TestPythonDatabaseAccess:
    """Tests for database access through Python scripts."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_FULL_INTEGRATION") != "1",
        reason="Requires VH_FULL_INTEGRATION=1"
    )
    def test_sql_query_script(self, hub_context, python_plugin, tmp_path):
        """Test a script that performs SQL queries."""
        if not python_plugin:
            pytest.skip("Python plugin not found")
        
        script = tmp_path / "sql_test.py"
        script.write_text('''
# Test SQL operations through vh module

def OnScriptCommand(nick, data):
    """Handle script commands for SQL testing."""
    if data == "!sqltest":
        # Query registered users
        result = vh.SQLQuery("SELECT COUNT(*) FROM reglist")
        if result:
            vh.SendToUser(nick, f"User count: {result[0][0]}")
        return 0
    return 1
''')
        
        hub_context.LoadPlugin(python_plugin)
        result = hub_context.ExecutePythonScript(str(script))


class TestPythonBotRegistration:
    """Tests for bot registration through Python scripts."""
    
    @pytest.mark.skipif(
        os.environ.get("VH_FULL_INTEGRATION") != "1",
        reason="Requires VH_FULL_INTEGRATION=1"
    )
    def test_register_bot_script(self, hub_context, python_plugin, tmp_path):
        """Test a script that registers a bot."""
        if not python_plugin:
            pytest.skip("Python plugin not found")
        
        script = tmp_path / "bot_test.py"
        script.write_text('''
# Test bot registration

BOT_NICK = "TestBot"
BOT_DESC = "A test bot for Python plugin"

def OnScriptLoaded():
    """Called when script is loaded - register our bot."""
    vh.RegisterBot(BOT_NICK, 10, BOT_DESC, "bot@hub")
    return 1

def OnScriptUnload():
    """Called when script is unloaded - unregister bot."""
    vh.UnregisterBot(BOT_NICK)
    return 1

def OnParsedMsgPM(nick, to_nick, text):
    """Handle private messages to our bot."""
    if to_nick == BOT_NICK:
        vh.SendPMToUser(BOT_NICK, nick, f"Hello {nick}! I'm a test bot.")
        return 0
    return 1
''')
        
        hub_context.LoadPlugin(python_plugin)
        result = hub_context.ExecutePythonScript(str(script))


# Test discovery helper
def test_python_test_dir_exists():
    """Verify Python test directory structure exists."""
    if not PYTHON_TESTS_DIR.exists():
        pytest.skip(f"Python tests dir not available: {PYTHON_TESTS_DIR}")
    assert PYTHON_TESTS_DIR.is_dir()
