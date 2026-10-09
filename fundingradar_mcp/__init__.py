"""Read-only MCP server for the FundingRadar API.

The package is layered so that everything except :mod:`fundingradar_mcp.client` runs
without a network:

``config``
    Environment -> API base URL, token and timeout, with strict validation.
``client``
    HTTP access to api/v1, plus the error mapping that turns an HTTP status into a
    message an agent can act on.
``service``
    One method per MCP tool: calls the endpoints and shapes the payloads.
``app``
    MCP wiring: tool definitions, instructions and the stdio entry point.

The server never touches the database. It holds a personal API token, so what a
colleague can read is what their own account can read, and revoking the token in the web
application takes effect immediately.
"""

from __future__ import annotations

__version__ = "0.2.0"
