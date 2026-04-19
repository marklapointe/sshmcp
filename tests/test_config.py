import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from ssh_mcp_agent.config import ConfigManager, Settings

def test_config_priority(tmp_path):
    # Create different config files
    local_etc = tmp_path / ".local" / "etc" / "ssh-mcp"
    local_etc.mkdir(parents=True)
    with open(local_etc / "sshagent.conf", "w") as f:
        json.dump({"database_url": "local-etc-db"}, f)
        
    system_etc = tmp_path / "etc" / "ssh-mcp"
    system_etc.mkdir(parents=True)
    with open(system_etc / "sshagent.conf", "w") as f:
        json.dump({"database_url": "system-etc-db", "ssh_password": "system-pass"}, f)

    # Mock home and system paths
    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("sys.platform", "linux"):
            manager = ConfigManager()
            with patch.object(manager, "get_config_paths") as mock_get_paths:
                mock_get_paths.return_value = [
                    local_etc / "sshagent.conf",
                    system_etc / "sshagent.conf"
                ]
                settings = manager._load_settings()
                # local_etc should override system_etc
                assert settings.database_url == "local-etc-db"
                # ssh_password should be picked up from system_etc if not in local_etc
                assert settings.ssh_password.get_secret_value() == "system-pass"

def test_config_save(tmp_path):
    config_file = tmp_path / "sshagent.conf"
    manager = ConfigManager(config_dir=str(tmp_path))
    
    with patch.object(manager, "get_config_paths", return_value=[config_file]):
        new_settings = Settings(database_url="new-db")
        manager.save_settings(new_settings)
        
        assert config_file.exists()
        with open(config_file, "r") as f:
            data = json.load(f)
            assert data["database_url"] == "new-db"
            # Password should be excluded
            assert "ssh_password" not in data

def test_cli_override(tmp_path):
    cli_config = tmp_path / "cli-sshagent.conf"
    with open(cli_config, "w") as f:
        json.dump({"database_url": "cli-db"}, f)
    
    manager = ConfigManager(config_dir=str(cli_config))
    assert manager.settings.database_url == "cli-db"


def test_cloudbsd_priority(tmp_path):
    with patch("pathlib.Path.home", return_value=tmp_path):
        manager = ConfigManager()
        paths = manager.get_config_paths("sshagent.conf")
        
        # Check that cloudbsd path comes before ssh-mcp path
        cloudbsd_user_path = tmp_path / ".local" / "etc" / "cloudbsd" / "sshagent" / "sshagent.conf"
        ssh_mcp_user_path = tmp_path / ".local" / "etc" / "ssh-mcp" / "sshagent.conf"
        
        assert cloudbsd_user_path in paths
        assert ssh_mcp_user_path in paths
        assert paths.index(cloudbsd_user_path) < paths.index(ssh_mcp_user_path)

        with patch("sys.platform", "linux"):
            paths = manager.get_config_paths("sshagent.conf")
            assert Path("/etc/cloudbsd/sshagent/sshagent.conf") in paths
            assert Path("/etc/ssh-mcp/sshagent.conf") in paths
            assert paths.index(Path("/etc/cloudbsd/sshagent/sshagent.conf")) < paths.index(Path("/etc/ssh-mcp/sshagent.conf"))
