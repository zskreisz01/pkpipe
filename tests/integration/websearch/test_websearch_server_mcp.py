"""Spec 017: the web-search MCP server as a real process: one tool, the pack deny-list and the private-network
block applied to results, and the ToolPort adapter with the pack's allow list (`websearch:search`)."""

from __future__ import annotations

import asyncio

from fastmcp import Client

from pkpipe.adapters.tools_mcp import McpTools
from tests.support.websearch_server import stub_searxng, websearch_server


def test_only_search_is_exposed_and_results_are_filtered(pack_dir):
    with stub_searxng() as (sx_url, queries), websearch_server(sx_url) as url:

        async def tools():
            async with Client(url) as c:
                return sorted(t.name for t in await c.list_tools())

        assert asyncio.run(tools()) == ["search"]
        mcp = McpTools({"websearch": {"transport": "streamable_http", "url": url, "allow": ["search"]}})
        out = mcp.call("websearch:search", {"query": "pkpipe test", "max_results": 5})
    assert [r["url"] for r in out["results"]] == ["https://example.org/good"]
    assert out["results"][0]["snippet"] == "A good result"
    assert any("q=pkpipe+test" in q and "format=json" in q for q in queries)


def test_backend_down_is_an_error_code(pack_dir):
    with websearch_server("http://127.0.0.1:9") as url:
        mcp = McpTools({"websearch": {"transport": "streamable_http", "url": url, "allow": ["search"]}})
        out = mcp.call("websearch:search", {"query": "x"})
    assert out["results"] == [] and out["error"] in {"unavailable", "timeout"}
