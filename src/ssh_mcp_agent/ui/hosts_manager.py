import json
import os
from typing import List, Dict, Any, Optional
from pydantic import BaseModel

class HostConfig(BaseModel):
    id: str
    name: str
    host: str
    username: str
    password: Optional[str] = None
    key_filename: Optional[str] = None
    port: int = 22

class HostsManager:
    def __init__(self, config_path: str = "hosts.json"):
        self.config_path = config_path
        self.hosts: Dict[str, HostConfig] = self._load_hosts()

    def _load_hosts(self) -> Dict[str, HostConfig]:
        if not os.path.exists(self.config_path):
            return {}
        try:
            with open(self.config_path, "r") as f:
                data = json.load(f)
                return {h["id"]: HostConfig(**h) for h in data}
        except Exception:
            return {}

    def _save_hosts(self):
        with open(self.config_path, "w") as f:
            json.dump([h.model_dump() for h in self.hosts.values()], f, indent=2)

    def get_all(self) -> List[HostConfig]:
        return list(self.hosts.values())

    def get_by_id(self, host_id: str) -> Optional[HostConfig]:
        return self.hosts.get(host_id)

    def add_host(self, host: HostConfig):
        self.hosts[host.id] = host
        self._save_hosts()

    def delete_host(self, host_id: str):
        if host_id in self.hosts:
            del self.hosts[host_id]
            self._save_hosts()
