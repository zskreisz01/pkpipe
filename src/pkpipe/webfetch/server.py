"""The web-fetch MCP server (spec 008 FR-002): one safe tool instead of a browser remote control.

``lightpanda mcp`` exposes about 30 tools, including ``evaluate`` (run JavaScript), ``click`` and ``fill``.
A model that can call those can be steered by a page into acting on it. This server exposes only:

* ``fetch_page(url, max_bytes)``: load the URL (Lightpanda, Chromium fallback) behind the filtering proxy and
  return the rendered HTML with its metadata (a ``FetchResult``);
* ``fetch_stats()``: counters incl. the Lightpanda fallback rate (SC-002).

The URL policy comes from the layered config (``security.url_filter`` and its deny-list file), re-read for
every fetch so a deny-list change applies at once. It listens on loopback only.

Run: ``python -m pkpipe.webfetch.server`` (Streamable HTTP on 127.0.0.1:8704) or ``--transport stdio``.

Test-only: ``PKPIPE_WEBFETCH_TEST_LOOPBACK=127.0.0.1:<port>`` lets the test suite fetch from its local
fixture server. Only loopback IP literals with a port are accepted, it is logged at startup, and no config
file can set it.
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import sys
from pathlib import Path

from fastmcp import FastMCP

from pkpipe.engine.registry import load_effective
from pkpipe.security.urlfilter import UrlPolicy, parse_test_loopback
from pkpipe.webfetch.browsers import ChromiumBrowser, LightpandaBrowser
from pkpipe.webfetch.service import SecureFetcher

DEFAULT_PORT = 8704
TEST_LOOPBACK_ENV = "PKPIPE_WEBFETCH_TEST_LOOPBACK"
TOOLS = ("fetch_page", "fetch_stats")
log = logging.getLogger("pkpipe.webfetch")


def policy_loader(pack: Path | None, overlay: Path | None, test_loopback: frozenset[tuple[str, int]]):
    def load() -> UrlPolicy:
        cfg = load_effective(pack, overlay)
        url_filter = (cfg.data.get("security") or {}).get("url_filter") or {}
        deny = url_filter.get("deny_list")
        deny_path = cfg.find_file(deny) if deny else None
        deny_text = deny_path.read_text(encoding="utf-8") if deny_path else None
        return UrlPolicy.from_config(url_filter, deny_text, test_loopback=test_loopback)

    return load


def build_server(load_policy, *, timeout_s: float = 30.0, stats_file: Path | None = None, browsers=None):
    primary, fallback = browsers or (LightpandaBrowser(), ChromiumBrowser())
    fetcher = SecureFetcher(
        load_policy(), primary=primary, fallback=fallback, timeout_s=timeout_s, stats_file=stats_file
    )
    state = {"fetcher": fetcher}
    mcp = FastMCP("web-fetch")

    @mcp.tool
    def fetch_page(url: str, max_bytes: int | None = None) -> dict:
        """Fetch one http(s) URL as rendered HTML with status, content type and kind. Private networks,
        deny-listed domains and oversized pages are blocked; non-HTML content returns metadata only."""
        fetcher = state["fetcher"]
        fetcher.policy = load_policy()
        return fetcher.fetch(url, max_bytes)

    @mcp.tool
    def fetch_stats() -> dict:
        """Counters of this server process: fetches, outcomes, fetcher used, fallback rate."""
        return state["fetcher"].stats_summary()

    return mcp


def _check_loopback(host: str) -> str:
    try:
        ok = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = False
    if not ok:
        raise SystemExit(f"refusing to bind {host!r}: the web-fetch server only listens on loopback")
    return host


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pkpipe-web-fetch")
    ap.add_argument("--pack", type=Path, help="pack directory (default: public pack or PKPIPE_PACK_DIR)")
    ap.add_argument("--overlay", type=Path, help="overlay directory (default: PKPIPE_OVERLAY_DIR)")
    ap.add_argument("--transport", choices=["http", "stdio"], default="http")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--timeout", type=float, default=30.0, help="seconds per browser attempt")
    ap.add_argument("--stats-file", type=Path, help="append one JSON line per fetch (no URLs)")
    args = ap.parse_args(argv)
    host = _check_loopback(args.host)
    try:
        test_loopback = parse_test_loopback(os.environ.get(TEST_LOOPBACK_ENV))
    except ValueError as exc:
        raise SystemExit(f"{TEST_LOOPBACK_ENV}: {exc}") from None
    if test_loopback:
        print(f"web-fetch: TEST-ONLY loopback allowance active: {sorted(test_loopback)}", file=sys.stderr)
    load = policy_loader(args.pack, args.overlay, test_loopback)
    try:
        load()
    except Exception as exc:  # noqa: BLE001 - a broken config must stop the server at startup
        raise SystemExit(f"invalid configuration: {exc}") from None
    server = build_server(load, timeout_s=args.timeout, stats_file=args.stats_file)
    if args.transport == "stdio":
        server.run(transport="stdio", show_banner=False)
    else:
        server.run(
            transport="http", host=host, port=args.port, show_banner=False, host_origin_protection=True
        )


if __name__ == "__main__":
    main(sys.argv[1:])
