import asyncio
import json
import os
import sys
from typing import List, Dict, Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from rich.console import Console
from rich.panel import Panel
from rich.live import Live
from rich.markdown import Markdown

from .llm.client import OllamaClient, ToolCallingFormat, LLMResponse
from .config import ConfigManager
from .hosts import HostsManager

console = Console()

class SSHMCPAgent:
    """
    The orchestrator that connects the LLM Client to the MCP Server.
    """
    def __init__(self, model: Optional[str] = None, format: str = "auto", log_callback=None, env_overrides: Dict[str, str] = None, config_path: Optional[str] = None, system_message: Optional[str] = None, ollama_host: Optional[str] = None):
        self.config_manager = ConfigManager(config_path)
        self.hosts_manager = HostsManager(self.config_manager.settings.database_url, config_path)
        
        # Get default Ollama instance from database
        default_ollama = self.hosts_manager.get_default_ollama_instance()
        
        # Priority for Ollama Host: 1. Argument, 2. DB Default, 3. Hardcoded Fallback
        actual_host = ollama_host or (default_ollama.host if default_ollama else "http://localhost:11434")
        
        # Priority for Model: 1. Argument, 2. DB Default, 3. Hardcoded Fallback
        actual_model = model or (default_ollama.default_model if default_ollama else "llama3.2")

        self.llm = OllamaClient(
            model=actual_model, 
            format=ToolCallingFormat(format),
            host=actual_host
        )
        self.messages: List[Dict[str, str]] = []
        if system_message:
            self.messages.append({"role": "system", "content": system_message})
            
        self.log_callback = log_callback
        
        # Why we use StdioServerParameters:
        # This tells the client how to launch and communicate with our MCP server.
        env = os.environ.copy()
        if env_overrides:
            env.update(env_overrides)
            
        server_args = ["-m", "ssh_mcp_agent.server"]
        if config_path:
            server_args.extend(["--config", config_path])

        self.server_params = StdioServerParameters(
            command=sys.executable,
            args=server_args,
            env=env
        )

    async def run(self, query: str):
        """Main interaction loop."""
        self.messages.append({"role": "user", "content": query})
        
        async with stdio_client(self.server_params) as (read, write):
            async with ClientSession(read, write) as session:
                # 1. Initialize session
                await session.initialize()
                
                # 2. List tools from server
                mcp_tools = await session.list_tools()
                
                # Why we transform tools:
                # MCP tools have a specific structure, but Ollama expects a format 
                # similar to OpenAI's function calling.
                ollama_tools = []
                for tool in mcp_tools.tools:
                    ollama_tools.append({
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.inputSchema
                        }
                    })

                while True:
                    # 3. Call LLM
                    with console.status("[bold green]Thinking..."):
                        response = await self.llm.chat(self.messages, ollama_tools)

                    if response.content:
                        console.print(Panel(Markdown(response.content), title="Agent"))
                        self.messages.append({"role": "assistant", "content": response.content})

                    if not response.tool_calls:
                        break

                    # 4. Handle tool calls
                    for tool_call in response.tool_calls:
                        name = tool_call["name"]
                        args = tool_call["arguments"]
                        
                        if self.log_callback:
                            await self.log_callback({"type": "tool_call", "name": name, "arguments": args})
                        
                        # Why we execute via session:
                        # The session is our connection to the MCP server.
                        result = await session.call_tool(name, args)
                        
                        # MCP returns a list of content items
                        content_text = "\n".join([c.text for c in result.content if hasattr(c, 'text')])
                        
                        console.print(f"[bold yellow]Tool Result:[/bold yellow]\n{content_text}")
                        
                        if self.log_callback:
                            await self.log_callback({"type": "tool_result", "name": name, "content": content_text})

                        # Add tool result to message history
                        self.messages.append({
                            "role": "tool",
                            "name": name,
                            "content": content_text
                        })

                console.print("[bold green]Task complete![/bold green]")

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="SSH MCP Agent")
    parser.add_argument("query", help="User query for the agent")
    parser.add_argument("--model", help="Ollama model to use (overrides config)")
    parser.add_argument("--format", default="native", choices=["native", "json"], help="Tool calling format")
    parser.add_argument("--config", help="Path to configuration directory or file")
    
    args = parser.parse_args()
    
    agent = SSHMCPAgent(model=args.model, format=args.format, config_path=args.config)
    await agent.run(args.query)

if __name__ == "__main__":
    asyncio.run(main())
