import json
import os
import sys
from typing import List, Optional
from pydantic import BaseModel, SecretStr
from pathlib import Path
from sqlalchemy import create_engine, String, Integer, select, delete, inspect, text, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, sessionmaker
from sqlalchemy.engine.url import make_url
from datetime import datetime, timezone
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

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
    default_format: str = "auto"

class User(BaseModel):
    id: Optional[int] = None
    username: str
    password: Optional[str] = None # Plain text only during creation/login
    role: str = "user" # "admin" or "user"

class ChatSession(BaseModel):
    id: str
    user_id: int
    host_id: Optional[str] = None
    created_at: datetime
    last_active: datetime

class ChatMessage(BaseModel):
    id: Optional[int] = None
    session_id: str
    role: str
    content: str
    name: Optional[str] = None
    created_at: datetime

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
    default_format: Mapped[str] = mapped_column(String(50), default="auto")

class UserRecord(Base):
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(1024))
    role: Mapped[str] = mapped_column(String(50), default="user")

class ChatSessionRecord(Base):
    __tablename__ = "chat_sessions"
    
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    host_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("hosts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    last_active: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class ChatMessageRecord(Base):
    __tablename__ = "chat_messages"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(50), ForeignKey("chat_sessions.id"))
    role: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(String(65535)) # Use a large string
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

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
        
        # Ensure default admin user exists
        self._ensure_default_admin()

    def _ensure_default_admin(self):
        """Ensure there is at least one admin user in the database."""
        with self.Session() as session:
            # Check if any admin exists
            admin_exists = session.query(UserRecord).filter(UserRecord.role == "admin").first() is not None
            if not admin_exists:
                # Check if 'admin' username is already taken by a non-admin
                existing_admin_user = session.query(UserRecord).filter(UserRecord.username == "admin").first()
                if existing_admin_user:
                    # Update existing user to admin
                    existing_admin_user.role = "admin"
                    # We don't reset password if it already exists, unless they want default admin:admin
                    # For safety, if it's already there, just make it admin.
                    print("Existing 'admin' user promoted to administrator role.", file=sys.stderr)
                else:
                    # Add default admin:admin
                    admin_user = UserRecord(
                        username="admin",
                        password_hash=pwd_context.hash("admin"),
                        role="admin"
                    )
                    session.add(admin_user)
                    print("Default admin user created (admin:admin)", file=sys.stderr)
                session.commit()

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
                            # For simple cases like adding a column, we can try to auto-fix
                            try:
                                with self.engine.begin() as conn:
                                    # Use a simple string representation for the type
                                    type_str = str(column.type.compile(self.engine.dialect))
                                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column.name} {type_str}"))
                                print(f"Successfully added missing column '{column.name}' to '{table_name}'", file=sys.stderr)
                            except Exception as e:
                                print(f"Failed to auto-fix discrepancy: {e}", file=sys.stderr)
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

    def update_ollama_instance_model(self, instance_id: int, model: str):
        with self.Session() as session:
            r = session.get(OllamaRecord, instance_id)
            if r:
                r.default_model = model
                session.commit()

    def update_ollama_instance_format(self, instance_id: int, format_name: str):
        with self.Session() as session:
            r = session.get(OllamaRecord, instance_id)
            if r:
                r.default_format = format_name
                session.commit()

    # User Management
    def get_user_by_username(self, username: str) -> Optional[User]:
        with self.Session() as session:
            stmt = select(UserRecord).where(UserRecord.username == username)
            r = session.execute(stmt).scalar_one_or_none()
            if r:
                return User(id=r.id, username=r.username, role=r.role)
            return None

    def verify_user(self, username: str, password: str) -> Optional[User]:
        with self.Session() as session:
            stmt = select(UserRecord).where(UserRecord.username == username)
            r = session.execute(stmt).scalar_one_or_none()
            if r and pwd_context.verify(password, r.password_hash):
                return User(id=r.id, username=r.username, role=r.role)
            return None

    def create_user(self, user: User) -> User:
        with self.Session() as session:
            record = UserRecord(
                username=user.username,
                password_hash=pwd_context.hash(user.password),
                role=user.role
            )
            session.add(record)
            session.commit()
            return User(id=record.id, username=record.username, role=record.role)

    def get_all_users(self) -> List[User]:
        with self.Session() as session:
            records = session.query(UserRecord).all()
            return [User(id=r.id, username=r.username, role=r.role) for r in records]

    # Session & Message Management
    def create_chat_session(self, session_id: str, user_id: int, host_id: Optional[str] = None) -> ChatSession:
        with self.Session() as session:
            now = datetime.now(timezone.utc)
            record = ChatSessionRecord(
                id=session_id,
                user_id=user_id,
                host_id=host_id,
                created_at=now,
                last_active=now
            )
            session.add(record)
            session.commit()
            return ChatSession(
                id=record.id,
                user_id=record.user_id,
                host_id=record.host_id,
                created_at=record.created_at,
                last_active=record.last_active
            )

    def get_chat_session(self, session_id: str) -> Optional[ChatSession]:
        with self.Session() as session:
            r = session.get(ChatSessionRecord, session_id)
            if r:
                return ChatSession(
                    id=r.id,
                    user_id=r.user_id,
                    host_id=r.host_id,
                    created_at=r.created_at,
                    last_active=r.last_active
                )
            return None

    def update_session_activity(self, session_id: str):
        with self.Session() as session:
            r = session.get(ChatSessionRecord, session_id)
            if r:
                r.last_active = datetime.now(timezone.utc)
                session.commit()

    def add_chat_message(self, message: ChatMessage):
        with self.Session() as session:
            record = ChatMessageRecord(
                session_id=message.session_id,
                role=message.role,
                content=message.content,
                name=message.name,
                created_at=message.created_at or datetime.now(timezone.utc)
            )
            session.add(record)
            # Also update session activity
            sess_record = session.get(ChatSessionRecord, message.session_id)
            if sess_record:
                sess_record.last_active = datetime.now(timezone.utc)
            session.commit()

    def get_chat_history(self, session_id: str) -> List[ChatMessage]:
        with self.Session() as session:
            stmt = select(ChatMessageRecord).where(ChatMessageRecord.session_id == session_id).order_by(ChatMessageRecord.created_at)
            records = session.execute(stmt).scalars().all()
            return [
                ChatMessage(
                    id=r.id,
                    session_id=r.session_id,
                    role=r.role,
                    content=r.content,
                    name=r.name,
                    created_at=r.created_at
                ) for r in records
            ]
