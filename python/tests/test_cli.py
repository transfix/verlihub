"""
Tests for the verlihub-cli command-line tool.
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import tempfile

from verlihub.cli import (
    format_bytes,
    format_uptime,
    load_config,
    save_config,
    main,
)


class TestCLIUtilities:
    """Test CLI utility functions."""
    
    def test_format_bytes_zero(self):
        """Test formatting zero bytes."""
        assert format_bytes(0) == "0.0 B"
    
    def test_format_bytes_small(self):
        """Test formatting small byte values."""
        assert format_bytes(512) == "512.0 B"
        assert format_bytes(1023) == "1023.0 B"
    
    def test_format_bytes_kb(self):
        """Test formatting kilobytes."""
        assert format_bytes(1024) == "1.0 KB"
        assert format_bytes(2048) == "2.0 KB"
    
    def test_format_bytes_mb(self):
        """Test formatting megabytes."""
        assert format_bytes(1024 * 1024) == "1.0 MB"
        assert format_bytes(5 * 1024 * 1024) == "5.0 MB"
    
    def test_format_bytes_gb(self):
        """Test formatting gigabytes."""
        assert format_bytes(1024 * 1024 * 1024) == "1.0 GB"
        assert format_bytes(100 * 1024 * 1024 * 1024) == "100.0 GB"
    
    def test_format_bytes_tb(self):
        """Test formatting terabytes."""
        assert format_bytes(1024 * 1024 * 1024 * 1024) == "1.0 TB"
    
    def test_format_uptime_seconds(self):
        """Test formatting seconds."""
        assert format_uptime(30) == "30s"
        assert format_uptime(59) == "59s"
    
    def test_format_uptime_minutes(self):
        """Test formatting minutes."""
        assert format_uptime(60) == "1m"
        assert format_uptime(90) == "1m 30s"
        assert format_uptime(125) == "2m 5s"
    
    def test_format_uptime_hours(self):
        """Test formatting hours."""
        assert format_uptime(3600) == "1h"
        assert format_uptime(3665) == "1h 1m 5s"
    
    def test_format_uptime_days(self):
        """Test formatting days."""
        assert format_uptime(86400) == "1d"
        assert format_uptime(86400 + 7200) == "1d 2h"
        assert format_uptime(86400 * 7 + 3600) == "7d 1h"


class TestCLIConfig:
    """Test CLI configuration functions."""
    
    def test_load_config_empty(self, tmp_path):
        """Test loading config when file doesn't exist."""
        with patch('verlihub.cli.CONFIG_FILE', tmp_path / "nonexistent.json"):
            config = load_config()
            assert config == {}
    
    def test_save_and_load_config(self, tmp_path):
        """Test saving and loading config."""
        config_file = tmp_path / "config.json"
        with patch('verlihub.cli.CONFIG_FILE', config_file):
            save_config({"token": "test123", "api_url": "http://example.com"})
            
            loaded = load_config()
            assert loaded["token"] == "test123"
            assert loaded["api_url"] == "http://example.com"


class TestCLICommands:
    """Test CLI command functions."""
    
    def test_main_no_command(self):
        """Test main with no command shows help."""
        with patch('sys.argv', ['verlihub-cli']):
            result = main()
            assert result == 0
    
    def test_main_help(self):
        """Test main with --help."""
        with patch('sys.argv', ['verlihub-cli', '--help']):
            with pytest.raises(SystemExit) as exc_info:
                main()
            # argparse exits with 0 on --help
            assert exc_info.value.code == 0
    
    @patch('verlihub.cli.get_client')
    def test_status_command_success(self, mock_get_client):
        """Test status command with successful response."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "running": True,
            "hub_name": "TestHub",
            "user_count": 100,
            "share_total": 1024 * 1024 * 1024,
            "uptime": 3600,
        }
        
        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_get_client.return_value = mock_client
        
        with patch('sys.argv', ['verlihub-cli', 'status']):
            result = main()
            assert result == 0
    
    @patch('verlihub.cli.get_client')
    def test_status_command_auth_required(self, mock_get_client):
        """Test status command when auth is required."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        
        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_get_client.return_value = mock_client
        
        with patch('sys.argv', ['verlihub-cli', 'status']):
            result = main()
            assert result == 1
    
    @patch('verlihub.cli.get_client')
    def test_users_command_json_format(self, mock_get_client):
        """Test users command with JSON output format."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"nick": "User1", "class": 1, "share": 1024, "ip": "192.168.1.1"},
            {"nick": "User2", "class": 3, "share": 2048, "ip": "192.168.1.2"},
        ]
        
        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_get_client.return_value = mock_client
        
        with patch('sys.argv', ['verlihub-cli', '--format', 'json', 'users']):
            result = main()
            assert result == 0
    
    @patch('verlihub.cli.get_client')
    def test_kick_command(self, mock_get_client):
        """Test kick command."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_get_client.return_value = mock_client
        
        with patch('sys.argv', ['verlihub-cli', 'kick', 'baduser', '--reason', 'Spamming']):
            result = main()
            assert result == 0
    
    @patch('verlihub.cli.get_client')
    def test_broadcast_command(self, mock_get_client):
        """Test broadcast command."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True}
        
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_get_client.return_value = mock_client
        
        with patch('sys.argv', ['verlihub-cli', 'broadcast', 'Hello world!']):
            result = main()
            assert result == 0
    
    @patch('verlihub.cli.get_client')
    def test_command_execution(self, mock_get_client):
        """Test command execution."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "output": "Command executed",
            "message": "OK",
        }
        
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_get_client.return_value = mock_client
        
        with patch('sys.argv', ['verlihub-cli', 'command', '!help']):
            result = main()
            assert result == 0


class TestCLIConfigCommand:
    """Test CLI config command."""
    
    def test_config_show(self, tmp_path):
        """Test config show command."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"token": "saved_token"}))
        
        with patch('verlihub.cli.CONFIG_FILE', config_file):
            with patch('sys.argv', ['verlihub-cli', 'config', '--show']):
                result = main()
                assert result == 0
    
    def test_config_set_url(self, tmp_path):
        """Test config set-url command."""
        config_file = tmp_path / "config.json"
        
        with patch('verlihub.cli.CONFIG_FILE', config_file):
            with patch('sys.argv', ['verlihub-cli', 'config', '--set-url', 'http://newurl.com']):
                result = main()
                assert result == 0
                
                # Verify URL was saved
                loaded = load_config()
                assert loaded.get("api_url") == "http://newurl.com"
    
    def test_config_clear(self, tmp_path):
        """Test config clear command."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"token": "to_be_cleared"}))
        
        with patch('verlihub.cli.CONFIG_FILE', config_file):
            with patch('sys.argv', ['verlihub-cli', 'config', '--clear']):
                result = main()
                assert result == 0
                assert not config_file.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
