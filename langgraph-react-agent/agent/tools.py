"""Built-in example tools that are always available to the agent.

Replace or extend these with your own tools. (For whole external toolsets, prefer
an MCP server entry in ``mcp_servers.json`` — no code change needed.)
"""

from __future__ import annotations

from datetime import datetime, timezone

from langchain_core.tools import tool


@tool
def current_time() -> str:
    """Return the current UTC date and time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


@tool
def add(a: float, b: float) -> float:
    """Add two numbers and return their sum."""
    return a + b


BUILTIN_TOOLS = [current_time, add]
