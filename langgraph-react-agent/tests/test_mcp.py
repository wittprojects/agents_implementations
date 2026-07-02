import asyncio
import json
from pathlib import Path

from agent.mcp import _load_config, _merge_configs, _prepare, load_mcp_tools


def test_missing_config_returns_empty(tmp_path):
    tools = asyncio.run(load_mcp_tools(tmp_path / "nope.json"))
    assert tools == []


def test_disabled_and_commented_servers_are_skipped(tmp_path):
    cfg = {
        "mcpServers": {
            "//_comment": {"transport": "stdio", "command": "x"},
            "disabled_one": {"transport": "stdio", "command": "x", "disabled": True},
        }
    }
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(cfg))
    assert _load_config(path) == {}
    # And loading yields no tools without contacting anything.
    assert asyncio.run(load_mcp_tools(path)) == []


def test_malformed_config_returns_empty(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text("{ not valid json")
    assert asyncio.run(load_mcp_tools(path)) == []


def test_prepare_injects_bearer_for_databricks_auth():
    cfg = {"transport": "streamable_http", "url": "https://x", "auth": "databricks"}
    prepared = _prepare(cfg, "tok123")
    assert prepared["headers"]["Authorization"] == "Bearer tok123"
    assert "auth" not in prepared


def test_prepare_no_token_no_header():
    cfg = {"transport": "streamable_http", "url": "https://x", "auth": "databricks"}
    prepared = _prepare(cfg, None)
    assert "headers" not in prepared or "Authorization" not in prepared.get("headers", {})


def test_shipped_config_parses(tmp_path):
    # The repo's example config ships with only disabled/commented servers.
    shipped = Path(__file__).resolve().parents[1] / "mcp_servers.json"
    assert _load_config(shipped) == {}


def test_merge_configs_local_wins(tmp_path):
    base = tmp_path / "mcp_servers.json"
    base.write_text(json.dumps({"mcpServers": {
        "a": {"transport": "stdio", "command": "x"},
        "b": {"transport": "stdio", "command": "y"},
    }}))
    local = tmp_path / "mcp_servers.local.json"
    local.write_text(json.dumps({"mcpServers": {
        "b": {"transport": "streamable_http", "url": "https://z"},
        "c": {"transport": "stdio", "command": "w"},
    }}))
    merged = _merge_configs([base, local, tmp_path / "missing.json"])
    assert set(merged) == {"a", "b", "c"}
    assert merged["b"]["transport"] == "streamable_http"  # local overrides base


def test_load_mcp_tools_accepts_list(tmp_path):
    # A list of (all missing) configs yields no tools and contacts nothing.
    assert asyncio.run(load_mcp_tools([tmp_path / "none1.json", tmp_path / "none2.json"])) == []
