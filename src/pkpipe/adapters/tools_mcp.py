"""ToolPort over MCP servers from ``mcp_servers.json`` (Streamable HTTP or stdio, FastMCP client).

Tool names are ``<server>:<tool>``. A tool that is not on its server's ``allow`` list is refused before any
connection is made. Each call opens a short client session, which keeps the adapter synchronous and
thread safe for parallel flow items.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Mapping
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

from pkpipe.adapters.model_openai import resolve_value


class ToolCallError(RuntimeError):
    """The MCP server reported an error for a tool call."""


class McpTools:
    def __init__(self, servers: Mapping[str, Mapping[str, Any]], *, timeout_s: float = 120, environ=None):
        import os

        self.servers = {k: dict(v) for k, v in servers.items()}
        self.timeout_s = timeout_s
        self.environ = os.environ if environ is None else environ

    def _split(self, tool: str) -> tuple[dict[str, Any], str]:
        server, sep, name = tool.partition(":")
        if not sep or server not in self.servers:
            raise PermissionError(f"tool '{tool}' references unregistered MCP server '{server}'")
        cfg = self.servers[server]
        if name not in (cfg.get("allow") or []):
            raise PermissionError(f"tool '{tool}' is not in the allow list of '{server}'")
        return cfg, name

    def list_tools(self, allow: list[str]) -> list[str]:
        out = []
        for tool in allow:
            try:
                self._split(tool)
            except PermissionError:
                continue
            out.append(tool)
        return sorted(out)

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        cfg, name = self._split(tool)
        coro = self._call(cfg, name, arguments)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        with concurrent.futures.ThreadPoolExecutor(1) as pool:  # called from async code: own loop
            return pool.submit(asyncio.run, coro).result()

    async def _call(self, cfg: dict[str, Any], name: str, arguments: dict[str, Any]) -> Any:
        if cfg["transport"] == "stdio":
            transport: Any = StdioTransport(cfg["command"], list(cfg.get("args") or []))
        else:
            transport = StreamableHttpTransport(resolve_value(cfg["url"], self.environ))
        async with Client(transport, timeout=self.timeout_s) as client:
            res = await client.call_tool(name, arguments, raise_on_error=False)
        if res.is_error:
            text = " ".join(getattr(c, "text", "") for c in res.content)
            raise ToolCallError(f"{name}: {text}"[:1000])
        if res.data is not None:
            return res.data
        if res.structured_content is not None:
            return res.structured_content
        return " ".join(getattr(c, "text", "") for c in res.content)
