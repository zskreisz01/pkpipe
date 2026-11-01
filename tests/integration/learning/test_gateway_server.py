"""Spec 012 FR-001, FR-002, FR-003, US2: the learning-gateway MCP server as a real process.

The backend is chosen by overlay configuration (``fake`` here); the four tools are called through the app's
``McpTools`` adapter, exactly as flows do.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from pkpipe.adapters.tools_mcp import McpTools, ToolCallError
from tests.support.learning_mcp import (
    SERVER_MODULE,
    http_server,
    stdio_server_config,
    write_overlay,
)

URL = "https://example.invalid/talk"


@pytest.fixture
def tools(tmp_path):
    """One server process (the fake backend counts polls in memory), reached over Streamable HTTP."""
    overlay = write_overlay(tmp_path, backend="fake", backend_options={"ready_after": 2, "fail": ["bad"]})
    with http_server(overlay) as url:
        allow = ["create", "status", "link", "audio"]
        yield McpTools({"learning": {"transport": "streamable_http", "url": url, "allow": allow}}), tmp_path


def test_four_tools_run_the_ready_path(tools):
    """FR-001: create(url, template), status(job_id), link(nb_id), audio(job_id)."""
    t, tmp_path = tools
    job = t.call("learning:create", {"url": URL, "template": "ten minutes", "item_id": "item1"})
    assert job["status"] == "generating" and job["backend"] == "fake"
    assert t.call("learning:status", {"job_id": job["job_id"]})["status"] == "generating"
    assert t.call("learning:status", {"job_id": job["job_id"]})["status"] == "ready"
    link = t.call("learning:link", {"nb_id": job["nb_id"]})
    assert link["url"].startswith("https://notebook.example.invalid/")
    audio = t.call("learning:audio", {"job_id": job["job_id"]})
    assert Path(audio["audio_path"]).read_bytes().startswith(b"ID3")
    assert str(tmp_path / "learning-data" / "audio") in audio["audio_path"]


def test_the_job_store_outlives_the_server_process(tmp_path):
    """A gateway restart keeps its jobs: a stdio server is a new process per call and still finds the job."""
    overlay = write_overlay(tmp_path, backend_options={"ready_after": 1})
    t = McpTools({"learning": stdio_server_config(overlay)}, timeout_s=60)
    job = t.call("learning:create", {"url": URL, "template": "", "item_id": "item1"})
    assert t.call("learning:status", {"job_id": job["job_id"]})["status"] == "ready"
    assert t.call("learning:link", {"nb_id": job["nb_id"]})["nb_id"] == job["nb_id"]


def test_failure_reason_comes_back_through_the_tool(tools):
    t, _ = tools
    job = t.call("learning:create", {"url": URL, "template": "", "item_id": "bad"})
    res = t.call("learning:status", {"job_id": job["job_id"]})
    assert res["status"] == "failed" and res["reason"] == "scripted failure"


@pytest.mark.parametrize(
    ("tool", "args", "message"),
    [
        ("create", {"url": "http://127.0.0.1:8080/admin", "template": ""}, "url_blocked: private_network"),
        ("create", {"url": "file:///etc/passwd", "template": ""}, "url_blocked: scheme_not_allowed"),
        ("create", {"url": URL, "template": "", "item_id": "../../etc"}, "item_id may only contain"),
        ("status", {"job_id": "nope"}, "unknown job"),
        ("link", {"nb_id": "nope"}, "unknown notebook"),
        ("audio", {"job_id": "nope"}, "unknown job"),
    ],
)
def test_bad_input_is_a_tool_error_not_a_crash(tools, tool, args, message):
    """Constitution IV and V: the gateway refuses what it must not forward."""
    t, _ = tools
    with pytest.raises(ToolCallError, match=message):
        t.call(f"learning:{tool}", args)


def test_the_app_allow_list_still_applies_to_the_learning_server(tmp_path):
    overlay = write_overlay(tmp_path)
    cfg = {**stdio_server_config(overlay), "allow": ["create", "status", "link"]}  # no audio
    t = McpTools({"learning": cfg}, timeout_s=60)
    with pytest.raises(PermissionError, match="not in the allow list"):
        t.call("learning:audio", {"job_id": "x"})


def test_create_is_idempotent_per_item_so_a_repeated_run_makes_no_second_notebook(tools):
    t, _ = tools
    first = t.call("learning:create", {"url": URL, "template": "", "item_id": "item1"})
    assert (
        t.call("learning:create", {"url": URL, "template": "", "item_id": "item1"})["job_id"]
        == first["job_id"]
    )


def run_server(*args: str, **env):
    import os

    return subprocess.run(
        [sys.executable, "-m", SERVER_MODULE, *args],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, **env},
    )


def test_server_refuses_to_listen_on_a_non_loopback_host(tmp_path):
    overlay = write_overlay(tmp_path)
    r = run_server("--overlay", str(overlay), "--host", "0.0.0.0")
    assert r.returncode != 0 and "only listens on loopback" in r.stderr


def test_bad_configuration_is_reported_clearly(tmp_path):
    overlay = write_overlay(tmp_path, timeout_s=-1)
    r = run_server("--overlay", str(overlay), "--transport", "stdio")
    assert (
        r.returncode != 0 and "invalid configuration" in r.stderr and "timeout_s must be positive" in r.stderr
    )
    overlay2 = write_overlay(tmp_path / "b", backend="nope")
    r2 = run_server("--overlay", str(overlay2), "--transport", "stdio")
    assert "unknown learning backend 'nope'" in r2.stderr
