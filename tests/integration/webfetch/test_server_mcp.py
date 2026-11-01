"""Spec 008 FR-002: the web-fetch MCP server as a real process: only safe tools (no JS evaluate, click or
fill), the URL policy from the layered config incl. the pack deny-list, the test-only loopback allowance
from the environment only, and the ToolPort adapter with the pack's allow list."""

from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest
from fastmcp import Client

from pkpipe.adapters.tools_mcp import McpTools
from tests.support.fixture_site import fixture_site
from tests.support.webfetch_mcp import webfetch_server


@pytest.fixture(scope="module")
def site():
    with fixture_site() as s:
        yield s


def _tools(url: str) -> list[str]:
    async def go():
        async with Client(url) as c:
            return sorted(t.name for t in await c.list_tools())

    return asyncio.run(go())


def test_only_safe_tools_are_exposed():
    """Lightpanda's own MCP has evaluate/click/fill; this server exposes none of them."""
    with webfetch_server() as url:
        tools = _tools(url)
    assert tools == ["fetch_page", "fetch_stats"]
    assert not {"evaluate", "click", "fill", "goto"} & set(tools)


def test_private_and_deny_listed_urls_are_blocked_by_the_server(pack_dir):
    """The pack deny-list (security/deny_list.txt) and the private-network block apply in the server."""
    with webfetch_server() as url:
        tools = McpTools({"webfetch": {"transport": "streamable_http", "url": url, "allow": ["fetch_page"]}})
        assert tools.call("webfetch:fetch_page", {"url": "https://denied.example/x"})["reason"] == "deny_list"
        assert tools.call("webfetch:fetch_page", {"url": "http://169.254.169.254/"})["reason"] == (
            "private_network"
        )
        with pytest.raises(PermissionError):
            tools.call("webfetch:fetch_stats", {})  # not on the pack allow list


@pytest.mark.live("lightpanda")
def test_fetch_through_the_server_with_the_test_allowance(site, tmp_path):
    stats = tmp_path / "stats.jsonl"
    with webfetch_server(test_loopback=site.env_allowance, stats_file=stats) as url:
        tools = McpTools({"webfetch": {"transport": "streamable_http", "url": url, "allow": ["fetch_page"]}})
        res = tools.call("webfetch:fetch_page", {"url": site.url("/clean"), "max_bytes": 100_000})
        blocked = tools.call("webfetch:fetch_page", {"url": site.url("/redirect-private")})
        other = tools.call("webfetch:fetch_page", {"url": site.url("/redirect-other-port")})
    assert res["status"] == "ok" and res["fetcher"] == "lightpanda" and "vectorlite" in res["html"]
    assert blocked["status"] == "blocked" and other["status"] == "blocked"
    assert len(stats.read_text().splitlines()) == 3


def test_without_the_env_allowance_loopback_is_blocked(site):
    before = len(site.hits)
    with webfetch_server() as url:
        tools = McpTools({"webfetch": {"transport": "streamable_http", "url": url, "allow": ["fetch_page"]}})
        res = tools.call("webfetch:fetch_page", {"url": site.url("/clean")})
    assert res["status"] == "blocked" and res["reason"] == "private_network"
    assert len(site.hits) == before  # never reached the fixture server


@pytest.mark.parametrize("value", ["10.0.0.1:80", "example.org:443", "127.0.0.1"])
def test_server_refuses_a_non_loopback_allowance(value):
    proc = subprocess.run(
        [sys.executable, "-m", "pkpipe.webfetch.server", "--transport", "stdio"],
        env={"PKPIPE_WEBFETCH_TEST_LOOPBACK": value, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode != 0 and b"PKPIPE_WEBFETCH_TEST_LOOPBACK" in proc.stderr


def test_server_refuses_a_non_loopback_bind():
    proc = subprocess.run(
        [sys.executable, "-m", "pkpipe.webfetch.server", "--host", "0.0.0.0"],
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode != 0 and b"only listens on loopback" in proc.stderr
