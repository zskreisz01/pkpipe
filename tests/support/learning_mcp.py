"""Start the learning-gateway MCP server as a real subprocess (stdio or Streamable HTTP), ``fake`` backend."""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tests.support.vault_mcp import free_port

SERVER_MODULE = "pkpipe.learning.server"


def write_overlay(directory: Path, *, backend: str = "fake", **options: Any) -> Path:
    """An overlay that selects the backend and its options (the only thing an owner changes to switch)."""
    overlay = directory / "learning-overlay"
    overlay.mkdir(parents=True, exist_ok=True)
    entry = {"backend": backend, "options": {"data_dir": str(directory / "learning-data"), **options}}
    (overlay / "mcp_servers.json").write_text(json.dumps({"learning": entry}), encoding="utf-8")
    return overlay


def stdio_server_config(overlay: Path) -> dict[str, Any]:
    """An ``mcp_servers.json`` style entry that McpTools can start (stdio)."""
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", SERVER_MODULE, "--overlay", str(overlay), "--transport", "stdio"],
        "allow": ["create", "status", "link", "audio"],
    }


@contextlib.contextmanager
def http_server(overlay: Path):
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", SERVER_MODULE, "--overlay", str(overlay), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        import socket

        deadline = time.time() + 40
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"learning gateway exited early: {proc.stderr.read().decode()[-500:]}")
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("learning gateway did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
        if proc.poll() is None:
            proc.kill()
