import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from ssh_mcp_agent.llm.client import OllamaClient, ToolCallingFormat, LLMResponse

@pytest.mark.asyncio
async def test_detect_format_native():
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.show = AsyncMock()
        
        # Mock info object with template
        mock_info = MagicMock()
        mock_info.template = "Some template with .Tools and .ToolCalls"
        mock_client.show.return_value = mock_info
        
        client = OllamaClient(model="test-model", format=ToolCallingFormat.AUTO)
        fmt = await client._detect_format()
        
        assert fmt == ToolCallingFormat.NATIVE
        mock_client.show.assert_called_once_with("test-model")

@pytest.mark.asyncio
async def test_detect_format_xml():
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.show = AsyncMock()
        
        mock_info = MagicMock()
        mock_info.template = "Standard template"
        mock_client.show.return_value = mock_info
        
        client = OllamaClient(model="mistral-test", format=ToolCallingFormat.AUTO)
        fmt = await client._detect_format()
        
        assert fmt == ToolCallingFormat.XML

@pytest.mark.asyncio
async def test_detect_format_json_default():
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.show = AsyncMock()
        
        mock_info = MagicMock()
        mock_info.template = "Standard template"
        mock_client.show.return_value = mock_info
        
        client = OllamaClient(model="generic-model", format=ToolCallingFormat.AUTO)
        fmt = await client._detect_format()
        
        assert fmt == ToolCallingFormat.JSON

@pytest.mark.asyncio
async def test_detect_format_exception():
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.show = AsyncMock(side_effect=Exception("API Error"))
        
        client = OllamaClient(model="generic-model", format=ToolCallingFormat.AUTO)
        fmt = await client._detect_format()
        
        assert fmt == ToolCallingFormat.JSON

@pytest.mark.asyncio
async def test_chat_native():
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.chat = AsyncMock()
        mock_client.show = AsyncMock()
        mock_info = MagicMock()
        mock_info.template = ".Tools"
        mock_client.show.return_value = mock_info
        
        mock_client.chat.return_value = {
            "message": {
                "role": "assistant",
                "content": "Hello",
                "tool_calls": [
                    {
                        "function": {
                            "name": "test_tool",
                            "arguments": {"arg1": "val1"}
                        }
                    }
                ]
            }
        }
        
        client = OllamaClient(model="test-model", format=ToolCallingFormat.NATIVE)
        response = await client.chat([{"role": "user", "content": "hi"}], [])
        
        assert response.content == "Hello"
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0]["name"] == "test_tool"

@pytest.mark.asyncio
async def test_chat_json_with_tools():
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.chat = AsyncMock()
        
        mock_client.chat.return_value = {
            "message": {
                "content": json.dumps({
                    "tool_calls": [{"name": "json_tool", "arguments": {"x": 1}}]
                })
            }
        }
        
        client = OllamaClient(model="test-model", format=ToolCallingFormat.JSON)
        response = await client.chat([], [])
        
        assert response.tool_calls[0]["name"] == "json_tool"

@pytest.mark.asyncio
async def test_chat_json_plain_text():
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.chat = AsyncMock()
        
        mock_client.chat.return_value = {
            "message": {
                "content": "Just a normal response"
            }
        }
        
        client = OllamaClient(model="test-model", format=ToolCallingFormat.JSON)
        response = await client.chat([], [])
        
        assert response.content == "Just a normal response"
        assert response.tool_calls == []

@pytest.mark.asyncio
async def test_chat_xml():
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.chat = AsyncMock()
        
        mock_client.chat.return_value = {
            "message": {
                "content": "Thinking... <tool_call>{\"name\": \"xml_tool\", \"arguments\": {}}</tool_call> and done."
            }
        }
        
        client = OllamaClient(model="test-model", format=ToolCallingFormat.XML)
        response = await client.chat([], [])
        
        assert response.tool_calls[0]["name"] == "xml_tool"
        assert response.content == "Thinking...  and done."

@pytest.mark.asyncio
async def test_chat_xml_no_tool():
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.chat = AsyncMock()
        
        mock_client.chat.return_value = {
            "message": {
                "content": "No tools here."
            }
        }
        
        client = OllamaClient(model="test-model", format=ToolCallingFormat.XML)
        response = await client.chat([], [])
        
        assert response.content == "No tools here."
        assert response.tool_calls == []

@pytest.mark.asyncio
async def test_chat_xml_invalid_json():
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.chat = AsyncMock()
        
        mock_client.chat.return_value = {
            "message": {
                "content": "<tool_call>invalid json</tool_call>"
            }
        }
        
        client = OllamaClient(model="test-model", format=ToolCallingFormat.XML)
        response = await client.chat([], [])
        
        assert response.tool_calls == []

@pytest.mark.asyncio
async def test_detect_format_cached():
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        client = OllamaClient()
        client._detected_format = ToolCallingFormat.NATIVE
        fmt = await client._detect_format()
        assert fmt == ToolCallingFormat.NATIVE
        mock_client.show.assert_not_called()

@pytest.mark.asyncio
async def test_chat_invalid_format():
    client = OllamaClient()
    client.format = "unsupported"
    with pytest.raises(NotImplementedError):
        await client.chat([], [])

@pytest.mark.asyncio
async def test_list_models():
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.list = AsyncMock()
        
        # Mock ListResponse
        mock_response = MagicMock()
        mock_model1 = MagicMock()
        mock_model1.model = "m1"
        mock_model2 = MagicMock()
        mock_model2.model = "m2"
        mock_response.models = [mock_model1, mock_model2]
        
        mock_client.list.return_value = mock_response
        
        client = OllamaClient(model="test")
        models = await client.list_models()
        
        assert models == ["m1", "m2"]
        mock_client.list.assert_called_once()

@pytest.mark.asyncio
async def test_list_models_empty():
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.list = AsyncMock(side_effect=Exception("Failed"))
        
        client = OllamaClient(model="test")
        models = await client.list_models()
        
        assert models == []
