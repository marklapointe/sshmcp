import json
import os
import sys
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from pathlib import Path

class HostConfig(BaseModel):
    id: str
    name: str
    host: str
    username: str
    password: Optional[str] = None
    key_filename: Optional[str] = None
    port: int = 22

class HostsManager:
    def __init__(self, config_path: Optional[str] = None):
        self.config_paths = self._get_config_paths(config_path)
        self.hosts: Dict[str, HostConfig] = self._load_hosts()

    def _get_config_paths(self, override_path: Optional[str] = None) -> List[Path]:
        paths = []
        if override_path:
            paths.append(Path(override_path))
        
        # Local path
        paths.append(Path("hosts.json"))
        
        # User home path
        paths.append(Path.home() / ".ssh-mcp" / "hosts.json")
        
        # OS-specific /etc/ paths
        if sys.platform == "linux":
            paths.append(Path("/etc/ssh-mcp/hosts.json"))
        elif sys.platform == "freebsd":
            paths.append(Path("/usr/local/etc/ssh-mcp/hosts.json"))
            paths.append(Path("/etc/ssh-mcp/hosts.json"))
        elif sys.platform == "darwin": # macOS
            paths.append(Path("/etc/ssh-mcp/hosts.json"))
            paths.append(Path("/Library/Application Support/ssh-mcp/hosts.json"))
            
        return paths

    def _load_hosts(self) -> Dict[str, HostConfig]:
        all_hosts = {}
        # Load from all paths, later paths override earlier ones (or we could stop at first found)
        # Actually, let's load from all and merge, or just pick the first one that exists for simplicity?
        # The user says "look in the appropriate /etc/ dirs", implying it could be in one of them.
        # Let's search them in order of priority (Local > Home > System) and load from the first one that exists?
        # Or merge them? Merging might be better if user has global and local configs.
        # But let's keep it simple: find the first existing config and use it.
        # Actually, the requirement "login into any host, but I need to configure the hosts with information" 
        # suggests a centralized management.
        
        for path in reversed(self.config_paths): # System -> Home -> Local (so Local wins)
            if path.exists():
                try:
                    with open(path, "r") as f:
                        data = json.load(f)
                        for h in data:
                            config = HostConfig(**h)
                            all_hosts[config.id] = config
                except Exception as e:
                    # Log error but continue
                    print(f"Error loading config from {path}: {e}", file=sys.stderr)
        return all_hosts

    def get_all(self) -> List[HostConfig]:
        return list(self.hosts.values())

    def get_by_id(self, host_id: str) -> Optional[HostConfig]:
        return self.hosts.get(host_id)

    def get_by_name_or_host(self, identifier: str) -> Optional[HostConfig]:
        """Find host by ID, name, or hostname."""
        # Exact match ID
        if identifier in self.hosts:
            return self.hosts[identifier]
        
        # Match name or host
        for host in self.hosts.values():
            if host.name == identifier or host.host == identifier:
                return host
        return None

    def has_host_info(self, identifier: str) -> bool:
        """Check if we have host and user information for the identifier."""
        host = self.get_by_name_or_host(identifier)
        return host is not None and host.username is not None

    def add_host(self, host: HostConfig, save_path: Optional[Path] = None):
        self.hosts[host.id] = host
        # Default save to the first writable path or the local one
        if not save_path:
            save_path = Path("hosts.json")
        
        # Ensure directory exists
        save_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(save_path, "w") as f:
            json.dump([h.model_dump() for h in self.hosts.values()], f, indent=2)

    def delete_host(self, host_id: str, save_path: Optional[Path] = None):
        if host_id in self.hosts:
            del self.hosts[host_id]
            if not save_path:
                save_path = Path("hosts.json")
            with open(save_path, "w") as f:
                json.dump([h.model_dump() for h in self.hosts.values()], f, indent=2)
