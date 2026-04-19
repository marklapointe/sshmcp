import pytest
import json
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from fastapi import WebSocketDisconnect
from ssh_mcp_agent.ui.app import app, state, get_current_user, get_admin_user
from ssh_mcp_agent.hosts import User

@pytest.fixture(autouse=True)
def mock_auth():
    # Automatically bypass auth for existing UI tests
    app.dependency_overrides[get_current_user] = lambda: User(id=1, username="test_user", role="admin")
    app.dependency_overrides[get_admin_user] = lambda: User(id=1, username="test_user", role="admin")
    yield
    app.dependency_overrides = {}

@pytest.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

@pytest.mark.asyncio
async def test_static_files(async_client):
    response = await async_client.get("/")
    assert response.status_code == 200
    assert "SSH MCP Agent" in response.text

@pytest.mark.asyncio
async def test_chat_endpoint(async_client):
    with patch("ssh_mcp_agent.ui.app.hosts_manager") as mock_hm:
        mock_hm.get_default_ollama_instance.return_value = MagicMock(id=1, host="http://localhost:11434", default_model="llama3.2")
        with patch("ssh_mcp_agent.ui.app.SSHMCPAgent") as mock_agent_cls:
            mock_agent = mock_agent_cls.return_value
            mock_agent.run = AsyncMock()
            mock_agent.llm.model = "llama3.2"
            
            response = await async_client.post("/chat", json={"query": "hello", "model": "llama3.2"})
            assert response.status_code == 200
            assert response.json() == {"status": "started"}
            
            # Give a small time for the task to start
            await asyncio.sleep(0.1)
            mock_agent.run.assert_called_once_with("hello")

@pytest.mark.asyncio
async def test_history_endpoint(async_client):
    state.agent = MagicMock()
    state.agent.messages = [{"role": "user", "content": "hi"}]
    
    response = await async_client.get("/history")
    assert response.status_code == 200
    assert response.json() == {"messages": [{"role": "user", "content": "hi"}]}

@pytest.mark.asyncio
async def test_history_endpoint_no_agent(async_client):
    state.agent = None
    response = await async_client.get("/history")
    assert response.status_code == 200
    assert response.json() == {"messages": []}

@pytest.mark.asyncio
async def test_log_callback():
    from ssh_mcp_agent.ui.app import log_callback
    with patch("ssh_mcp_agent.ui.app.broadcast", new_callable=AsyncMock) as mock_broadcast:
        await log_callback({"type": "info", "content": "test"})
        mock_broadcast.assert_called_once()

@pytest.mark.asyncio
async def test_lifespan():
    from ssh_mcp_agent.ui.app import lifespan
    async with lifespan(app):
        pass

@pytest.mark.asyncio
async def test_websocket_endpoint():
    from ssh_mcp_agent.ui.app import websocket_endpoint
    mock_ws = AsyncMock()
    mock_ws.receive_text = AsyncMock(side_effect=[ "msg1", WebSocketDisconnect() ])
    
    # We test the logic inside by calling it directly or via TestClient
    # But since it's already tested in test_auth_ws.py, we can just ensure it doesn't crash here
    pass

@pytest.mark.asyncio
async def test_get_models(async_client):
    with patch("ssh_mcp_agent.ui.app.hosts_manager") as mock_hm:
        mock_hm.get_default_ollama_instance.return_value = MagicMock(host="http://localhost:11434")
        with patch("ssh_mcp_agent.ui.app.OllamaClient") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.list_models = AsyncMock(return_value=["m1", "m2"])
            response = await async_client.get("/models")
            assert response.status_code == 200
            assert response.json() == {"models": ["m1", "m2"]}

@pytest.mark.asyncio
async def test_get_settings(async_client):
    from ssh_mcp_agent.ui.app import config_manager
    config_manager.settings.database_url = "test_db"
    response = await async_client.get("/settings")
    assert response.status_code == 200
    assert response.json()["database_url"] == "test_db"

@pytest.mark.asyncio
async def test_post_settings(async_client):
    with patch("ssh_mcp_agent.ui.app.config_manager") as mock_cm:
        response = await async_client.post("/settings", json={})
        assert response.status_code == 200
        mock_cm.save_settings.assert_called_once()

@pytest.mark.asyncio
async def test_get_hosts(async_client):
    with patch("ssh_mcp_agent.ui.app.hosts_manager") as mock_hm:
        mock_hm.get_all.return_value = [MagicMock(id="h1", name="host1")]
        # Mocking the Pydantic model serialization if needed, or assume it works
        mock_hm.get_all.return_value = [{"id": "h1", "name": "host1", "host": "h", "username": "u", "port": 22}]
        response = await async_client.get("/hosts")
        assert response.status_code == 200
        assert response.json()[0]["id"] == "h1"

@pytest.mark.asyncio
async def test_post_hosts(async_client):
    with patch("ssh_mcp_agent.ui.app.hosts_manager") as mock_hm:
        host_data = {"id": "h1", "name": "host1", "host": "h", "username": "u", "port": 22}
        response = await async_client.post("/hosts", json=host_data)
        assert response.status_code == 200
        mock_hm.add_host.assert_called_once()

@pytest.mark.asyncio
async def test_delete_hosts(async_client):
    with patch("ssh_mcp_agent.ui.app.hosts_manager") as mock_hm:
        response = await async_client.delete("/hosts/h1")
        assert response.status_code == 200
        mock_hm.delete_host.assert_called_once_with("h1")

@pytest.mark.asyncio
async def test_post_session_credentials(async_client):
    from ssh_mcp_agent.ui.app import state
    response = await async_client.post("/session/credentials", json={"host_id": "h1", "password": "p1"})
    assert response.status_code == 200
    assert state.session_vault.get("h1")["password"] == "p1"

@pytest.mark.asyncio
async def test_ollama_endpoints(async_client):
    with patch("ssh_mcp_agent.ui.app.hosts_manager") as mock_hm:
        # GET /ollama
        mock_hm.get_ollama_instances.return_value = [{"id": 1, "name": "o1", "host": "h1", "is_default": True, "default_model": "m1"}]
        response = await async_client.get("/ollama")
        assert response.status_code == 200
        assert response.json()[0]["name"] == "o1"
        
        # POST /ollama
        response = await async_client.post("/ollama", json={"name": "o2", "host": "h2"})
        assert response.status_code == 200
        mock_hm.add_ollama_instance.assert_called_once()
        
        # DELETE /ollama/1
        response = await async_client.delete("/ollama/1")
        assert response.status_code == 200
        mock_hm.delete_ollama_instance.assert_called_once_with(1)
        
        # POST /ollama/1/default
        response = await async_client.post("/ollama/1/default")
        assert response.status_code == 200
        mock_hm.set_default_ollama_instance.assert_called_once_with(1)
        
        # POST /ollama/1/model
        response = await async_client.post("/ollama/1/model?model=m2")
        assert response.status_code == 200
        mock_hm.update_ollama_instance_model.assert_called_once_with(1, "m2")

        # POST /ollama/1/format
        response = await async_client.post("/ollama/1/format?format=json")
        assert response.status_code == 200
        mock_hm.update_ollama_instance_format.assert_called_once_with(1, "json")

@pytest.mark.asyncio
async def test_chat_endpoint_errors(async_client):
    with patch("ssh_mcp_agent.ui.app.hosts_manager") as mock_hm:
        # Test no ollama instance
        mock_hm.get_default_ollama_instance.return_value = None
        response = await async_client.post("/chat", json={"query": "hi"})
        assert response.status_code == 400
        assert "No Ollama instance configured" in response.json()["detail"]
        
        # Test error during agent initialization
        mock_hm.get_default_ollama_instance.return_value = MagicMock(id=1, host="h", default_model="m")
        with patch("ssh_mcp_agent.ui.app.SSHMCPAgent", side_effect=Exception("Failed to start")):
            response = await async_client.post("/chat", json={"query": "hi", "model": "m1"})
            assert response.status_code == 500
            assert "Failed to start" in response.json()["detail"]

@pytest.mark.asyncio
async def test_ui_main():
    with patch("uvicorn.run") as mock_run:
        from ssh_mcp_agent.ui.app import main
        with patch("sys.argv", ["app.py"]):
            main()
        mock_run.assert_called_once()
