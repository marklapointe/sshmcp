import pytest
from unittest.mock import patch, MagicMock
from ssh_mcp_agent.server import list_tools, call_tool

@pytest.mark.asyncio
async def test_list_tools():
    tools = await list_tools()
    assert len(tools) == 4
    names = [tool.name for tool in tools]
    assert "ssh_execute" in names
    assert "ssh_upload" in names
    assert "ssh_download" in names
    assert "ssh_check_config" in names

@pytest.fixture
def mock_hosts_manager():
    with patch("ssh_mcp_agent.server.hosts_manager") as mock_hm:
        from ssh_mcp_agent.hosts import HostConfig
        mock_hm.get_by_name_or_host.side_effect = lambda h: HostConfig(
            id=h, name=h, host=h, username="user", port=22
        ) if h != "unknown" else None
        mock_hm.has_host_info.side_effect = lambda h: h != "unknown"
        yield mock_hm

@pytest.mark.asyncio
async def test_call_tool_ssh_execute(mock_hosts_manager):
    with patch("ssh_mcp_agent.server.SSHClient") as mock_ssh_cls:
        mock_client = mock_ssh_cls.return_value
        mock_client.execute_command.return_value = (0, "output", "")
        
        args = {
            "host": "localhost",
            "command": "whoami"
        }
        
        result = await call_tool("ssh_execute", args)
        
        assert len(result) == 1
        assert "Exit Status: 0" in result[0].text
        assert "STDOUT:\noutput" in result[0].text
        mock_client.execute_command.assert_called_once_with("whoami")
        mock_client.close.assert_called_once()

@pytest.mark.asyncio
async def test_call_tool_ssh_upload(mock_hosts_manager):
    with patch("ssh_mcp_agent.server.SSHClient") as mock_ssh_cls:
        mock_client = mock_ssh_cls.return_value
        
        args = {
            "host": "localhost",
            "local_path": "l.txt",
            "remote_path": "r.txt"
        }
        
        result = await call_tool("ssh_upload", args)
        
        assert len(result) == 1
        assert "Successfully uploaded l.txt" in result[0].text
        mock_client.upload_file.assert_called_once_with("l.txt", "r.txt")

@pytest.mark.asyncio
async def test_call_tool_ssh_download(mock_hosts_manager):
    with patch("ssh_mcp_agent.server.SSHClient") as mock_ssh_cls:
        mock_client = mock_ssh_cls.return_value
        
        args = {
            "host": "localhost",
            "local_path": "l.txt",
            "remote_path": "r.txt"
        }
        
        result = await call_tool("ssh_download", args)
        
        assert len(result) == 1
        assert "Successfully downloaded r.txt" in result[0].text
        mock_client.download_file.assert_called_once_with("r.txt", "l.txt")

@pytest.mark.asyncio
async def test_call_tool_ssh_check_config(mock_hosts_manager):
    result = await call_tool("ssh_check_config", {"host": "localhost"})
    assert "is configured" in result[0].text
    
    result = await call_tool("ssh_check_config", {"host": "unknown"})
    assert "is NOT configured" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_unknown():
    result = await call_tool("unknown_tool", {})
    assert len(result) == 1
    assert "Error: Unknown tool: unknown_tool" in result[0].text

@pytest.mark.asyncio
async def test_call_tool_exception(mock_hosts_manager):
    with patch("ssh_mcp_agent.server.SSHClient", side_effect=Exception("Conn error")):
        result = await call_tool("ssh_execute", {"host": "h", "command": "c"})
        assert len(result) == 1
        assert "Error: Conn error" in result[0].text
