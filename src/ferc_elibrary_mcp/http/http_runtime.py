from __future__ import annotations

import os
from typing import Any


def http_run_kwargs() -> dict[str, Any]:
    """Build FastMCP HTTP transport kwargs from environment."""
    transport = os.environ.get("FERC_MCP_TRANSPORT", "stdio").strip().lower()
    if transport not in {"http", "streamable-http", "sse"}:
        return {"transport": transport}

    kwargs: dict[str, Any] = {
        "transport": "streamable-http" if transport == "http" else transport,
        "host": os.environ.get("FERC_MCP_HOST", "0.0.0.0"),
        "port": int(os.environ.get("FERC_MCP_PORT", "8000")),
        "path": os.environ.get("FERC_MCP_PATH", "/mcp"),
    }
    return kwargs
