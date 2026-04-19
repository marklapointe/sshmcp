import asyncio
import os
import argparse
from typing import Any, Dict, List, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
)
from pydantic import BaseModel, Field

from .tools.ssh import SSHClient, SSHConfig
from .hosts import HostsManager, HostConfig
from .config import ConfigManager

# Managers will be initialized in main()
hosts_manager: Optional[HostsManager] = None
config_manager: Optional[ConfigManager] = None

# Why we are using a Server class:
# The MCP Server acts as a bridge between the LLM and the local/remote tools.
# It defines what tools are available and how to execute them.
app = Server("ssh-mcp-server")

# Define input schemas for our tools using Pydantic for validation.
class SSHExecuteArgs(BaseModel):
    host: str = Field(..., description="Remote host identifier (ID, Name, or IP) configured in the system")
    command: str = Field(..., description="Command to execute")

class SSHTransferArgs(BaseModel):
    host: str = Field(..., description="Remote host identifier (ID, Name, or IP) configured in the system")
    local_path: str = Field(..., description="Path on local machine")
    remote_path: str = Field(..., description="Path on remote machine")

class SSHCheckArgs(BaseModel):
    host: str = Field(..., description="Remote host identifier to check configuration for")

# Tool Registration
# -----------------
# We register tools so that the LLM can "discover" them.

@app.list_tools()
async def list_tools() -> List[Tool]:
    """List available SSH tools."""
    return [
        Tool(
            name="ssh_execute",
            description="Execute a command on a remote system via SSH using pre-configured credentials",
            inputSchema=SSHExecuteArgs.model_json_schema(),
        ),
        Tool(
            name="ssh_upload",
            description="Upload a local file to a remote system via SFTP using pre-configured credentials",
            inputSchema=SSHTransferArgs.model_json_schema(),
        ),
        Tool(
            name="ssh_download",
            description="Download a remote file to the local system via SFTP using pre-configured credentials",
            inputSchema=SSHTransferArgs.model_json_schema(),
        ),
        Tool(
            name="ssh_check_config",
            description="Check if a host is pre-configured with necessary information",
            inputSchema=SSHCheckArgs.model_json_schema(),
        ),
    ]

def get_ssh_config(identifier: str) -> SSHConfig:
    """Look up SSH configuration for a given host identifier."""
    if hosts_manager is None:
        raise ValueError("Hosts manager not initialized")
    
    host_config = hosts_manager.get_by_name_or_host(identifier)
    
    # Use config_manager for global settings if available
    global_ssh_password = config_manager.settings.ssh_password.get_secret_value() if config_manager and config_manager.settings.ssh_password else None
    
    # Check environment variable as well (for session-based passwords from UI)
    env_ssh_password = os.environ.get("SSH_PASSWORD")
    
    if host_config:
        return SSHConfig(
            host=host_config.host,
            username=host_config.username,
            password=env_ssh_password or global_ssh_password or (host_config.password.get_secret_value() if host_config.password else None),
            key_filename=host_config.key_filename,
            port=host_config.port
        )
    
    raise ValueError(f"No configuration found for host '{identifier}'. Please configure it in the Host Manager first.")

@app.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle tool calls from the LLM client."""
    if hosts_manager is None:
        return [TextContent(type="text", text="Error: Hosts manager not initialized")]
    
    try:
        if name == "ssh_execute":
            args = SSHExecuteArgs(**arguments)
            config = get_ssh_config(args.host)
            client = SSHClient(config)
            try:
                status, stdout, stderr = client.execute_command(args.command)
                result = f"Exit Status: {status}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                return [TextContent(type="text", text=result)]
            finally:
                client.close()

        elif name == "ssh_upload":
            args = SSHTransferArgs(**arguments)
            config = get_ssh_config(args.host)
            client = SSHClient(config)
            try:
                client.upload_file(args.local_path, args.remote_path)
                return [TextContent(type="text", text=f"Successfully uploaded {args.local_path} to {args.remote_path}")]
            finally:
                client.close()

        elif name == "ssh_download":
            args = SSHTransferArgs(**arguments)
            config = get_ssh_config(args.host)
            client = SSHClient(config)
            try:
                client.download_file(args.remote_path, args.local_path)
                return [TextContent(type="text", text=f"Successfully downloaded {args.remote_path} to {args.local_path}")]
            finally:
                client.close()
        
        elif name == "ssh_check_config":
            args = SSHCheckArgs(**arguments)
            exists = hosts_manager.has_host_info(args.host)
            if exists:
                host_info = hosts_manager.get_by_name_or_host(args.host)
                return [TextContent(type="text", text=f"Host '{args.host}' is configured with username '{host_info.username}'")]
            else:
                return [TextContent(type="text", text=f"Host '{args.host}' is NOT configured. Please provide its details in the Host Manager.")]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]

async def main():
    """Main entry point for the MCP server."""
    parser = argparse.ArgumentParser(description="SSH MCP Server")
    parser.add_argument("--config", help="Path to configuration directory or file")
    args = parser.parse_args()

    global hosts_manager, config_manager
    config_manager = ConfigManager(args.config)
    hosts_manager = HostsManager(config_manager.settings.database_url, args.config)

    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())
