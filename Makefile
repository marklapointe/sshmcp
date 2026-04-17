# Makefile for SSH MCP Agent
# Compatible with GNU Make and BMake

PYTHON = python3
PIP = $(PYTHON) -m pip
MODEL = llama3.2

.PHONY: all install run-server run-agent run-ui test test-cov clean help

all: install

help:
	@echo "Available targets:"
	@echo "  install      - Install dependencies and the project in editable mode"
	@echo "  run-server   - Run the MCP SSH Server directly"
	@echo "  run-agent    - Run the CLI Agent (requires QUERY, e.g. make run-agent QUERY=\"check uptime\")"
	@echo "  run-ui       - Run the Web UI (FastAPI)"
	@echo "  test         - Run unit tests"
	@echo "  test-cov     - Run unit tests with coverage report"
	@echo "  clean        - Remove build artifacts and cache"

install:
	$(PIP) install -e .
	$(PIP) install pytest pytest-asyncio pytest-cov httpx

run-server:
	$(PYTHON) -m ssh_mcp_agent.server

run-agent:
	@if [ -z "$(QUERY)" ]; then \
		echo "Usage: make run-agent QUERY=\"your query\""; \
		exit 1; \
	fi
	$(PYTHON) -m ssh_mcp_agent.agent "$(QUERY)" --model $(MODEL)

run-ui:
	$(PYTHON) -m ssh_mcp_agent.ui.app

test:
	PYTHONPATH=src pytest tests/ --asyncio-mode=auto

test-cov:
	PYTHONPATH=src pytest --cov=ssh_mcp_agent tests/ --cov-report=term-missing --asyncio-mode=auto

clean:
	rm -rf build/ dist/ *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
