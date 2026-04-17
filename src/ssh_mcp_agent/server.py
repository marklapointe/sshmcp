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

# Load environment variables from .env file if it exists
load_dotenv()

# Why we are using a Server class:
# The MCP Server acts as a bridge between the LLM and the local/remote tools.
# It defines what tools are available and how to execute them.
app = Server("ssh-mcp-server")

# Define input schemas for our tools using Pydantic for validation.
class SSHExecuteArgs(BaseModel):
    host: Optional[str] = Field(None, description="Remote host address (defaults to SSH_HOST env var)")
    username: Optional[str] = Field(None, description="SSH username (defaults to SSH_USERNAME env var)")
    command: str = Field(..., description="Command to execute")
    password: Optional[str] = Field(None, description="SSH password (optional, defaults to SSH_PASSWORD env var)")
    key_filename: Optional[str] = Field(None, description="Path to SSH private key file (optional, defaults to SSH_KEY_FILENAME env var)")
    port: Optional[int] = Field(None, description="SSH port (defaults to SSH_PORT env var or 22)")

class SSHTransferArgs(BaseModel):
    host: Optional[str] = Field(None, description="Remote host address (defaults to SSH_HOST env var)")
    username: Optional[str] = Field(None, description="SSH username (defaults to SSH_USERNAME env var)")
    local_path: str = Field(..., description="Path on local machine")
    remote_path: str = Field(..., description="Path on remote machine")
    password: Optional[str] = Field(None, description="SSH password (optional, defaults to SSH_PASSWORD env var)")
    key_filename: Optional[str] = Field(None, description="Path to SSH private key file (optional, defaults to SSH_KEY_FILENAME env var)")
    port: Optional[int] = Field(None, description="SSH port (defaults to SSH_PORT env var or 22)")

# Tool Registration
# -----------------
# We register tools so that the LLM can "discover" them.

@app.list_tools()
async def list_tools() -> List[Tool]:
    """List available SSH tools."""
    return [
        Tool(
            name="ssh_execute",
            description="Execute a command on a remote system via SSH",
            inputSchema=SSHExecuteArgs.model_json_schema(),
        ),
        Tool(
            name="ssh_upload",
            description="Upload a local file to a remote system via SFTP",
            inputSchema=SSHTransferArgs.model_json_schema(),
        ),
        Tool(
            name="ssh_download",
            description="Download a remote file to the local system via SFTP",
            inputSchema=SSHTransferArgs.model_json_schema(),
        ),
    ]

@app.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """Handle tool calls from the LLM client."""
    
    try:
        if name == "ssh_execute":
            args = SSHExecuteArgs(**arguments)
            
            host = args.host or os.getenv("SSH_HOST")
            username = args.username or os.getenv("SSH_USERNAME")
            password = args.password or os.getenv("SSH_PASSWORD")
            key_filename = args.key_filename or os.getenv("SSH_KEY_FILENAME")
            port_str = os.getenv("SSH_PORT", "22")
            port = args.port or (int(port_str) if port_str.isdigit() else 22)

            if not host:
                raise ValueError("Host must be provided or set via SSH_HOST environment variable")
            if not username:
                raise ValueError("Username must be provided or set via SSH_USERNAME environment variable")

            config = SSHConfig(
                host=host,
                username=username,
                password=password,
                key_filename=key_filename,
                port=port
            )
            client = SSHClient(config)
            try:
                status, stdout, stderr = client.execute_command(args.command)
                result = f"Exit Status: {status}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
                return [TextContent(type="text", text=result)]
            finally:
                client.close()

        elif name == "ssh_upload":
            args = SSHTransferArgs(**arguments)
            
            host = args.host or os.getenv("SSH_HOST")
            username = args.username or os.getenv("SSH_USERNAME")
            password = args.password or os.getenv("SSH_PASSWORD")
            key_filename = args.key_filename or os.getenv("SSH_KEY_FILENAME")
            port_str = os.getenv("SSH_PORT", "22")
            port = args.port or (int(port_str) if port_str.isdigit() else 22)

            if not host:
                raise ValueError("Host must be provided or set via SSH_HOST environment variable")
            if not username:
                raise ValueError("Username must be provided or set via SSH_USERNAME environment variable")

            config = SSHConfig(
                host=host,
                username=username,
                password=password,
                key_filename=key_filename,
                port=port
            )
            client = SSHClient(config)
            try:
                client.upload_file(args.local_path, args.remote_path)
                return [TextContent(type="text", text=f"Successfully uploaded {args.local_path} to {args.remote_path}")]
            finally:
                client.close()

        elif name == "ssh_download":
            args = SSHTransferArgs(**arguments)
            
            host = args.host or os.getenv("SSH_HOST")
            username = args.username or os.getenv("SSH_USERNAME")
            password = args.password or os.getenv("SSH_PASSWORD")
            key_filename = args.key_filename or os.getenv("SSH_KEY_FILENAME")
            port_str = os.getenv("SSH_PORT", "22")
            port = args.port or (int(port_str) if port_str.isdigit() else 22)

            if not host:
                raise ValueError("Host must be provided or set via SSH_HOST environment variable")
            if not username:
                raise ValueError("Username must be provided or set via SSH_USERNAME environment variable")

            config = SSHConfig(
                host=host,
                username=username,
                password=password,
                key_filename=key_filename,
                port=port
            )
            client = SSHClient(config)
            try:
                client.download_file(args.remote_path, args.local_path)
                return [TextContent(type="text", text=f"Successfully downloaded {args.remote_path} to {args.local_path}")]
            finally:
                client.close()

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
