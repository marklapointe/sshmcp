import pytest
import os
import json
from pathlib import Path
from ssh_mcp_agent.hosts import HostsManager, HostConfig
from ssh_mcp_agent.server import get_ssh_config, SSHExecuteArgs

def test_host_manager_paths(tmp_path):
    # Create a dummy hosts.json in tmp_path
    host_data = [{
        "id": "test-host",
        "name": "Test Host",
        "host": "1.2.3.4",
        "username": "testuser",
        "password": "testpassword",
        "port": 2222
    }]
    config_file = tmp_path / "hosts.json"
    with open(config_file, "w") as f:
        json.dump(host_data, f)
    
    # Initialize HostsManager with override path
    hm = HostsManager(config_path=str(config_file))
    
    assert hm.has_host_info("test-host")
    assert hm.has_host_info("Test Host")
    assert hm.has_host_info("1.2.3.4")
    
    host = hm.get_by_name_or_host("Test Host")
    assert host.username == "testuser"
    assert host.port == 2222

def test_get_ssh_config_lookup(tmp_path, monkeypatch):
    host_data = [{
        "id": "prod-server",
        "name": "Production",
        "host": "prod.example.com",
        "username": "admin",
        "key_filename": "/path/to/key"
    }]
    config_file = tmp_path / "hosts.json"
    with open(config_file, "w") as f:
        json.dump(host_data, f)
    
    # Mock HostsManager to use our temp file
    from ssh_mcp_agent import server
    monkeypatch.setattr(server, "hosts_manager", HostsManager(config_path=str(config_file)))
    
    config = get_ssh_config("Production")
    assert config.host == "prod.example.com"
    assert config.username == "admin"
    assert config.key_filename == "/path/to/key"
    
    with pytest.raises(ValueError, match="No configuration found"):
        get_ssh_config("unknown-host")

def test_ssh_args_no_credentials():
    # Verify that SSHExecuteArgs doesn't have credential fields anymore
    schema = SSHExecuteArgs.model_json_schema()
    properties = schema.get("properties", {})
    
    assert "host" in properties
    assert "command" in properties
    assert "username" not in properties
    assert "password" not in properties
    assert "key_filename" not in properties
    assert "port" not in properties

def test_os_specific_paths(monkeypatch):
    # Test Linux paths
    monkeypatch.setattr("sys.platform", "linux")
    hm = HostsManager()
    paths = [str(p) for p in hm.config_paths]
    assert "/etc/ssh-mcp/hosts.json" in paths
    
    # Test FreeBSD paths
    monkeypatch.setattr("sys.platform", "freebsd")
    hm = HostsManager()
    paths = [str(p) for p in hm.config_paths]
    assert "/usr/local/etc/ssh-mcp/hosts.json" in paths
    assert "/etc/ssh-mcp/hosts.json" in paths
    
    # Test macOS paths
    monkeypatch.setattr("sys.platform", "darwin")
    hm = HostsManager()
    paths = [str(p) for p in hm.config_paths]
    assert "/etc/ssh-mcp/hosts.json" in paths
    assert "/Library/Application Support/ssh-mcp/hosts.json" in paths
