#!/usr/bin/env python3
"""Entry point for the FundingRadar MCP server (stdio transport).

MCP clients point at this file with an absolute path:

    "command": "D:\\\\GIT\\\\fundingradar-mcp\\\\.venv\\\\Scripts\\\\python.exe",
    "args": ["D:\\\\GIT\\\\fundingradar-mcp\\\\server.py"]

Running it by hand starts a stdio MCP server: it waits for JSON-RPC on stdin and
prints nothing to stdout except protocol messages. Use tests/stdio_smoke_test.py
to verify it instead of typing at it.
"""

from __future__ import annotations

from fundingradar_mcp.app import main

if __name__ == "__main__":
    main()
