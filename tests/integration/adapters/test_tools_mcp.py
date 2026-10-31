"""Spec 005: the MCP ToolPort adapter (``pkpipe.adapters.tools_mcp``) against the real vault server."""

from __future__ import annotations

import sys

import pytest

from pkpipe.adapters.tools_mcp import McpTools, ToolCallError
from tests.support.vault_mcp import free_port, http_server, write_policy


@pytest.fixture
def vault_url(tmp_path, synthetic_vault):
    with http_server(write_policy(tmp_path, synthetic_vault), free_port()) as url:
        yield url


def test_streamable_http_calls_allowed_tools_and_refuses_others(vault_url):
    tools = McpTools(
        {
            "obsidian": {
                "transport": "streamable_http",
                "url": vault_url,
                "allow": ["list_folder", "read_note"],
            }
        }
    )
    listing = tools.call("obsidian:list_folder", {"path": "0-INBOX"})
    assert "example-tool.md" in listing["notes"]
    note = tools.call("obsidian:read_note", {"path": "0-INBOX/example-tool.md"})
    assert note["front_matter"]["source"] == "https://example.invalid/tool"
    with pytest.raises(PermissionError, match="not in the allow list"):
        tools.call("obsidian:write_note", {"path": "0-INBOX/x.md", "body": "x"})
    with pytest.raises(PermissionError, match="unregistered MCP server"):
        tools.call("web:search", {})
    assert tools.list_tools(["obsidian:read_note", "obsidian:write_note", "web:search"]) == [
        "obsidian:read_note"
    ]


def test_server_side_rejection_becomes_a_tool_error(vault_url):
    """The vault's own path policy still applies behind the allow list (defence in depth)."""
    tools = McpTools({"obsidian": {"transport": "streamable_http", "url": vault_url, "allow": ["read_note"]}})
    with pytest.raises(ToolCallError):
        tools.call("obsidian:read_note", {"path": "../outside.md"})


def test_stdio_transport(tmp_path, synthetic_vault):
    cfg = write_policy(tmp_path, synthetic_vault)
    server = {
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", "pkpipe.obsidian.server", "--config", str(cfg), "--transport", "stdio"],
        "allow": ["list_folder"],
    }
    assert (
        "example-video.md"
        in McpTools({"obsidian": server}).call("obsidian:list_folder", {"path": "0-INBOX"})["notes"]
    )
