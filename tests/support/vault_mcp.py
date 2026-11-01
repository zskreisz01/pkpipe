"""Helpers that start the obsidian-vault MCP server as a real subprocess (stdio or Streamable HTTP)."""

from __future__ import annotations

import contextlib
import json
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from tests.support.ports import free_port  # noqa: F401 - re-exported: tests import it from here

SERVER_MODULE = "pkpipe.obsidian.server"


def write_policy(directory: Path, vault: Path, **extra: Any) -> Path:
    path = directory / "vault-policy.json"
    path.write_text(json.dumps({"vault_dir": str(vault), **extra}), encoding="utf-8")
    return path


@contextlib.asynccontextmanager
async def stdio_client(config: Path) -> AsyncIterator[Client]:
    transport = StdioTransport(
        sys.executable, ["-m", SERVER_MODULE, "--config", str(config), "--transport", "stdio"]
    )
    async with Client(transport) as client:
        yield client


@contextlib.contextmanager
def http_server(config: Path, port: int):
    proc = subprocess.Popen(
        [sys.executable, "-m", SERVER_MODULE, "--config", str(config), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("vault server exited early")
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("vault server did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
        if proc.poll() is None:
            proc.kill()


async def call(client: Client, tool: str, **args: Any) -> tuple[bool, Any]:
    """(is_error, data-or-message) so tests can assert both successes and rejections."""
    res = await client.call_tool(tool, args, raise_on_error=False)
    if res.is_error:
        return True, res.content[0].text
    return False, res.data
