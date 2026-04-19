import asyncio
import json
import os
import sys
import logging
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Depends, status, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel
from contextlib import asynccontextmanager

from ..agent import SSHMCPAgent
from ..llm.client import ToolCallingFormat, OllamaClient
from ..hosts import HostsManager, HostConfig, OllamaInstance, User, ChatMessage
from ..config import ConfigManager

# Security constants
SECRET_KEY = os.getenv("SSH_MCP_SECRET_KEY", "super-secret-key-change-it")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 # 1 day

logger = logging.getLogger(__name__)

class AgentState:
    def __init__(self):
        self.agent: Optional[SSHMCPAgent] = None
        self.websockets: List[WebSocket] = []
        self.current_host_id: Optional[str] = None
        self.current_ollama_id: Optional[int] = None
        self.current_task: Optional[asyncio.Task] = None
        self.current_session_id: Optional[str] = None
        # Session Vault: host_id -> {"password": str, "last_used": float}
        self.session_vault: Dict[str, Dict[str, Any]] = {}

state = AgentState()

# We use an environment variable to pass the config path from main() to the FastAPI app initialization
config_path = os.getenv("SSH_MCP_CONFIG")
config_manager = ConfigManager(config_path)
hosts_manager = HostsManager(config_manager.settings.database_url, config_path)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start cleanup task for session vault
    cleanup_task = asyncio.create_task(cleanup_session_vault())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

async def cleanup_session_vault():
    """Periodically remove expired credentials from the session vault."""
    while True:
        try:
            await asyncio.sleep(60) # Check every minute
            now = time.time()
            # 1 hour timeout = 3600 seconds
            expired = [
                host_id for host_id, data in state.session_vault.items()
                if now - data.get("last_used", 0) > 3600
            ]
            for host_id in expired:
                logger.info(f"Expiring session credentials for host: {host_id}")
                del state.session_vault[host_id]
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in cleanup_session_vault: {e}")
            await asyncio.sleep(10)

app = FastAPI(lifespan=lifespan)

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)

class ChatRequest(BaseModel):
    query: str
    model: Optional[str] = None
    format: str = "auto"
    host_id: Optional[str] = None
    ollama_id: Optional[int] = None

class CredentialRequest(BaseModel):
    host_id: str
    password: str

class SettingsUpdateRequest(BaseModel):
    # Currently mostly empty as Ollama settings moved to DB
    # We could add other global settings here later
    pass

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=15)
    to_encode.update({"exp": int(expire.timestamp())})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = hosts_manager.get_user_by_username(username)
    if user is None:
        raise credentials_exception
    return user

async def get_admin_user(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operation restricted to administrators"
        )
    return current_user

async def broadcast(data: Dict[str, Any]):
    """Send data to all connected WebSockets."""
    if not state.websockets:
        return
    
    # Pre-serialize to avoid repeated work
    message = json.dumps(data)
    
    # We use a copy of the list because we might remove items while iterating
    active_ws = list(state.websockets)
    for ws in active_ws:
        try:
            await ws.send_text(message)
        except Exception:
            if ws in state.websockets:
                state.websockets.remove(ws)

async def log_callback(data: Dict[str, Any]):
    # Add UTC timestamp if not present
    if "timestamp" not in data:
        data["timestamp"] = datetime.now(timezone.utc).isoformat()
        
    await broadcast(data)
    
    # If it's a message from assistant or user, save it to DB
    if state.current_session_id:
        # Use provided timestamp if available
        ts = datetime.fromisoformat(data["timestamp"]) if "timestamp" in data else datetime.now(timezone.utc)
        
        if data.get("type") == "assistant_message":
            hosts_manager.add_chat_message(ChatMessage(
                session_id=state.current_session_id,
                role="assistant",
                content=data["content"],
                created_at=ts
            ))
        elif data.get("type") == "tool_result":
            hosts_manager.add_chat_message(ChatMessage(
                session_id=state.current_session_id,
                role="tool",
                content=data["content"],
                name=data["name"],
                created_at=ts
            ))
        elif data.get("type") == "user_message": # If agent emits user messages
            hosts_manager.add_chat_message(ChatMessage(
                session_id=state.current_session_id,
                role="user",
                content=data["content"],
                created_at=ts
            ))

    # Heartbeat for session vault: update last_used for current host if we are doing something
    if state.current_host_id and state.current_host_id in state.session_vault:
        state.session_vault[state.current_host_id]["last_used"] = time.time()

@app.post("/token")
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = hosts_manager.verify_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer", "role": user.role}

@app.get("/me")
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

@app.get("/hosts")
async def get_hosts(current_user: User = Depends(get_current_user)):
    return hosts_manager.get_all()

@app.post("/hosts")
async def add_host(host: HostConfig, admin_user: User = Depends(get_admin_user)):
    logger.info(f"Registering host: {host.name} ({host.host}) with ID {host.id}")
    # If password is provided, put it in session vault and it will be excluded from disk
    if host.password:
        state.session_vault[host.id] = {
            "password": host.password.get_secret_value(),
            "last_used": time.time()
        }
    hosts_manager.add_host(host)
    return {"status": "ok"}

@app.delete("/hosts/{host_id}")
async def delete_host(host_id: str, admin_user: User = Depends(get_admin_user)):
    hosts_manager.delete_host(host_id)
    if host_id in state.session_vault:
        del state.session_vault[host_id]
    return {"status": "ok"}

@app.get("/ollama")
async def get_ollama_instances(current_user: User = Depends(get_current_user)):
    return hosts_manager.get_ollama_instances()

@app.post("/ollama")
async def add_ollama_instance(instance: OllamaInstance, admin_user: User = Depends(get_admin_user)):
    hosts_manager.add_ollama_instance(instance)
    return {"status": "ok"}

@app.delete("/ollama/{instance_id}")
async def delete_ollama_instance(instance_id: int, admin_user: User = Depends(get_admin_user)):
    hosts_manager.delete_ollama_instance(instance_id)
    return {"status": "ok"}

@app.post("/ollama/{instance_id}/default")
async def set_default_ollama(instance_id: int, admin_user: User = Depends(get_admin_user)):
    hosts_manager.set_default_ollama_instance(instance_id)
    return {"status": "ok"}

@app.post("/ollama/{instance_id}/model")
async def set_ollama_model(instance_id: int, model: str, admin_user: User = Depends(get_admin_user)):
    """Update the default model for a specific Ollama instance."""
    hosts_manager.update_ollama_instance_model(instance_id, model)
    return {"status": "ok"}

@app.post("/ollama/{instance_id}/format")
async def set_ollama_format(instance_id: int, format: str, admin_user: User = Depends(get_admin_user)):
    """Update the default tool-calling format for a specific Ollama instance."""
    hosts_manager.update_ollama_instance_format(instance_id, format)
    return {"status": "ok"}

@app.get("/models")
async def get_models(ollama_id: Optional[int] = None, current_user: User = Depends(get_current_user)):
    """List available models from an Ollama instance."""
    try:
        if ollama_id:
            instance = hosts_manager.get_ollama_instance_by_id(ollama_id)
        else:
            instance = hosts_manager.get_default_ollama_instance()
            
        if not instance:
            return {"models": [], "error": "No Ollama instance found"}
            
        client = OllamaClient(host=instance.host)
        models = await client.list_models()
        return {"models": models}
    except Exception as e:
        logger.error(f"Failed to fetch models: {e}")
        return {"models": [], "error": str(e)}

@app.get("/settings")
async def get_settings(current_user: User = Depends(get_current_user)):
    """Get current application settings."""
    return config_manager.settings.model_dump(exclude={"ssh_password"})

@app.post("/settings")
async def update_settings(request: SettingsUpdateRequest, admin_user: User = Depends(get_admin_user)):
    """Update and persist application settings."""
    current = config_manager.settings
    # Currently no settings in SettingsUpdateRequest, but we keep the endpoint
    config_manager.save_settings(current)
    return {"status": "ok", "settings": current.model_dump(exclude={"ssh_password"})}

@app.post("/session/credentials")
async def set_credentials(request: CredentialRequest, current_user: User = Depends(get_current_user)):
    """Securely store credentials in the in-memory session vault."""
    state.session_vault[request.host_id] = {
        "password": request.password,
        "last_used": time.time()
    }
    # Update existing agent's environment if it matches the host
    if state.agent and state.current_host_id == request.host_id:
        state.agent.update_env({"SSH_PASSWORD": request.password})
    return {"status": "ok"}

@app.post("/chat")
async def chat(request: ChatRequest, current_user: User = Depends(get_current_user)):
    env_overrides = {}
    
    # We use a single session ID for now per user, or from request if we wanted multi-session
    # The user said "All session information should be saved in the database".
    # Let's use a fixed session ID "default-session" for now or based on user.
    session_id = f"session-{current_user.username}"
    state.current_session_id = session_id
    
    # Ensure session exists in DB
    if not hosts_manager.get_chat_session(session_id):
        hosts_manager.create_chat_session(session_id, current_user.id, request.host_id)
    
    # Save user message to DB
    ts = datetime.now(timezone.utc)
    hosts_manager.add_chat_message(ChatMessage(
        session_id=session_id,
        role="user",
        content=request.query,
        created_at=ts
    ))
    
    # Broadcast user message so other windows see it
    await broadcast({
        "type": "user_message",
        "content": request.query,
        "timestamp": ts.isoformat()
    })
    
    # Cancel any currently running task
    if state.current_task and not state.current_task.done():
        state.current_task.cancel()
        try:
            await state.current_task
        except asyncio.CancelledError:
            pass
    
    # Get Ollama instance
    if request.ollama_id:
        ollama_instance = hosts_manager.get_ollama_instance_by_id(request.ollama_id)
    else:
        ollama_instance = hosts_manager.get_default_ollama_instance()
        
    if not ollama_instance:
        raise HTTPException(status_code=400, detail="No Ollama instance configured")

    # Use default model and format from instance if not provided in request
    actual_model = request.model or ollama_instance.default_model
    actual_format = request.format
    if actual_format == "auto" and ollama_instance.default_format != "auto":
        actual_format = ollama_instance.default_format

    if request.host_id:
        host_config = hosts_manager.get_by_id(request.host_id)
        if host_config:
            # Pull from session vault if available
            vault_entry = state.session_vault.get(request.host_id)
            if vault_entry:
                env_overrides["SSH_PASSWORD"] = vault_entry["password"]
                # Update last used timestamp
                vault_entry["last_used"] = time.time()
            elif host_config.password:
                env_overrides["SSH_PASSWORD"] = host_config.password.get_secret_value()
    
    # Re-initialize agent only if model, host, or ollama instance changes
    needs_reinit = (
        not state.agent or 
        state.agent.llm.model != actual_model or 
        state.current_host_id != request.host_id or 
        state.current_ollama_id != (ollama_instance.id if ollama_instance else None)
    )

    if needs_reinit:
        system_message = "You are an SSH MCP Agent."
        initial_messages = []
        if state.agent:
            # Preserve history from memory if agent was already active
            initial_messages = state.agent.messages
        else:
            # Try to load history from DB for this user session
            history = hosts_manager.get_chat_history(session_id)
            if history:
                # Convert DB model to list of dicts for agent
                initial_messages = [
                    {"role": h.role, "content": h.content, "name": h.name}
                    for h in history
                ]
                # Filter out messages that might be redundant if we are adding a new user query
                # But since we just added the new user query to DB, we should probably 
                # load everything EXCEPT the very last user message we just added?
                # Actually, the agent.run() will add the query to self.messages IF it's different 
                # from the last one.
            
        if request.host_id:
            host_config = hosts_manager.get_by_id(request.host_id)
            if host_config:
                system_message += f" The user has currently selected host '{host_config.name}' ({host_config.host}) as the primary target. Use this host for SSH operations unless instructed otherwise."
        
        try:
            state.agent = SSHMCPAgent(
                model=actual_model, 
                format=actual_format, 
                log_callback=log_callback,
                env_overrides=env_overrides,
                config_path=config_path,
                system_message=system_message,
                ollama_host=ollama_instance.host,
                initial_messages=initial_messages
            )
            state.current_host_id = request.host_id
            state.current_ollama_id = ollama_instance.id
        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    else:
        # Just update environment (in case password was added to vault since last run)
        state.agent.update_env(env_overrides)
    
    # Run agent in a background task
    async def run_agent_and_save():
        try:
            await state.agent.run(request.query)
        except Exception as e:
            logger.error(f"Error in agent run: {e}")
            await broadcast({"type": "error", "content": str(e), "timestamp": datetime.now(timezone.utc).isoformat()})

    state.current_task = asyncio.create_task(run_agent_and_save())
    return {"status": "started"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = Query(None)):
    await websocket.accept()
    
    if not token:
        logger.warning("WebSocket connection attempt without token")
        try:
            await websocket.send_json({"type": "error", "content": "Authentication token missing"})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        except Exception:
            pass
        return
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            logger.warning("WebSocket token missing 'sub' claim")
            await websocket.send_json({"type": "error", "content": "Invalid token: missing subject"})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        logger.info(f"WebSocket connection accepted for user: {username}")
    except JWTError as e:
        logger.warning(f"WebSocket token validation failed: {e}")
        try:
            await websocket.send_json({"type": "error", "content": f"Authentication failed: {e}"})
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        except Exception:
            pass
        return

    state.websockets.append(websocket)
    try:
        while True:
            # Keep connection alive and wait for messages if needed
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in state.websockets:
            state.websockets.remove(websocket)

@app.get("/history")
async def get_history(current_user: User = Depends(get_current_user)):
    session_id = f"session-{current_user.username}"
    history = hosts_manager.get_chat_history(session_id)
    if history:
        return {"messages": [h.model_dump() for h in history]}
    
    # Fallback to current agent memory if session is new but agent exists
    if state.agent:
        return {"messages": state.agent.messages}
    return {"messages": []}

app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")

def main():
    import uvicorn
    import argparse
    parser = argparse.ArgumentParser(description="SSH MCP UI")
    parser.add_argument("--config", help="Path to configuration directory or file")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind UI to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind UI to")
    
    args = parser.parse_args()
    
    if args.config:
        os.environ["SSH_MCP_CONFIG"] = args.config
        
    uvicorn.run("ssh_mcp_agent.ui.app:app", host=args.host, port=args.port, reload=False)

if __name__ == "__main__":
    main()
