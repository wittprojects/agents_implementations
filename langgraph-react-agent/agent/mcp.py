"""MCP servers: drop-in external toolsets.

Reads ``mcp_servers.json`` and loads every enabled server's tools via
``langchain-mcp-adapters``' ``MultiServerMCPClient``, merging them into the
agent's tool list. Adding a toolset requires **no code change** — add an entry to
the JSON config.

Config format (``mcpServers`` keyed by name)::

    {
      "mcpServers": {
        "my_http_server": {
          "transport": "streamable_http",
          "url": "https://.../mcp",
          "auth": "databricks",        # optional: inject a Bearer token
          "disabled": false
        },
        "my_local_server": {
          "transport": "stdio",
          "command": "python",
          "args": ["-m", "my_mcp_server"]
        }
      }
    }

HTTP transports are the deployable default; a ``stdio`` server needs its binary
present in the App container. Entries with ``"disabled": true`` or whose name
starts with ``//`` (comment convention) are skipped. Loading never raises — a bad
or unreachable server is logged and skipped so it can't break agent startup.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List

from langchain_core.tools import BaseTool, StructuredTool

logger = logging.getLogger(__name__)


def _as_text(result) -> str:
    """Flatten an MCP tool result (which may be a list of structured content blocks)
    to plain text."""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = []
        for block in result:
            if isinstance(block, dict):
                parts.append(block.get("text") or json.dumps(block, default=str))
            else:
                parts.append(getattr(block, "text", None) or str(block))
        return "\n".join(parts)
    return str(result)


def _text_only(tool: BaseTool) -> BaseTool:
    """Wrap an MCP tool so its result is a plain-text string.

    MCP servers can return structured content blocks (each carrying an ``id`` /
    annotations). Some chat APIs (e.g. Claude via Databricks serving) reject those
    extra fields inside a tool_result block ("Extra inputs are not permitted"), so we
    normalize the output to text before it re-enters the model conversation.
    """

    async def _arun(**kwargs):
        return _as_text(await tool.ainvoke(kwargs))

    return StructuredTool.from_function(
        coroutine=_arun,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
    )


def _load_config(config_path: Path) -> dict:
    if not config_path.exists():
        logger.info("no MCP config at %s; skipping MCP tools", config_path)
        return {}
    try:
        raw = json.loads(config_path.read_text())
    except Exception:
        logger.exception("failed to parse MCP config %s; skipping MCP tools", config_path)
        return {}
    servers = raw.get("mcpServers") or raw.get("servers") or {}
    return {
        name: cfg
        for name, cfg in servers.items()
        if isinstance(cfg, dict) and not cfg.get("disabled") and not name.startswith(("//", "_"))
    }


def _prepare(cfg: dict, bearer_token: str | None) -> dict:
    cfg = dict(cfg)
    if cfg.pop("auth", None) == "databricks" and bearer_token:
        headers = dict(cfg.get("headers", {}))
        headers.setdefault("Authorization", f"Bearer {bearer_token}")
        cfg["headers"] = headers
    cfg.pop("disabled", None)
    return cfg


def _merge_configs(paths) -> dict:
    """Merge enabled servers from several config files; later files win on name."""
    merged: dict = {}
    for p in paths:
        merged.update(_load_config(Path(p)))
    return merged


async def load_mcp_tools(config_path, *, bearer_token: str | None = None) -> List[BaseTool]:
    # Accept a single path or a list of paths (base config + optional local override).
    paths = config_path if isinstance(config_path, (list, tuple)) else [config_path]
    servers = _merge_configs(paths)
    if not servers:
        return []

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        logger.warning("langchain-mcp-adapters not installed; skipping MCP tools")
        return []

    tools: List[BaseTool] = []
    # Load each server independently so one failure doesn't drop the others.
    for name, cfg in servers.items():
        try:
            client = MultiServerMCPClient({name: _prepare(cfg, bearer_token)})
            server_tools = await client.get_tools()
            tools.extend(_text_only(t) for t in server_tools)
            logger.info("loaded %d tool(s) from MCP server '%s'", len(server_tools), name)
        except Exception:
            logger.exception("failed to load MCP server '%s'; skipping", name)
    return tools
