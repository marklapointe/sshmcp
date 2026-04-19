import asyncio
import json
import os
import sys
import logging
import argparse
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager

from ..agent import SSHMCPAgent
from ..llm.client import ToolCallingFormat, OllamaClient
from ..hosts import HostsManager, HostConfig, OllamaInstance
from ..config import ConfigManager

logger = logging.getLogger(__name__)

class AgentState:
    def __init__(self):
        self.agent: Optional[SSHMCPAgent] = None
        self.queue: asyncio.Queue = asyncio.Queue()
        self.messages: List[Dict[str, Any]] = []
        self.current_host_id: Optional[str] = None
        self.current_ollama_id: Optional[int] = None
        # Session Vault: host_id -> password
        self.session_vault: Dict[str, str] = {}

state = AgentState()

# We use an environment variable to pass the config path from main() to the FastAPI app initialization
config_path = os.getenv("SSH_MCP_CONFIG")
config_manager = ConfigManager(config_path)
hosts_manager = HostsManager(config_manager.settings.database_url, config_path)

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(lifespan=lifespan)

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

async def log_callback(data: Dict[str, Any]):
    await state.queue.put(data)

@app.get("/hosts")
async def get_hosts():
    return hosts_manager.get_all()

@app.post("/hosts")
async def add_host(host: HostConfig):
    logger.info(f"Registering host: {host.name} ({host.host}) with ID {host.id}")
    # If password is provided, put it in session vault and it will be excluded from disk
    if host.password:
        state.session_vault[host.id] = host.password.get_secret_value()
    hosts_manager.add_host(host)
    return {"status": "ok"}

@app.delete("/hosts/{host_id}")
async def delete_host(host_id: str):
    hosts_manager.delete_host(host_id)
    if host_id in state.session_vault:
        del state.session_vault[host_id]
    return {"status": "ok"}

@app.get("/ollama")
async def get_ollama_instances():
    return hosts_manager.get_ollama_instances()

@app.post("/ollama")
async def add_ollama_instance(instance: OllamaInstance):
    hosts_manager.add_ollama_instance(instance)
    return {"status": "ok"}

@app.delete("/ollama/{instance_id}")
async def delete_ollama_instance(instance_id: int):
    hosts_manager.delete_ollama_instance(instance_id)
    return {"status": "ok"}

@app.post("/ollama/{instance_id}/default")
async def set_default_ollama(instance_id: int):
    hosts_manager.set_default_ollama_instance(instance_id)
    return {"status": "ok"}

@app.get("/models")
async def get_models(ollama_id: Optional[int] = None):
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
async def get_settings():
    """Get current application settings."""
    return config_manager.settings.model_dump(exclude={"ssh_password"})

@app.post("/settings")
async def update_settings(request: SettingsUpdateRequest):
    """Update and persist application settings."""
    current = config_manager.settings
    # Currently no settings in SettingsUpdateRequest, but we keep the endpoint
    config_manager.save_settings(current)
    return {"status": "ok", "settings": current.model_dump(exclude={"ssh_password"})}

@app.post("/session/credentials")
async def set_credentials(request: CredentialRequest):
    """Securely store credentials in the in-memory session vault."""
    state.session_vault[request.host_id] = request.password
    # Force agent re-initialization on next chat if host matches
    if state.current_host_id == request.host_id:
        state.agent = None
    return {"status": "ok"}

@app.post("/chat")
async def chat(request: ChatRequest):
    env_overrides = {}
    
    # Get Ollama instance
    if request.ollama_id:
        ollama_instance = hosts_manager.get_ollama_instance_by_id(request.ollama_id)
    else:
        ollama_instance = hosts_manager.get_default_ollama_instance()
        
    if not ollama_instance:
        raise HTTPException(status_code=400, detail="No Ollama instance configured")

    # Use default model from instance if not provided in request
    actual_model = request.model or ollama_instance.default_model

    if request.host_id:
        host_config = hosts_manager.get_by_id(request.host_id)
        if host_config:
            # Pull from session vault if available
            session_password = state.session_vault.get(request.host_id)
            if session_password:
                env_overrides["SSH_PASSWORD"] = session_password
            elif host_config.password:
                env_overrides["SSH_PASSWORD"] = host_config.password.get_secret_value()
    
    # Re-initialize agent if model, host, or ollama instance changes, or if agent was cleared
    if not state.agent or state.agent.llm.model != actual_model or state.current_host_id != request.host_id or state.current_ollama_id != (ollama_instance.id if ollama_instance else None):
        system_message = "You are an SSH MCP Agent."
        if request.host_id:
            host_config = hosts_manager.get_by_id(request.host_id)
            if host_config:
                system_message += f" The user has currently selected host '{host_config.name}' ({host_config.host}) as the primary target. Use this host for SSH operations unless instructed otherwise."
        
        state.agent = SSHMCPAgent(
            model=actual_model, 
            format=request.format, 
            log_callback=log_callback,
            env_overrides=env_overrides,
            config_path=config_path,
            system_message=system_message,
            ollama_host=ollama_instance.host
        )
        state.current_host_id = request.host_id
        state.current_ollama_id = ollama_instance.id
    
    # Run agent in a background task so we can stream logs
    asyncio.create_task(state.agent.run(request.query))
    return {"status": "started"}

async def event_generator(request: Request):
    while True:
        if await request.is_disconnected():
            break
        try:
            data = await asyncio.wait_for(state.queue.get(), timeout=1.0)
            yield f"data: {json.dumps(data)}\n\n"
        except asyncio.TimeoutError:
            yield ": keep-alive\n\n"

@app.get("/events")
async def events(request: Request):
    return StreamingResponse(event_generator(request), media_type="text/event-stream")

@app.get("/history")
async def get_history():
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
