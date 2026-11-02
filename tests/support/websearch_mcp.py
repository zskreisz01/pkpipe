"""A synthetic ``websearch`` MCP server for tests (the real one is an external search backend).

``python -m tests.support.websearch_mcp --port N --log FILE`` serves ``search(query, max_results)`` on
Streamable
HTTP with canned, synthetic results and appends every query as one JSON line to FILE, so a test can
assert what
left the machine (the query guard, spec 010 FR-002).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from tests.support.ports import free_port

RESULTS = [
    {"title": "Example project page", "url": "https://example.org/project", "snippet": "A synthetic result."},
    {
        "title": "Example comparison",
        "url": "https://example.org/compare",
        "snippet": "Another synthetic result.",
    },
]


def build(log: Path):
    from fastmcp import FastMCP

    mcp = FastMCP("websearch-fake")

    @mcp.tool
    def search(query: str, max_results: int = 5) -> dict:
        """Search the web (synthetic results)."""
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"query": query}) + "\n")
        return {"results": RESULTS[:max_results]}

    return mcp


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--log", type=Path, required=True)
    args = ap.parse_args(argv)
    build(args.log).run(transport="http", host="127.0.0.1", port=args.port, show_banner=False)


@contextlib.contextmanager
def websearch_server(log: Path) -> Iterator[str]:
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.support.websearch_mcp", "--port", str(port), "--log", str(log)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("websearch server exited early")
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("websearch server did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
        if proc.poll() is None:
            proc.kill()


def queries(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [json.loads(line)["query"] for line in log.read_text(encoding="utf-8").splitlines() if line]


if __name__ == "__main__":
    main(sys.argv[1:])
