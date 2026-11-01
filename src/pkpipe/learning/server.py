"""FastMCP server of the learning gateway (spec 012 FR-001): ``create``, ``status``, ``link``, ``audio``.

Run: ``python -m pkpipe.learning.server [--pack DIR] [--overlay DIR]`` (Streamable HTTP on 127.0.0.1:8703) or
``--transport stdio``. The backend, the timeout and the URL filter come from the effective config
(``mcp_servers.json`` ``learning`` entry and ``security.json``); only loopback hosts are accepted.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .config import GatewaySettings
from .errors import LearningError
from .gateway import LearningGateway, LearningMetrics

DEFAULT_PORT = 8703
_ITEM_ID = re.compile(r"[A-Za-z0-9_-]{0,64}")


def _run(fn: Callable[..., Any], *args: Any) -> Any:
    try:
        return fn(*args)
    except LearningError as exc:
        raise ToolError(str(exc)) from None


def build_server(gateway: LearningGateway) -> FastMCP:
    mcp = FastMCP("learning-gateway")

    @mcp.tool
    def create(url: str, template: str = "", item_id: str = "") -> dict:
        """Create a notebook from a URL and start the podcast generation. Only the URL and the prompt
        template reach the backend. Returns job_id, nb_id and status (generating)."""
        if not _ITEM_ID.fullmatch(item_id):
            raise ToolError("item_id may only contain letters, digits, '-' and '_' (max 64)")
        return _run(gateway.create, url, template, item_id)

    @mcp.tool
    def status(job_id: str) -> dict:
        """Status of a generation job: generating, ready or failed (with the failure reason)."""
        return _run(gateway.status, job_id)

    @mcp.tool
    def link(nb_id: str) -> dict:
        """URL of the notebook, to open in the browser or the backend's app."""
        return _run(gateway.link, nb_id)

    @mcp.tool
    def audio(job_id: str) -> dict:
        """Download the finished podcast into the gateway's audio folder; returns its path."""
        return _run(gateway.audio, job_id)

    return mcp


def _check_loopback(host: str) -> str:
    try:
        ok = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = False
    if not ok:
        raise SystemExit(f"refusing to bind {host!r}: the learning gateway only listens on loopback")
    return host


def gateway_from_config(
    pack: Path | None, overlay: Path | None, environ=None, *, metrics: LearningMetrics | None = None
) -> LearningGateway:
    from pkpipe.engine.registry import ConfigError, load_effective

    try:
        cfg = load_effective(pack, overlay)
        deny_file = ((cfg.data.get("security") or {}).get("url_filter") or {}).get("deny_list")
        found = cfg.find_file(deny_file) if deny_file else None
        deny_text = found.read_text(encoding="utf-8") if found else None
        return GatewaySettings.from_config(cfg.data, deny_text=deny_text, environ=environ).build(
            metrics=metrics
        )
    except (ConfigError, LearningError, OSError) as exc:
        raise SystemExit(f"invalid configuration: {exc}") from None


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pkpipe-learning-gateway")
    ap.add_argument("--pack", type=Path, help="pack directory (default: PKPIPE_PACK_DIR or the public pack)")
    ap.add_argument("--overlay", type=Path, help="private overlay (default: PKPIPE_OVERLAY_DIR)")
    ap.add_argument("--transport", choices=["http", "stdio"], default="http")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)
    host = _check_loopback(args.host)
    from pkpipe import telemetry

    # OTEL_* from the environment, like the app; the gateway records the learning metrics (constitution X)
    tel = telemetry.configure()
    server = build_server(gateway_from_config(args.pack, args.overlay, metrics=tel.metrics))
    if args.transport == "stdio":
        server.run(transport="stdio", show_banner=False)
    else:
        server.run(
            transport="http", host=host, port=args.port, show_banner=False, host_origin_protection=True
        )


if __name__ == "__main__":
    main()
