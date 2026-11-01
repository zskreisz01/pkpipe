"""Spec 014 FR-001/FR-002: ``pkpipe serve`` as a real process (API, UI, scheduler, app MCP, one listener)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest
from fastmcp import Client

from pkpipe.cli import main
from tests.support.app_stack import OWNER_TOKEN, make_overlay_repo
from tests.support.vault_mcp import free_port


def _lan_ip() -> str | None:
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    return None


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::"])
def test_serve_refuses_non_loopback_non_tailscale_addresses(host, capsys):
    """FR-001: wildcard / LAN binds are refused before anything starts."""
    assert main(["serve", "--host", host]) == 2
    assert "refusing to bind" in capsys.readouterr().err


def test_serve_refuses_a_weak_token_from_the_environment(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("PKPIPE_TOKEN", "short")
    assert main(["serve", "--db", str(tmp_path / "x.db"), "--port", str(free_port())]) == 2
    assert "at least" in capsys.readouterr().err


@pytest.fixture
def served(tmp_path, synthetic_vault):
    overlay = make_overlay_repo(tmp_path / "overlay")
    port = free_port()
    env = {**os.environ, "PKPIPE_TOKEN": OWNER_TOKEN, "PKPIPE_DATA_DIR": str(tmp_path / "data")}
    env.pop("PKPIPE_OVERLAY_DIR", None)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pkpipe.cli",
            "serve",
            "--port",
            str(port),
            "--overlay",
            str(overlay),
            "--vault",
            str(synthetic_vault),
            "--db",
            str(tmp_path / "data" / "app.db"),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("pkpipe serve exited: " + (proc.stdout.read() if proc.stdout else ""))
            with contextlib.suppress(httpx.HTTPError):
                if httpx.get(f"{url}/api/health", timeout=1).status_code == 200:
                    break
            time.sleep(0.2)
        else:
            raise RuntimeError("pkpipe serve did not start")
        yield proc, url, port, tmp_path
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=20)
        if proc.poll() is None:
            proc.kill()


def test_serve_serves_ui_api_and_mcp_on_loopback_only(served):
    proc, url, port, tmp_path = served
    auth = {"Authorization": f"Bearer {OWNER_TOKEN}"}
    assert httpx.get(f"{url}/api/runs", timeout=5).status_code == 401
    assert httpx.get(f"{url}/api/flows", headers=auth, timeout=5).json()["flows"]
    assert httpx.get(f"{url}/login", timeout=5).status_code == 200
    assert httpx.get(f"{url}/triage", timeout=5, follow_redirects=False).status_code == 303

    async def tools():
        async with Client(f"{url}/mcp", auth=OWNER_TOKEN) as c:
            return {t.name for t in await c.list_tools()}

    assert "confirm_item" in asyncio.run(tools())
    lan = _lan_ip()
    if lan:
        with socket.socket() as s:
            s.settimeout(1)
            assert s.connect_ex((lan, port)) != 0, "the UI must not listen on the LAN address"


def test_serve_starts_the_scheduler_and_shuts_down_gracefully_on_sigterm(served):
    proc, url, port, tmp_path = served
    import sqlite3

    db = tmp_path / "data" / "app.db"
    deadline = time.time() + 15
    jobs = []
    while time.time() < deadline and not jobs:
        with contextlib.suppress(sqlite3.Error):
            jobs = [r[0] for r in sqlite3.connect(db).execute("SELECT id FROM apscheduler_jobs")]
        time.sleep(0.2)
    assert {"flow:triage", "flow:process_released"} <= set(jobs)
    proc.send_signal(signal.SIGTERM)
    code = proc.wait(timeout=30)
    out = proc.stdout.read()
    assert code in (0, -signal.SIGTERM)  # uvicorn re-raises the signal after a graceful shutdown
    assert "Application shutdown complete" in out and "Traceback" not in out


def test_serve_without_vault_and_overlay_is_usable_but_read_only(tmp_path):
    port = free_port()
    env = {**os.environ, "PKPIPE_TOKEN": OWNER_TOKEN, "PKPIPE_DATA_DIR": str(tmp_path / "d")}
    env.pop("PKPIPE_OVERLAY_DIR", None)
    env.pop("PKPIPE_VAULT_DIR", None)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pkpipe.cli",
            "serve",
            "--port",
            str(port),
            "--no-scheduler",
            "--db",
            str(tmp_path / "d" / "app.db"),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(300):
            with contextlib.suppress(httpx.HTTPError):
                if httpx.get(f"{url}/api/health", timeout=1).status_code == 200:
                    break
            time.sleep(0.2)
        auth = {"Authorization": f"Bearer {OWNER_TOKEN}"}
        assert httpx.get(f"{url}/api/config/files", headers=auth).json()["editable"] is False
        assert httpx.post(f"{url}/api/drafts/x/publish", headers=auth, json={}).status_code in (404, 409)
        triage = httpx.get(f"{url}/api/triage", headers=auth)
        assert triage.status_code == 409 and "no vault" in json.dumps(triage.json())
    finally:
        proc.terminate()
        proc.wait(timeout=20)
