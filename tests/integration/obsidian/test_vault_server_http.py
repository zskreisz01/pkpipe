"""Spec 004 FR-004: Streamable HTTP on 127.0.0.1 only, run as a real subprocess."""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys

from fastmcp import Client

from tests.support.vault_mcp import call, free_port, http_server, write_policy


def test_http_server_serves_tools_on_loopback_only(tmp_path, synthetic_vault):
    """FR-004: reachable on 127.0.0.1, bound to loopback, tools work and rejections hold over HTTP."""
    cfg = write_policy(tmp_path, synthetic_vault)
    port = free_port()
    with http_server(cfg, port) as url:

        async def go():
            async with Client(url) as c:
                return (
                    await call(c, "list_folder", path="0-INBOX"),
                    await call(c, "read_note", path="../x.md"),
                )

        (ok_err, listing), (bad_err, _) = asyncio.run(go())
        assert not ok_err and "example-video.md" in listing["notes"]
        assert bad_err
        # not reachable via a non-loopback address of this machine
        lan = _lan_ip()
        if lan:
            with socket.socket() as s:
                s.settimeout(1)
                assert s.connect_ex((lan, port)) != 0


def test_server_refuses_non_loopback_host(tmp_path, synthetic_vault):
    """FR-004: --host 0.0.0.0 (or any public address) aborts instead of listening."""
    cfg = write_policy(tmp_path, synthetic_vault)
    for host in ("0.0.0.0", "192.0.2.1", "example.com"):
        proc = subprocess.run(
            [sys.executable, "-m", "pkpipe.obsidian.server", "--config", str(cfg), "--host", host],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode != 0 and "loopback" in proc.stderr


def test_server_fails_clearly_on_bad_configuration(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "pkpipe.obsidian.server", "--vault", str(tmp_path / "missing")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0 and "does not exist" in proc.stderr
    proc = subprocess.run(
        [sys.executable, "-m", "pkpipe.obsidian.server"], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode != 0 and "vault_dir" in proc.stderr


def _lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None
