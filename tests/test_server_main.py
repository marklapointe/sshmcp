import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from ssh_mcp_agent.server import main

@pytest.mark.asyncio
async def test_server_main():
    with patch("ssh_mcp_agent.server.stdio_server") as mock_stdio:
        mock_stdio.return_value.__aenter__.return_value = (MagicMock(), MagicMock())
        with patch("ssh_mcp_agent.server.app.run") as mock_run:
            await main()
            mock_run.assert_called_once()
