import json
import os
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, SecretStr

class Settings(BaseModel):
    ssh_password: Optional[SecretStr] = None
    database_url: str = "sqlite:///data/hosts.db"

class ConfigManager:
    def __init__(self, config_dir: Optional[str] = None):
        self.config_dir = config_dir
        self.settings = self._load_settings()

    def get_config_paths(self, filename: str) -> List[Path]:
        paths = []
        if self.config_dir:
            p = Path(self.config_dir)
            if p.is_dir():
                paths.append(p / filename)
            else:
                # If it's a file path, we use it directly but this might be confusing if filename is passed
                # For now assume config_dir is actually config_path if it's a file
                paths.append(p)

        # 0. Data directory in PWD (High priority local config)
        paths.append(Path("data") / filename)

        # 1. User local etc (Highest priority after CLI)
        paths.append(Path.home() / ".local" / "etc" / "cloudbsd" / "sshagent" / filename)
        paths.append(Path.home() / ".local" / "etc" / "ssh-mcp" / filename)

        # 2. System-wide configs
        if sys.platform.startswith("linux"):
            paths.append(Path("/etc/cloudbsd/sshagent") / filename)
            paths.append(Path("/etc/ssh-mcp") / filename)
        elif sys.platform.startswith("freebsd"):
            paths.append(Path("/usr/local/etc/cloudbsd/sshagent") / filename)
            paths.append(Path("/usr/local/etc/ssh-mcp") / filename)
            paths.append(Path("/etc/cloudbsd/sshagent") / filename)
            paths.append(Path("/etc/ssh-mcp") / filename)
        elif sys.platform.startswith("darwin"):
            paths.append(Path("/etc/cloudbsd/sshagent") / filename)
            paths.append(Path("/etc/ssh-mcp") / filename)
            paths.append(Path("/Library/Application Support/cloudbsd/sshagent") / filename)
            paths.append(Path("/Library/Application Support/ssh-mcp") / filename)

        # 3. Fallbacks
        paths.append(Path.home() / ".ssh-mcp" / filename)
        paths.append(Path(filename))

        return paths

    def _load_settings(self) -> Settings:
        paths = self.get_config_paths("sshagent.conf")
        legacy_paths = self.get_config_paths("config.json")
        
        # Load in reverse priority so higher priority overrides
        loaded_data = {}
        
        # Combine paths: config.json (lower priority) and sshagent.conf (higher priority)
        # We process in reverse priority, so lower priority comes first.
        # Priority order (highest to lowest): paths[0], paths[1], ..., legacy_paths[0], legacy_paths[1]
        # Reverse order for processing: legacy_paths[-1], ..., legacy_paths[0], paths[-1], ..., paths[0]
        
        all_paths = []
        for p in reversed(legacy_paths):
            all_paths.append(p)
        for p in reversed(paths):
            all_paths.append(p)

        for path in all_paths:
            if path.exists():
                try:
                    with open(path, "r") as f:
                        data = json.load(f)
                        loaded_data.update(data)
                except Exception as e:
                    print(f"Warning: Failed to load config from {path}: {e}", file=sys.stderr)
        
        return Settings(**loaded_data)

    def save_settings(self, settings: Settings):
        self.settings = settings
        # Save to the first available writable path (usually the user-local one)
        paths = self.get_config_paths("sshagent.conf")
        
        # Try to save to the first path in the list that we can write to
        for path in paths:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "w") as f:
                    # Exclude password from save if we want to be safe, 
                    # but the requirement didn't explicitly say not to store this one.
                    # However, earlier issues said "don't store secrets in clear text".
                    # Let's exclude password from sshagent.conf too.
                    json.dump(settings.model_dump(exclude={"ssh_password"}), f, indent=2)
                return
            except Exception:
                continue
        
        # Fallback to current directory if nothing else works
        with open("sshagent.conf", "w") as f:
            json.dump(settings.model_dump(exclude={"ssh_password"}), f, indent=2)
