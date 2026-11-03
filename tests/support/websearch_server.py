"""Start the real web-search MCP server (spec 017) against a stub SearXNG on loopback."""

from __future__ import annotations

import contextlib
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

from tests.support.ports import free_port

ROWS = [
    {"title": "Good", "url": "https://example.org/good", "content": "A <b>good</b> result"},
    {"title": "Denied", "url": "https://denied.example/x", "content": "pack deny-list"},
    {"title": "Metadata", "url": "http://169.254.169.254/", "content": "private"},
]


@contextlib.contextmanager
def stub_searxng() -> Iterator[tuple[str, list[str]]]:
    queries: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            queries.append(self.path)
            body = json.dumps({"results": ROWS}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", free_port()), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", queries
    finally:
        httpd.shutdown()


@contextlib.contextmanager
def websearch_server(searxng_url: str) -> Iterator[str]:
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "pkpipe.websearch.server", "--port", str(port), "--searxng-url", searxng_url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("web-search server exited early: " + proc.stderr.read().decode()[-500:])
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("web-search server did not start")
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
        if proc.poll() is None:
            proc.kill()
