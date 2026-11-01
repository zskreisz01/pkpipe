"""Filtering forward proxy: the network boundary of the browsers (spec 008 FR-001, FR-002).

Lightpanda (``--http-proxy``) and Chromium (``proxy`` + ``--proxy-bypass-list=<-loopback>``) send *every*
request through this proxy: the page, each redirect hop, scripts, XHR/fetch, images. For each request the
proxy applies the :class:`~pkpipe.security.urlfilter.UrlPolicy` after DNS resolution and connects to the
address it checked (no second lookup, so DNS rebinding cannot swap in a private address). Blocked requests
get ``403`` and are recorded. The bytes received from upstream are counted against a budget; above it the
connection is cut and the fetch is reported as too large.

One proxy serves one fetch (it listens on an ephemeral loopback port and stops afterwards), so its records
belong to exactly that fetch.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from pkpipe.security.urlfilter import Resolver, UrlPolicy

HOP_HEADERS = (
    b"proxy-connection",
    b"proxy-authorization",
    b"connection",
    b"keep-alive",
    b"te",
    b"trailer",
    b"upgrade",
)
READ_LIMIT = 64 * 1024
HEADER_LIMIT = 64 * 1024


@dataclass
class ProxyRecord:
    blocked: list[dict] = field(default_factory=list)  # {"host", "port", "reason"}
    requests: int = 0
    bytes_in: int = 0
    too_large: bool = False


class FilteringProxy:
    def __init__(self, policy: UrlPolicy, *, budget_bytes: int, resolve: Resolver | None = None):
        self.policy = policy
        self.budget = budget_bytes
        self.resolve = resolve
        self.record = ProxyRecord()
        self.port: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.base_events.Server | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._writers: set[asyncio.StreamWriter] = set()  # open client and upstream connections

    # -- lifecycle ---------------------------------------------------------------------------------------
    def __enter__(self) -> FilteringProxy:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        ready = threading.Event()

        def run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._server = self._loop.run_until_complete(
                asyncio.start_server(self._handle, "127.0.0.1", 0, limit=HEADER_LIMIT)
            )
            self.port = self._server.sockets[0].getsockname()[1]
            ready.set()
            self._loop.run_forever()
            # shut down without waiting for clients: a browser killed mid-request (timeout) or a stalled
            # upstream must not keep the proxy (and the fetch) alive
            self._server.close()
            tasks = asyncio.all_tasks(self._loop)
            for task in tasks:
                task.cancel()
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            for w in list(self._writers):
                with contextlib.suppress(Exception):
                    w.close()
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(asyncio.wait_for(self._server.wait_closed(), 2))
            self._loop.close()

        self._thread = threading.Thread(target=run, name="pkpipe-fetch-proxy", daemon=True)
        self._thread.start()
        if not ready.wait(10):
            raise RuntimeError("filtering proxy did not start")

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(10)

    # -- bookkeeping -------------------------------------------------------------------------------------
    def _block(self, host: str | None, port: int | None, reason: str) -> None:
        with self._lock:
            self.record.blocked.append({"host": host, "port": port, "reason": reason})

    def _count(self, n: int) -> bool:
        """Add received bytes; False once the budget is exceeded."""
        with self._lock:
            self.record.bytes_in += n
            if self.record.bytes_in > self.budget:
                self.record.too_large = True
            return not self.record.too_large

    # -- protocol ----------------------------------------------------------------------------------------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            await self._serve(reader, writer)
        finally:
            self._writers.discard(writer)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        lines = head.split(b"\r\n")
        try:
            method, target, _version = lines[0].decode("latin-1").split(" ", 2)
        except ValueError:
            await self._reply(writer, 400, "bad request")
            return
        with self._lock:
            self.record.requests += 1
        if method.upper() == "CONNECT":
            await self._connect(target, reader, writer)
        else:
            await self._forward(method, target, lines[1:], reader, writer)

    async def _reply(self, writer: asyncio.StreamWriter, code: int, reason: str) -> None:
        body = f"pkpipe secure fetch: {reason}\n".encode()
        writer.write(
            f"HTTP/1.1 {code} Blocked\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
            f"X-Pkpipe-Blocked: {reason}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        with contextlib.suppress(Exception):
            await writer.drain()
        writer.close()

    def _check(self, host: str, port: int, url: str | None):
        if url is not None:
            return self.policy.check_url(url, self.resolve)
        return self.policy.check_host(host, port, self.resolve)

    async def _open(self, host: str, port: int, url: str | None, writer: asyncio.StreamWriter):
        decision = await asyncio.to_thread(self._check, host, port, url)
        if not decision.allowed:
            self._block(decision.host or host, port, decision.reason or "blocked")
            await self._reply(writer, 403, decision.reason or "blocked")
            return None
        try:
            upstream = await asyncio.wait_for(asyncio.open_connection(decision.addresses[0], port), 15)
        except (OSError, TimeoutError):
            await self._reply(writer, 502, "upstream_unreachable")
            return None
        self._writers.add(upstream[1])
        return upstream

    async def _connect(self, target: str, reader, writer) -> None:
        host, _, port_s = target.rpartition(":")
        try:
            port = int(port_s)
        except ValueError:
            await self._reply(writer, 400, "bad connect target")
            return
        upstream = await self._open(host.strip("[]"), port, None, writer)
        if upstream is None:
            return
        u_reader, u_writer = upstream
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await self._pipe(reader, writer, u_reader, u_writer)

    async def _forward(self, method: str, target: str, headers: list[bytes], reader, writer) -> None:
        try:
            parts = urlsplit(target)
            port = parts.port or 80
        except ValueError:
            await self._reply(writer, 400, "bad target")
            return
        if parts.scheme.lower() != "http" or not parts.hostname:
            await self._reply(writer, 400, "absolute http URL required")
            return
        upstream = await self._open(parts.hostname, port, target, writer)
        if upstream is None:
            return
        u_reader, u_writer = upstream
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        kept = [h for h in headers if h and h.split(b":", 1)[0].strip().lower() not in HOP_HEADERS]
        u_writer.write(
            f"{method} {path} HTTP/1.1\r\n".encode("latin-1")
            + b"".join(h + b"\r\n" for h in kept)
            + b"Connection: close\r\n\r\n"
        )
        await u_writer.drain()
        await self._pipe(reader, writer, u_reader, u_writer)

    async def _pipe(self, reader, writer, u_reader, u_writer) -> None:
        async def up() -> None:
            with contextlib.suppress(Exception):
                while data := await reader.read(READ_LIMIT):
                    u_writer.write(data)
                    await u_writer.drain()
            with contextlib.suppress(Exception):
                u_writer.write_eof()

        async def down() -> None:
            with contextlib.suppress(Exception):
                while data := await u_reader.read(READ_LIMIT):
                    if not self._count(len(data)):
                        break
                    writer.write(data)
                    await writer.drain()

        up_task = asyncio.create_task(up())
        await down()
        up_task.cancel()
        for w in (u_writer, writer):
            self._writers.discard(w)
            with contextlib.suppress(Exception):
                w.close()
