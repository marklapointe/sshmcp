import json
import os
import sys
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, SecretStr
from pathlib import Path
from sqlalchemy import create_engine, String, Integer, select, delete, inspect, text, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, sessionmaker
from sqlalchemy.engine.url import make_url

class HostConfig(BaseModel):
    id: str
    name: str
    host: str
    username: str
    password: Optional[SecretStr] = None
    key_filename: Optional[str] = None
    port: int = 22

class OllamaInstance(BaseModel):
    id: Optional[int] = None
    name: str
    host: str
    is_default: bool = False
    default_model: str = "llama3.2"

class Base(DeclarativeBase):
    pass

class HostRecord(Base):
    __tablename__ = "hosts"
    
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    host: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255))
    key_filename: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    port: Mapped[int] = mapped_column(Integer, default=22)

class OllamaRecord(Base):
    __tablename__ = "ollama_instances"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    host: Mapped[str] = mapped_column(String(255))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    default_model: Mapped[str] = mapped_column(String(255), default="llama3.2")

class HostsManager:
    def __init__(self, database_url: str, config_dir: Optional[str] = None):
        self._ensure_database_exists(database_url)
        self.engine = create_engine(database_url)
        self._check_schema_discrepancy()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        
        # Check if we need to migrate from JSON
        self._migrate_from_json(config_dir)
        
        # Ensure at least one Ollama instance exists
        self._ensure_default_ollama_instance()

    def _ensure_database_exists(self, database_url: str):
        """Ensure the database exists, creating it if possible for supported backends."""
        url = make_url(database_url)
        if url.drivername.startswith("sqlite"):
            # SQLite automatically creates the file, but we need to ensure the directory exists
            if url.database:
                db_path = Path(url.database)
                if not db_path.parent.exists():
                    db_path.parent.mkdir(parents=True, exist_ok=True)
            return
            
        if url.drivername.startswith("mysql") or url.drivername.startswith("mariadb"):
            db_name = url.database
            if not db_name:
                return
            
            # Connect without database name to create it
            root_url = url.set(database=None)
            temp_engine = create_engine(root_url)
            try:
                with temp_engine.connect() as conn:
                    # Use backticks for database name to handle reserved words/special chars
                    conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{db_name}`"))
                    conn.commit()
            except Exception as e:
                # We don't fail here as the user might not have CREATE DATABASE permissions
                # but the database might already exist.
                print(f"Note: Attempt to ensure database '{db_name}' exists: {e}", file=sys.stderr)
            finally:
                temp_engine.dispose()

    def _check_schema_discrepancy(self):
        """Check for discrepancies between the defined model and the actual database schema."""
        try:
            inspector = inspect(self.engine)
            existing_tables = inspector.get_table_names()
            
            for table_name, table in Base.metadata.tables.items():
                if table_name in existing_tables:
                    existing_columns = {c['name']: c for c in inspector.get_columns(table_name)}
                    for column in table.columns:
                        if column.name not in existing_columns:
                            print(f"DATABASE DISCREPANCY: Table '{table_name}' is missing column '{column.name}'", file=sys.stderr)
                        else:
                            # Basic type check could be added here if needed
                            pass
                elif existing_tables:
                    # Database exists and has some tables, but our table is missing
                    print(f"DATABASE DISCREPANCY: Table '{table_name}' is missing from the database", file=sys.stderr)
        except Exception as e:
            # If we can't even inspect (e.g. first run, DB doesn't exist yet), we skip reporting
            pass

    def _get_config_paths(self, override_path: Optional[str] = None) -> List[Path]:
        filename = "hosts.json"
        paths = []
        if override_path:
            p = Path(override_path)
            if p.is_dir():
                paths.append(p / filename)
            else:
                paths.append(p)
        
        # 0. Data directory in PWD
        paths.append(Path("data") / filename)
        
        # 1. User local etc (Highest priority after CLI)
        paths.append(Path.home() / ".local" / "etc" / "cloudbsd" / "sshagent" / filename)
        paths.append(Path.home() / ".local" / "etc" / "ssh-mcp" / filename)
        
        # 2. System configs
        if sys.platform.startswith("linux"):
            paths.append(Path("/etc/cloudbsd/sshagent") / filename)
            paths.append(Path("/etc/ssh-mcp") / filename)
        elif sys.platform.startswith("freebsd"):
            paths.append(Path("/usr/local/etc/cloudbsd/sshagent") / filename)
            paths.append(Path("/usr/local/etc/ssh-mcp") / filename)
            paths.append(Path("/etc/cloudbsd/sshagent") / filename)
            paths.append(Path("/etc/ssh-mcp") / filename)
        elif sys.platform.startswith("darwin"): # macOS
            paths.append(Path("/etc/cloudbsd/sshagent") / filename)
            paths.append(Path("/etc/ssh-mcp") / filename)
            paths.append(Path("/Library/Application Support/cloudbsd/sshagent") / filename)
            paths.append(Path("/Library/Application Support/ssh-mcp") / filename)
            
        # 3. Fallbacks
        paths.append(Path.home() / ".ssh-mcp" / filename)
        paths.append(Path(filename))
            
        return paths

    def _migrate_from_json(self, config_dir: Optional[str]):
        """Migrate hosts from legacy JSON format to the database if the database is empty."""
        try:
            # Only migrate if database is empty
            with self.Session() as session:
                count = session.query(HostRecord).count()
                if count > 0:
                    return
        except Exception:
            # If we can't count (e.g. schema discrepancy), skip migration to avoid crashing
            return

        config_paths = self._get_config_paths(config_dir)
        migrated_any = False
        
        # Load from all paths, later paths override earlier ones in priority
        # (reverse search to maintain same priority as before)
        all_json_hosts = {}
        for path in reversed(config_paths):
            if path.exists():
                try:
                    with open(path, "r") as f:
                        data = json.load(f)
                        for h in data:
                            # Use dict to allow overriding by higher priority config
                            all_json_hosts[h["id"]] = h
                except Exception as e:
                    print(f"Error reading {path} during migration: {e}", file=sys.stderr)

        if all_json_hosts:
            with self.Session() as session:
                for h_data in all_json_hosts.values():
                    # Map JSON fields to DB record
                    # Ensure we don't try to save password even if it was there (it shouldn't be)
                    record = HostRecord(
                        id=h_data["id"],
                        name=h_data["name"],
                        host=h_data["host"],
                        username=h_data["username"],
                        key_filename=h_data.get("key_filename"),
                        port=h_data.get("port", 22)
                    )
                    session.merge(record)
                session.commit()
                migrated_any = True
        
        if migrated_any:
            print("Successfully migrated hosts from JSON to database.", file=sys.stderr)

    def get_all(self) -> List[HostConfig]:
        with self.Session() as session:
            records = session.query(HostRecord).all()
            return [
                HostConfig(
                    id=r.id,
                    name=r.name,
                    host=r.host,
                    username=r.username,
                    key_filename=r.key_filename,
                    port=r.port
                ) for r in records
            ]

    def get_by_id(self, host_id: str) -> Optional[HostConfig]:
        with self.Session() as session:
            r = session.get(HostRecord, host_id)
            if r:
                return HostConfig(
                    id=r.id,
                    name=r.name,
                    host=r.host,
                    username=r.username,
                    key_filename=r.key_filename,
                    port=r.port
                )
            return None

    def get_by_name_or_host(self, identifier: str) -> Optional[HostConfig]:
        """Find host by ID, name, or hostname."""
        with self.Session() as session:
            # Exact match ID
            r = session.get(HostRecord, identifier)
            if not r:
                # Match name or host
                stmt = select(HostRecord).where(
                    (HostRecord.name == identifier) | (HostRecord.host == identifier)
                )
                r = session.execute(stmt).scalar_one_or_none()
            
            if r:
                return HostConfig(
                    id=r.id,
                    name=r.name,
                    host=r.host,
                    username=r.username,
                    key_filename=r.key_filename,
                    port=r.port
                )
            return None

    def has_host_info(self, identifier: str) -> bool:
        """Check if we have host and user information for the identifier."""
        host = self.get_by_name_or_host(identifier)
        return host is not None and host.username is not None

    def add_host(self, host: HostConfig):
        with self.Session() as session:
            record = HostRecord(
                id=host.id,
                name=host.name,
                host=host.host,
                username=host.username,
                key_filename=host.key_filename,
                port=host.port
            )
            session.merge(record)
            session.commit()

    def delete_host(self, host_id: str):
        with self.Session() as session:
            stmt = delete(HostRecord).where(HostRecord.id == host_id)
            session.execute(stmt)
            session.commit()

    def _ensure_default_ollama_instance(self):
        """Ensure there is at least one Ollama instance in the database."""
        with self.Session() as session:
            count = session.query(OllamaRecord).count()
            if count == 0:
                # Add a default local instance
                default_instance = OllamaRecord(
                    name="Local Ollama",
                    host="http://localhost:11434",
                    is_default=True,
                    default_model="llama3.2"
                )
                session.add(default_instance)
                session.commit()

    def get_ollama_instances(self) -> List[OllamaInstance]:
        with self.Session() as session:
            records = session.query(OllamaRecord).all()
            return [
                OllamaInstance(
                    id=r.id,
                    name=r.name,
                    host=r.host,
                    is_default=r.is_default,
                    default_model=r.default_model
                ) for r in records
            ]

    def get_ollama_instance_by_id(self, instance_id: int) -> Optional[OllamaInstance]:
        with self.Session() as session:
            r = session.get(OllamaRecord, instance_id)
            if r:
                return OllamaInstance(
                    id=r.id,
                    name=r.name,
                    host=r.host,
                    is_default=r.is_default,
                    default_model=r.default_model
                )
            return None

    def get_default_ollama_instance(self) -> Optional[OllamaInstance]:
        with self.Session() as session:
            stmt = select(OllamaRecord).where(OllamaRecord.is_default == True)
            r = session.execute(stmt).scalar_one_or_none()
            if not r:
                # Fallback to first one if no default marked
                r = session.query(OllamaRecord).first()
            
            if r:
                return OllamaInstance(
                    id=r.id,
                    name=r.name,
                    host=r.host,
                    is_default=r.is_default,
                    default_model=r.default_model
                )
            return None

    def add_ollama_instance(self, instance: OllamaInstance):
        with self.Session() as session:
            if instance.is_default:
                # Clear existing default
                session.execute(text("UPDATE ollama_instances SET is_default = 0"))
            
            record = OllamaRecord(
                name=instance.name,
                host=instance.host,
                is_default=instance.is_default,
                default_model=instance.default_model
            )
            if instance.id:
                record.id = instance.id
                session.merge(record)
            else:
                session.add(record)
            session.commit()

    def delete_ollama_instance(self, instance_id: int):
        with self.Session() as session:
            stmt = delete(OllamaRecord).where(OllamaRecord.id == instance_id)
            session.execute(stmt)
            session.commit()

    def set_default_ollama_instance(self, instance_id: int):
        with self.Session() as session:
            session.execute(text("UPDATE ollama_instances SET is_default = 0"))
            session.execute(text(f"UPDATE ollama_instances SET is_default = 1 WHERE id = {instance_id}"))
            session.commit()
