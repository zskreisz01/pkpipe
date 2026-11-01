"""A real local HTTP server that captures webhook requests (never a real Discord / Teams endpoint)."""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class Captured:
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class WebhookServer:
    base_url: str
    requests: list[Captured] = field(default_factory=list)
    #: path prefix -> status to answer with (default 204)
    status: dict[str, int] = field(default_factory=dict)

    def on(self, path: str) -> list[Captured]:
        return [r for r in self.requests if r.path == path]


@contextlib.contextmanager
def webhook_server() -> Iterator[WebhookServer]:
    state: WebhookServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw.decode("utf-8", "replace")
            state.requests.append(Captured(self.path, dict(self.headers), body))
            code = next((c for p, c in state.status.items() if self.path.startswith(p)), 204)
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:  # silence
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state = WebhookServer(f"http://127.0.0.1:{server.server_address[1]}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
