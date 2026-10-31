"""FastMCP server over the vault folder (spec 004 FR-001, FR-004).

Run: ``python -m pkpipe.obsidian.server --config vault.json`` (Streamable HTTP on 127.0.0.1:8701) or
``--transport stdio`` for tests and local MCP clients. Only loopback hosts are accepted.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .errors import VaultError
from .policy import VaultPolicy
from .vault import Vault

DEFAULT_PORT = 8701


def _run(fn: Callable[..., Any], *args: Any) -> Any:
    try:
        return fn(*args)
    except VaultError as exc:
        raise ToolError(str(exc)) from None


def build_server(policy: VaultPolicy) -> FastMCP:
    vault = Vault(policy)
    mcp = FastMCP("obsidian-vault")

    @mcp.tool
    def list_folder(path: str) -> dict:
        """List notes, sub folders and sync-conflict files of a vault folder (vault relative path)."""
        return _run(vault.list_folder, path)

    @mcp.tool
    def read_note(path: str) -> dict:
        """Read a note: front matter (parsed) and body."""
        return _run(vault.read_note, path)

    @mcp.tool
    def write_note(path: str, body: str, front_matter: dict | None = None, overwrite: bool = False) -> dict:
        """Create a note atomically; front_matter=None keeps an existing block, overwrite=true replaces."""
        return _run(vault.write_note, path, body, front_matter, overwrite)

    @mcp.tool
    def append_note(path: str, text: str) -> dict:
        """Append text to an existing note; its front matter stays untouched."""
        return _run(vault.append_note, path, text)

    @mcp.tool
    def move_note(src: str, dst: str) -> dict:
        """Move a note inside the allowed folders; never overwrites."""
        return _run(vault.move_note, src, dst)

    @mcp.tool
    def read_triage() -> dict:
        """Return the triage rows (confirm, override, state per item), unreadable rows and sync conflicts."""
        return _run(vault.read_triage)

    @mcp.tool
    def update_triage(rows: list[dict]) -> dict:
        """Upsert triage rows by item_id; omitted fields keep their current value. All-or-nothing."""
        return _run(vault.update_triage, rows)

    @mcp.tool
    def upsert_processing_list(rows: list[dict]) -> dict:
        """Upsert processing-list rows by item_id; other rows, including manual edits, are preserved."""
        return _run(vault.upsert_processing_list, rows)

    return mcp


def _check_loopback(host: str) -> str:
    try:
        ok = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = False
    if not ok:
        raise SystemExit(f"refusing to bind {host!r}: the vault server only listens on loopback (FR-004)")
    return host


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pkpipe-obsidian-vault")
    ap.add_argument("--config", type=Path, help="JSON policy file (vault_dir, allow, triage_file, ...)")
    ap.add_argument("--vault", type=Path, help="vault folder (overrides vault_dir of the config)")
    ap.add_argument("--transport", choices=["http", "stdio"], default="http")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)
    host = _check_loopback(args.host)
    try:
        policy = VaultPolicy.load(args.config, args.vault)
    except (ValueError, OSError) as exc:
        raise SystemExit(f"invalid configuration: {exc}") from None
    if not policy.vault_dir.is_dir():
        raise SystemExit(f"vault folder does not exist: {policy.vault_dir}")
    server = build_server(policy)
    if args.transport == "stdio":
        server.run(transport="stdio", show_banner=False)
    else:
        # Strict Host/Origin validation: a web page in the owner's browser must not reach the vault through
        # DNS rebinding or a cross-origin request; only loopback Host headers and non-browser clients pass.
        server.run(
            transport="http",
            host=host,
            port=args.port,
            show_banner=False,
            host_origin_protection=True,
        )


if __name__ == "__main__":
    main(sys.argv[1:])
