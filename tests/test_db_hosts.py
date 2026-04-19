import pytest
import os
from pathlib import Path
from unittest.mock import patch
from ssh_mcp_agent.hosts import HostsManager, HostConfig

def test_hosts_manager_sqlite(tmp_path):
    db_file = tmp_path / "test_hosts.db"
    db_url = f"sqlite:///{db_file}"
    # Mock _get_config_paths to return empty list so we don't migrate real hosts
    with patch("ssh_mcp_agent.hosts.HostsManager._get_config_paths", return_value=[]):
        manager = HostsManager(db_url)
    
    # Add a host
    host = HostConfig(
        id="h1",
        name="test-host",
        host="127.0.0.1",
        username="user1",
        port=22
    )
    manager.add_host(host)
    
    # Retrieve host
    retrieved = manager.get_by_id("h1")
    assert retrieved.name == "test-host"
    assert retrieved.username == "user1"
    
    # Retrieve by name
    retrieved_by_name = manager.get_by_name_or_host("test-host")
    assert retrieved_by_name.id == "h1"
    
    # Retrieve by host
    retrieved_by_host = manager.get_by_name_or_host("127.0.0.1")
    assert retrieved_by_host.id == "h1"
    
    # Get all
    all_hosts = manager.get_all()
    assert len(all_hosts) == 1
    
    # Delete host
    manager.delete_host("h1")
    assert manager.get_by_id("h1") is None
    assert len(manager.get_all()) == 0

def test_migration_from_json(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    hosts_json = config_dir / "hosts.json"
    
    hosts_data = [
        {
            "id": "j1",
            "name": "json-host",
            "host": "192.168.1.1",
            "username": "admin",
            "port": 2222
        }
    ]
    import json
    with open(hosts_json, "w") as f:
        json.dump(hosts_data, f)
        
    db_file = tmp_path / "migrated.db"
    db_url = f"sqlite:///{db_file}"
    
    # Initialize manager with the config dir to trigger migration
    # Mock _get_config_paths to return only our test path
    with patch("ssh_mcp_agent.hosts.HostsManager._get_config_paths", return_value=[hosts_json]):
        manager = HostsManager(db_url, config_dir=str(config_dir))
    
    # Check if host was migrated
    host = manager.get_by_id("j1")
    assert host is not None
    assert host.name == "json-host"
    assert host.username == "admin"
    assert host.port == 2222
    
    # Verify we can still add new ones
    new_host = HostConfig(
        id="h2",
        name="new-host",
        host="10.0.0.1",
        username="user2"
    )
    manager.add_host(new_host)
    assert len(manager.get_all()) == 2
