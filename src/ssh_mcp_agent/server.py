import asyncio
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from mcp.server import Server, NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    EmbeddedResource,
    LoggingLevel,
)
from pydantic import BaseModel, Field

from .tools.ssh import SSHClient, SSHConfig, SSHConfig as SSHConfigData
from .hosts import HostsManager, HostConfig

# Load environment variables from .env file if it exists
load_dotenv()

# Initialize Hosts Manager
hosts_manager = HostsManager()

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
    host_config = hosts_manager.get_by_name_or_host(identifier)
    
    if host_config:
        return SSHConfig(
            host=host_config.host,
            username=host_config.username,
            password=host_config.password,
            key_filename=host_config.key_filename,
            port=host_config.port
        )
    
    # Fallback to env vars ONLY if the identifier matches the env SSH_HOST
    env_host = os.getenv("SSH_HOST")
    if env_host and (identifier == env_host or identifier == "default"):
        return SSHConfig(
            host=env_host,
            username=os.getenv("SSH_USERNAME", ""),
            password=os.getenv("SSH_PASSWORD"),
            key_filename=os.getenv("SSH_KEY_FILENAME"),
            port=int(os.getenv("SSH_PORT", "22"))
        )
        
    raise ValueError(f"No configuration found for host '{identifier}'. Please configure it in the Host Manager first.")

@app.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle tool calls from the LLM client."""
    
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
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )

if __name__ == "__main__":
    asyncio.run(main())
