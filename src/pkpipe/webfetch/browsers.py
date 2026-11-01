"""Browser backends of the web-fetch server: Lightpanda (primary) and Playwright + Chromium (fallback).

Both run behind the :class:`~pkpipe.webfetch.proxy.FilteringProxy` and return the rendered DOM as HTML plus
the main response's status and headers. Neither exposes any scripting surface to a caller: there is no
``evaluate``, ``click`` or ``fill``, only "load this URL and give me the document". Lightpanda runs as a
separate process with a minimal environment (no inherited secrets) in an empty temporary home directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Protocol

LIGHTPANDA_ENV = "LIGHTPANDA_BIN"


class BrowserError(Exception):
    """The backend could not load the page (navigation error, timeout, crash, not installed)."""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


@dataclass
class Page:
    url: str
    final_url: str
    http_status: int | None
    headers: dict[str, str] = field(default_factory=dict)
    html: str = ""


class Browser(Protocol):
    name: str

    def load(self, url: str, *, proxy_url: str, max_bytes: int, timeout_s: float) -> Page: ...


def _clean_env(home: str) -> dict[str, str]:
    """Only what a browser needs: no API keys, tokens or webhook URLs from the parent environment."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "TMPDIR": home}
    env["LIGHTPANDA_DISABLE_TELEMETRY"] = "true"
    for key in ("LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def lightpanda_binary() -> str | None:
    return os.environ.get(LIGHTPANDA_ENV) or shutil.which("lightpanda")


class LightpandaBrowser:
    """``lightpanda fetch --json --dump html`` through the filtering proxy, one process per page."""

    name = "lightpanda"

    def __init__(self, binary: str | None = None, *, wait_ms: int = 5000):
        self.binary = binary
        self.wait_ms = wait_ms

    def load(self, url: str, *, proxy_url: str, max_bytes: int, timeout_s: float) -> Page:
        binary = self.binary or lightpanda_binary()
        if not binary:
            raise BrowserError("not_installed", "lightpanda binary not found")
        timeout_ms = int(timeout_s * 1000)
        args = [
            binary,
            "fetch",
            "--json",
            "--dump",
            "html",
            "--http-proxy",
            proxy_url,
            "--http-max-response-size",
            str(max_bytes),
            "--http-timeout",
            str(timeout_ms),
            "--terminate-ms",
            str(timeout_ms),
            "--wait-ms",
            str(min(self.wait_ms, timeout_ms)),
            "--log-level",
            "fatal",
            url,
        ]
        with tempfile.TemporaryDirectory(prefix="pkpipe-lp-") as home:
            try:
                proc = subprocess.run(
                    args,
                    capture_output=True,
                    timeout=timeout_s + 5,
                    env=_clean_env(home),
                    cwd=home,
                    stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired as exc:
                raise BrowserError("timeout", "lightpanda timed out") from exc
            except OSError as exc:
                raise BrowserError("not_installed", str(exc)) from exc
        try:
            data: dict[str, Any] = json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
        except ValueError as exc:
            raise BrowserError("bad_output", f"lightpanda exit {proc.returncode}") from exc
        error = data.get("error")
        if error:
            reason = (
                "too_large" if "TooLarge" in str(error) else ("timeout" if "imeout" in str(error) else "")
            )
            raise BrowserError(reason or "navigation_error", str(error)[:200])
        if not data.get("http_status"):  # no response at all (connection reset, empty reply)
            raise BrowserError("navigation_error", "no HTTP response")
        headers = {str(h.get("name", "")).lower(): str(h.get("value", "")) for h in data.get("headers") or []}
        return Page(
            url=url,
            final_url=str(data.get("url") or url),
            http_status=data.get("http_status"),
            headers=headers,
            html=str(data.get("content") or ""),
        )


class ChromiumBrowser:
    """Playwright + headless Chromium through the same proxy. JavaScript runs (pages need it) but no caller
    can run code in the page; downloads and service workers are off, WebRTC cannot bypass the proxy."""

    name = "chromium"

    def load(self, url: str, *, proxy_url: str, max_bytes: int, timeout_s: float) -> Page:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserError("not_installed", "playwright is not installed") from exc
        with tempfile.TemporaryDirectory(prefix="pkpipe-cr-") as home:
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(
                        headless=True,
                        proxy={"server": proxy_url},
                        args=[
                            "--proxy-bypass-list=<-loopback>",
                            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                            "--disable-background-networking",
                            "--disable-sync",
                            "--no-pings",
                        ],
                        env={**_clean_env(home), "HOME": home},
                        timeout=timeout_s * 1000,
                    )
                    try:
                        context = browser.new_context(
                            accept_downloads=False, service_workers="block", ignore_https_errors=False
                        )
                        page = context.new_page()
                        response = page.goto(url, wait_until="load", timeout=timeout_s * 1000)
                        html = page.content()
                        headers = {k.lower(): v for k, v in (response.headers if response else {}).items()}
                        if len(html.encode("utf-8", "ignore")) > max_bytes:
                            raise BrowserError("too_large", "document exceeds max_bytes")
                        return Page(
                            url=url,
                            final_url=page.url,
                            http_status=response.status if response else None,
                            headers=headers,
                            html=html,
                        )
                    finally:
                        browser.close()
            except PlaywrightError as exc:
                text = str(exc)
                reason = "timeout" if "Timeout" in text else "navigation_error"
                if "Executable doesn't exist" in text:
                    reason = "not_installed"
                raise BrowserError(reason, text.splitlines()[0][:200] if text else reason) from exc
