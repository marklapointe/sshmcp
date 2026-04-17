import asyncio
import os
from typing import Any, Dict, List, Optional

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

# Why we are using a Server class:
# The MCP Server acts as a bridge between the LLM and the local/remote tools.
# It defines what tools are available and how to execute them.
app = Server("ssh-mcp-server")

# Define input schemas for our tools using Pydantic for validation.
class SSHExecuteArgs(BaseModel):
    host: str = Field(..., description="Remote host address")
    username: str = Field(..., description="SSH username")
    command: str = Field(..., description="Command to execute")
    password: Optional[str] = Field(None, description="SSH password (optional if key is used)")
    key_filename: Optional[str] = Field(None, description="Path to SSH private key file (optional)")
    port: int = Field(22, description="SSH port (default: 22)")

class SSHTransferArgs(BaseModel):
    host: str = Field(..., description="Remote host address")
    username: str = Field(..., description="SSH username")
    local_path: str = Field(..., description="Path on local machine")
    remote_path: str = Field(..., description="Path on remote machine")
    password: Optional[str] = Field(None, description="SSH password (optional if key is used)")
    key_filename: Optional[str] = Field(None, description="Path to SSH private key file (optional)")
    port: int = Field(22, description="SSH port (default: 22)")

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
            config = SSHConfig(
                host=args.host,
                username=args.username,
                password=args.password,
                key_filename=args.key_filename,
                port=args.port
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
            config = SSHConfig(
                host=args.host,
                username=args.username,
                password=args.password,
                key_filename=args.key_filename,
                port=args.port
            )
            client = SSHClient(config)
            try:
                client.upload_file(args.local_path, args.remote_path)
                return [TextContent(type="text", text=f"Successfully uploaded {args.local_path} to {args.remote_path}")]
            finally:
                client.close()

        elif name == "ssh_download":
            args = SSHTransferArgs(**arguments)
            config = SSHConfig(
                host=args.host,
                username=args.username,
                password=args.password,
                key_filename=args.key_filename,
                port=args.port
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
