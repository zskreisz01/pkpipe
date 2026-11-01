"""Start the web-fetch MCP server (spec 008) as a real subprocess on Streamable HTTP."""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from tests.support.vault_mcp import free_port

SERVER_MODULE = "pkpipe.webfetch.server"


@contextlib.contextmanager
def webfetch_server(
    *,
    test_loopback: str | None = None,
    overlay: Path | None = None,
    stats_file: Path | None = None,
    timeout_s: float = 15,
    extra_env: dict[str, str] | None = None,
) -> Iterator[str]:
    port = free_port()
    env = {k: v for k, v in os.environ.items() if k != "PKPIPE_WEBFETCH_TEST_LOOPBACK"}
    if test_loopback:
        env["PKPIPE_WEBFETCH_TEST_LOOPBACK"] = test_loopback
    env.update(extra_env or {})
    args = [sys.executable, "-m", SERVER_MODULE, "--port", str(port), "--timeout", str(timeout_s)]
    if overlay:
        args += ["--overlay", str(overlay)]
    if stats_file:
        args += ["--stats-file", str(stats_file)]
    proc = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("web-fetch server exited: " + proc.stderr.read().decode()[-500:])
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("web-fetch server did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
        if proc.poll() is None:
            proc.kill()
