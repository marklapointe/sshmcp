import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Protocol
from enum import Enum
import ollama
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class ToolCallingFormat(Enum):
    AUTO = "auto"           # Auto-detect based on model
    NATIVE = "native"       # Ollama native tool calling
    JSON = "json"           # Structured JSON output
    XML = "xml"             # XML tags (often better for models like Claude/Mistral)

class LLMResponse(BaseModel):
    content: str
    tool_calls: List[Dict[str, Any]] = []

class LLMClient(Protocol):
    """Protocol for LLM clients to ensure extensibility."""
    async def chat(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]]) -> LLMResponse:
        ...  # pragma: no cover

class OllamaClient:
    """
    A dynamic Ollama client that supports multiple tool-calling formats and auto-detection.
    """
    def __init__(
        self, 
        model: str = "llama3.2", 
        format: ToolCallingFormat = ToolCallingFormat.AUTO,
        host: Optional[str] = None
    ):
        self.model = model
        self.format = format
        # Use provided host, or OLLAMA_HOST env var, or default to localhost
        actual_host = host or os.getenv("OLLAMA_HOST") or "http://localhost:11434"
        self.client = ollama.AsyncClient(host=actual_host)
        self._detected_format: Optional[ToolCallingFormat] = None

    async def _detect_format(self) -> ToolCallingFormat:
        """
        Inspects the model to decide the best tool-calling format.
        """
        if self.format != ToolCallingFormat.AUTO:
            return self.format
        
        if self._detected_format:
            return self._detected_format

        try:
            info = await self.client.show(self.model)
            template = getattr(info, "template", "")
            
            # Check for native tool support indicators in template
            if ".Tools" in template or ".ToolCalls" in template:
                self._detected_format = ToolCallingFormat.NATIVE
                logger.info(f"Auto-detected NATIVE format for {self.model}")
            elif "mistral" in self.model.lower():
                self._detected_format = ToolCallingFormat.XML
                logger.info(f"Auto-detected XML format for {self.model}")
            else:
                self._detected_format = ToolCallingFormat.JSON
                logger.info(f"Auto-detected JSON format for {self.model}")
        except Exception as e:
            logger.warning(f"Failed to detect format for {self.model}, defaulting to JSON: {e}")
            self._detected_format = ToolCallingFormat.JSON
            
        return self._detected_format

    async def chat(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]]) -> LLMResponse:
        """
        Sends a chat request to Ollama using the best available format.
        """
        fmt = await self._detect_format()
        
        if fmt == ToolCallingFormat.NATIVE:
            return await self._chat_native(messages, tools)
        elif fmt == ToolCallingFormat.JSON:
            return await self._chat_json(messages, tools)
        elif fmt == ToolCallingFormat.XML:
            return await self._chat_xml(messages, tools)
        else:
            raise NotImplementedError(f"Format {fmt} not implemented")

    async def _chat_native(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]]) -> LLMResponse:
        response = await self.client.chat(
            model=self.model,
            messages=messages,
            tools=tools
        )
        
        message = response.get("message", {})
        content = message.get("content", "")
        tool_calls = []
        
        if "tool_calls" in message:
            for tc in message["tool_calls"]:
                tool_calls.append({
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"]
                })
        
        return LLMResponse(content=content, tool_calls=tool_calls)

    async def _chat_json(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]]) -> LLMResponse:
        tools_desc = json.dumps(tools, indent=2)
        system_prompt = (
            "You are an assistant with access to tools. "
            "If you need to use a tool, respond with a JSON object in this format:\n"
            "{\n  \"tool_calls\": [\n    {\"name\": \"tool_name\", \"arguments\": { ... }}\n  ]\n}\n"
            f"Available tools:\n{tools_desc}\n"
            "If no tool is needed, respond with normal text."
        )
        
        modified_messages = [{"role": "system", "content": system_prompt}] + messages
        
        response = await self.client.chat(
            model=self.model,
            messages=modified_messages,
            format="json"
        )
        
        content = response["message"]["content"]
        try:
            data = json.loads(content)
            tool_calls = data.get("tool_calls", [])
            return LLMResponse(content="", tool_calls=tool_calls)
        except json.JSONDecodeError:
            return LLMResponse(content=content, tool_calls=[])

    async def _chat_xml(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]]) -> LLMResponse:
        # Some models prefer XML-like tags for tool calls.
        tools_desc = json.dumps(tools, indent=2)
        system_prompt = (
            "You are an assistant with access to tools. "
            "To call a tool, use the following format:\n"
            "<tool_call>\n{\"name\": \"tool_name\", \"arguments\": { ... }}\n</tool_call>\n"
            f"Available tools:\n{tools_desc}\n"
            "Respond with normal text if no tool is needed."
        )
        
        modified_messages = [{"role": "system", "content": system_prompt}] + messages
        
        response = await self.client.chat(
            model=self.model,
            messages=modified_messages
        )
        
        content = response["message"]["content"]
        tool_calls = []
        
        # Regex to find <tool_call> tags
        matches = re.findall(r"<tool_call>(.*?)</tool_call>", content, re.DOTALL)
        if matches:
            for match in matches:
                try:
                    call_data = json.loads(match.strip())
                    tool_calls.append(call_data)
                except json.JSONDecodeError:
                    continue
            return LLMResponse(content=re.sub(r"<tool_call>.*?</tool_call>", "", content, flags=re.DOTALL).strip(), tool_calls=tool_calls)
        
        return LLMResponse(content=content, tool_calls=[])
