# SSH MCP Agent with Ollama Control Center

An extensible Model Context Protocol (MCP) Agent that can SSH into systems, execute commands, and transfer files. Powered by Ollama with dynamic tool-calling compatibility for a wide range of LLMs.

## 🚀 Overview

This project implements a complete Agentic AI system consisting of:
1.  **MCP SSH Server**: Exposes SSH capabilities (exec, upload, download) via the Model Context Protocol.
2.  **Extensible LLM Client**: A dynamic client for Ollama that auto-detects and supports multiple tool-calling formats (Native, JSON, XML).
3.  **Agent Orchestrator**: The "brain" that connects the LLM to the MCP tools.
4.  **Web Control Center**: A modern, split-pane Web UI to interact with the agent and monitor tool execution in real-time.

---

## 🛠 Design Choices & Implementation Details

### 1. Extensible MCP Architecture
We use the official `mcp` Python SDK. The server (`src/ssh_mcp_agent/server.py`) is decoupled from the agent. It uses `paramiko` for robust SSH/SFTP operations.
*   **Why?** By using MCP, this server can be used by *any* MCP-compliant client (like Claude Desktop or other agents), not just our own.

### 2. Dynamic LLM Client (Ollama)
Different models have different strengths in tool calling. Our `OllamaClient` (`src/ssh_mcp_agent/llm/client.py`) supports:
*   **Native**: Uses Ollama's built-in `/chat` tools support (best for Llama 3.1/3.2, Qwen 2.5).
*   **JSON**: Injects tool definitions into the system prompt and forces JSON output (best for smaller models).
*   **XML**: Uses `<tool_call>` tags, which are often more reliable for models like Mistral.
*   **Auto-detection**: Automatically inspects the model's template to choose the best format.

### 3. Real-time Web UI
Built with **FastAPI** and **Tailwind CSS**. It uses **Server-Sent Events (SSE)** to stream tool execution logs from the background agent to the browser.
*   **Split Layout**: Chat on the left, "Raw" execution logs on the right. This is designed for learning, allowing you to see exactly how the LLM decides to call tools and the raw output it receives.

---

## 📥 Installation

1.  **Prerequisites**:
    *   Python 3.10+
    *   [Ollama](https://ollama.com/) installed and running.
    *   At least one tool-capable model (e.g., `ollama pull llama3.2`).

2.  **Install**:
    ```bash
    make install
    ```

---

## 🏃 Running the Project

### Using the Web UI (Recommended)
```bash
make run-ui
```
Open [http://localhost:8000](http://localhost:8000) in your browser.

### Using the CLI Agent
```bash
make run-agent QUERY="Check the disk usage on localhost"
```

### Using IntelliJ / PyCharm
We have provided pre-configured run configurations in `.idea/runConfigurations/`:
*   **Web UI**: Starts the FastAPI server.
*   **CLI Agent**: Runs a sample query.
*   **SSH Server**: Runs the MCP server standalone (for testing with other clients).

---

## 📁 Project Structure

*   `src/ssh_mcp_agent/`
    *   `llm/`: Dynamic Ollama client logic.
    *   `tools/`: SSH and SFTP implementations.
    *   `ui/`: FastAPI app and Web Control Center assets.
    *   `server.py`: The MCP Server entry point.
    *   `agent.py`: The Orchestrator logic.
*   `Makefile`: Cross-platform build/run commands (GNU/BMake).
*   `pyproject.toml`: Project metadata and dependencies.

---

## 🎓 Learning from this Project
*   **Observe the Logs**: Use the Web UI to see the difference between what the LLM says and what the tools actually do.
*   **Try Different Models**: Swap `llama3.2` for `qwen2.5-coder` or `mistral` and watch how the "Protocol" auto-switches in the logs.
*   **Extend the Tools**: Add new capabilities in `src/ssh_mcp_agent/tools/` and register them in `server.py`.
