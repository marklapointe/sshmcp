import pytest
import json
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from ssh_mcp_agent.ui.app import app, state

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
    await log_callback({"log": "info"})
    data = await state.queue.get()
    assert data == {"log": "info"}

@pytest.mark.asyncio
async def test_lifespan():
    from ssh_mcp_agent.ui.app import lifespan
    async with lifespan(app):
        pass

@pytest.mark.asyncio
async def test_event_generator_direct():
    from ssh_mcp_agent.ui.app import event_generator
    mock_request = MagicMock()
    mock_request.is_disconnected = AsyncMock(side_effect=[False, False, True])
    
    # 1. Test normal data yield
    await state.queue.put({"type": "msg1"})
    gen = event_generator(mock_request)
    msg1 = await anext(gen)
    assert "msg1" in msg1
    
    # 2. Test timeout yield (keep-alive)
    with patch("ssh_mcp_agent.ui.app.asyncio.wait_for", side_effect=asyncio.TimeoutError()):
        msg_ka = await anext(gen)
        assert "keep-alive" in msg_ka

    # 3. Test disconnection
    with pytest.raises(StopAsyncIteration):
        await anext(gen)

@pytest.mark.asyncio
async def test_events_endpoint_hit(async_client):
    with patch("ssh_mcp_agent.ui.app.StreamingResponse") as mock_sr:
        response = await async_client.get("/events")
        assert response.status_code == 200
        mock_sr.assert_called_once()

@pytest.mark.asyncio
async def test_get_models(async_client):
    with patch("ssh_mcp_agent.ui.app.OllamaClient") as mock_client:
        mock_client.return_value.list_models.return_value = ["m1", "m2"]
        response = await async_client.get("/models")
        assert response.status_code == 200
        assert response.json() == ["m1", "m2"]

@pytest.mark.asyncio
async def test_get_settings(async_client):
    from ssh_mcp_agent.ui.app import config_manager
    config_manager.settings.ollama_host = "test_host"
    response = await async_client.get("/settings")
    assert response.status_code == 200
    assert response.json()["ollama_host"] == "test_host"

@pytest.mark.asyncio
async def test_post_settings(async_client):
    with patch("ssh_mcp_agent.ui.app.config_manager") as mock_cm:
        response = await async_client.post("/settings", json={"ollama_host": "new_host", "ollama_model": "new_model"})
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
    from ssh_mcp_agent.ui.app import session_vault
    response = await async_client.post("/session/credentials", json={"host_id": "h1", "password": "p1"})
    assert response.status_code == 200
    assert session_vault.get("h1") == "p1"

@pytest.mark.asyncio
async def test_chat_endpoint_errors(async_client):
    # Test missing model
    response = await async_client.post("/chat", json={"query": "hi"})
    assert response.status_code == 400
    assert "Missing model" in response.json()["detail"]
    
    # Test error during agent initialization
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
