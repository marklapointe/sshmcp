import asyncio
import json
import os
import sys
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager

from ..agent import SSHMCPAgent
from ..llm.client import ToolCallingFormat
from ..hosts import HostsManager, HostConfig

class AgentState:
    def __init__(self):
        self.agent: Optional[SSHMCPAgent] = None
        self.queue: asyncio.Queue = asyncio.Queue()
        self.messages: List[Dict[str, Any]] = []
        self.current_host_id: Optional[str] = None

state = AgentState()
hosts_manager = HostsManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(lifespan=lifespan)

class ChatRequest(BaseModel):
    query: str
    model: str = "llama3.2"
    format: str = "auto"
    host_id: Optional[str] = None

async def log_callback(data: Dict[str, Any]):
    await state.queue.put(data)

@app.get("/hosts")
async def get_hosts():
    return hosts_manager.get_all()

@app.post("/hosts")
async def add_host(host: HostConfig):
    hosts_manager.add_host(host)
    return {"status": "ok"}

@app.delete("/hosts/{host_id}")
async def delete_host(host_id: str):
    hosts_manager.delete_host(host_id)
    return {"status": "ok"}

@app.post("/chat")
async def chat(request: ChatRequest):
    env_overrides = {}
    if request.host_id:
        host_config = hosts_manager.get_by_id(request.host_id)
        if host_config:
            env_overrides = {
                "SSH_HOST": host_config.host,
                "SSH_USERNAME": host_config.username,
                "SSH_PORT": str(host_config.port)
            }
            if host_config.password:
                env_overrides["SSH_PASSWORD"] = host_config.password
            if host_config.key_filename:
                env_overrides["SSH_KEY_FILENAME"] = host_config.key_filename
    
    # Re-initialize agent if model or host changes
    if not state.agent or state.agent.llm.model != request.model or state.current_host_id != request.host_id:
        state.agent = SSHMCPAgent(
            model=request.model, 
            format=request.format, 
            log_callback=log_callback,
            env_overrides=env_overrides
        )
        state.current_host_id = request.host_id
    
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
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main()
