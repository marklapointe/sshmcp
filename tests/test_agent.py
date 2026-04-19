import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from ssh_mcp_agent.agent import SSHMCPAgent
from ssh_mcp_agent.llm.client import LLMResponse

@pytest.fixture
def mock_mcp_session():
    session = AsyncMock()
    session.initialize = AsyncMock()
    
    mock_tool = MagicMock()
    mock_tool.name = "ssh_execute"
    mock_tool.description = "desc"
    mock_tool.inputSchema = {}
    
    mock_tools_list = MagicMock()
    mock_tools_list.tools = [mock_tool]
    session.list_tools = AsyncMock(return_value=mock_tools_list)
    
    mock_result = MagicMock()
    mock_content = MagicMock()
    mock_content.text = "tool output"
    mock_result.content = [mock_content]
    session.call_tool = AsyncMock(return_value=mock_result)
    
    return session

@pytest.mark.asyncio
async def test_agent_run(mock_mcp_session):
    with patch("ssh_mcp_agent.agent.HostsManager") as mock_hm_cls:
        mock_hm = mock_hm_cls.return_value
        mock_hm.get_default_ollama_instance.return_value = MagicMock(host="http://localhost:11434", default_model="llama3.2")
        
        with patch("ssh_mcp_agent.agent.stdio_client") as mock_stdio:
            # Mock stdio_client context manager
            mock_stdio.return_value.__aenter__.return_value = (MagicMock(), MagicMock())
            
            with patch("ssh_mcp_agent.agent.ClientSession") as mock_session_cls:
                mock_session_cls.return_value.__aenter__.return_value = mock_mcp_session
                
                with patch("ssh_mcp_agent.agent.OllamaClient") as mock_llm_cls:
                    mock_llm = mock_llm_cls.return_value
                    
                    # First call returns a tool call, second call returns content
                    mock_llm.chat = AsyncMock(side_effect=[
                        LLMResponse(content="I will run a command", tool_calls=[{"name": "ssh_execute", "arguments": {}}]),
                        LLMResponse(content="All done", tool_calls=[])
                    ])
                    
                    log_callback = AsyncMock()
                    agent = SSHMCPAgent(log_callback=log_callback)
                    await agent.run("test query")
                    
                    assert len(agent.messages) == 4 # user, assistant (thinking), tool result, assistant (final)
                    assert agent.messages[0]["content"] == "test query"
                    assert agent.messages[-1]["content"] == "All done"
                    
                    # Check log_callback
                    # 4 calls: 2 assistant messages, 1 tool call, 1 tool result
                    assert log_callback.call_count == 4
                    log_callback.assert_any_call({"type": "tool_call", "name": "ssh_execute", "arguments": {}})
                    log_callback.assert_any_call({"type": "tool_result", "name": "ssh_execute", "content": "tool output"})

@pytest.mark.asyncio
async def test_agent_main():
    with patch("ssh_mcp_agent.agent.SSHMCPAgent") as mock_agent_cls:
        mock_agent = mock_agent_cls.return_value
        mock_agent.run = AsyncMock()
        
        from ssh_mcp_agent.agent import main
        import sys
        with patch.object(sys, 'argv', ['agent.py', 'hello', '--model', 'm1', '--format', 'json']):
            await main()
    
        mock_agent_cls.assert_called_once_with(model='m1', format='json', config_path=None)
        mock_agent.run.assert_called_once_with('hello')
