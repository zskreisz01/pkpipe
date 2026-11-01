"""One secure fetch: URL policy, Lightpanda behind the filtering proxy, one Chromium fallback (spec 008).

Outcome (the ``FetchResult`` of ``pkpipe.security.verdict``):

* ``blocked``: the URL, a redirect hop or a request the page made hit the policy (deny-list, private
  network) or the size budget. Never retried with the fallback: it would be blocked the same way.
* ``failed``: Lightpanda failed (navigation error, timeout, crash, not installed), the Chromium fallback ran
  once and failed too, or the page answered with an HTTP error. Not a security flag.
* ``ok``: the rendered document; for non-HTML content (PDF, image, video, audio) only metadata.

Every outcome is counted (fallback rate, spec 008 SC-002) and, when a stats file is configured, appended to
it as one JSON line without the URL.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pkpipe.security.urlfilter import FLAG_REASONS, Reason, Resolver, UrlPolicy
from pkpipe.webfetch.browsers import Browser, BrowserError, ChromiumBrowser, LightpandaBrowser, Page
from pkpipe.webfetch.proxy import FilteringProxy

#: The page plus its scripts and styles may use this multiple of ``max_bytes`` in total.
BUDGET_FACTOR = 4
#: Default of ``SecureFetcher(fallback=...)``: Playwright + Chromium. Pass ``None`` for no fallback.
DEFAULT_FALLBACK = object()


def content_kind(content_type: str | None) -> str:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct in ("", "text/html", "application/xhtml+xml"):
        return "html"
    if ct.startswith("text/"):
        return "text"
    if ct == "application/pdf":
        return "pdf"
    for prefix in ("video", "image", "audio"):
        if ct.startswith(prefix + "/"):
            return prefix
    return "other"


class SecureFetcher:
    def __init__(
        self,
        policy: UrlPolicy,
        *,
        primary: Browser | None = None,
        fallback: Browser | None | object = DEFAULT_FALLBACK,
        resolve: Resolver | None = None,
        timeout_s: float = 30.0,
        stats_file: Path | None = None,
    ):
        self.policy = policy
        self.primary = primary or LightpandaBrowser()
        # ``None`` means no fallback at all; only the default builds the Chromium one
        self.fallback: Browser | None = ChromiumBrowser() if fallback is DEFAULT_FALLBACK else fallback  # type: ignore[assignment]
        self.resolve = resolve
        self.timeout_s = timeout_s
        self.stats_file = stats_file
        self.stats: Counter[str] = Counter()
        self._lock = threading.Lock()

    # -- stats --------------------------------------------------------------------------------------------
    def _record(self, result: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.stats["fetches"] += 1
            self.stats[f"status.{result['status']}"] += 1
            if result.get("fetcher"):
                self.stats[f"fetcher.{result['fetcher']}"] += 1
            if result.get("fallback_used"):
                self.stats["fallback"] += 1
            if self.stats_file:
                line = {
                    "ts": time.time(),
                    "status": result["status"],
                    "fetcher": result.get("fetcher"),
                    "fallback_used": bool(result.get("fallback_used")),
                    "fallback_reason": result.get("fallback_reason"),
                    "kind": result.get("kind"),
                    "reason": result.get("reason"),
                }
                self.stats_file.parent.mkdir(parents=True, exist_ok=True)
                with self.stats_file.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(line) + "\n")
        return result

    def stats_summary(self) -> dict[str, Any]:
        with self._lock:
            s = dict(self.stats)
        n = s.get("fetches", 0)
        return {**s, "fallback_rate": round(s.get("fallback", 0) / n, 4) if n else 0.0}

    # -- fetch --------------------------------------------------------------------------------------------
    def fetch(self, url: str, max_bytes: int | None = None) -> dict[str, Any]:
        limit = min(self.policy.max_bytes, max_bytes or self.policy.max_bytes)
        base: dict[str, Any] = {"url": url, "fallback_used": False}
        decision = self.policy.check_url(url, self.resolve)
        if not decision.allowed:
            status = "blocked" if decision.flag else "failed"
            return self._record({**base, "status": status, "reason": decision.reason})
        first_error: str | None = None
        for i, browser in enumerate([self.primary, self.fallback]):
            if browser is None:
                break
            with FilteringProxy(
                self.policy, budget_bytes=limit * BUDGET_FACTOR, resolve=self.resolve
            ) as proxy:
                try:
                    page = browser.load(url, proxy_url=proxy.url, max_bytes=limit, timeout_s=self.timeout_s)
                    error: BrowserError | None = None
                except BrowserError as exc:
                    page, error = None, exc
                record = proxy.record
            blocked = self._blocked(record, error, page)
            extra = {"fetcher": browser.name, "fallback_used": i > 0}
            if i > 0:
                extra["fallback_reason"] = first_error
            if blocked:
                return self._record({**base, **extra, "status": "blocked", "reason": blocked})
            if page is not None:
                return self._record({**base, **extra, **self._page(page, limit)})
            first_error = first_error or (error.reason if error else "error")
        return self._record(
            {
                **base,
                "status": "failed",
                "reason": first_error or "fetch_failed",
                "fetcher": None,
                "fallback_used": self.fallback is not None,
                "fallback_reason": first_error,
            }
        )

    @staticmethod
    def _blocked(record, error: BrowserError | None, page: Page | None = None) -> str | None:
        """A policy block anywhere in the fetch: the main URL or a redirect hop (deny-list, private network),
        a sub-request to a private address, or the size budget. Only flag reasons count: a third-party host
        that does not resolve is not a security flag."""
        if record.too_large or (error is not None and error.reason == "too_large"):
            return Reason.SIZE
        flags = [b for b in record.blocked if b["reason"] in FLAG_REASONS]
        for b in flags:
            if b["reason"] == Reason.PRIVATE:
                return Reason.PRIVATE
        if page is not None and flags:
            # the navigation itself ended on the proxy's refusal (e.g. a redirect to a deny-listed domain):
            # the browser reports the proxy's 403 as the page, which must not pass as an HTTP error
            host = (urlsplit(page.final_url or "").hostname or "").rstrip(".")
            for b in flags:
                if b["host"] == host:
                    return b["reason"]
        if error is not None and flags:
            return flags[0]["reason"]
        return None

    @staticmethod
    def _page(page: Page, limit: int) -> dict[str, Any]:
        ct = page.headers.get("content-type")
        kind = content_kind(ct)
        out: dict[str, Any] = {
            "final_url": page.final_url,
            "http_status": page.http_status,
            "content_type": ct,
            "kind": kind,
        }
        if page.http_status is not None and page.http_status >= 400:
            return {**out, "status": "failed", "reason": f"http_{page.http_status}"}
        body = page.html.encode("utf-8", "ignore")
        if len(body) > limit:
            return {**out, "status": "blocked", "reason": Reason.SIZE}
        if kind in ("html", "text"):
            return {**out, "status": "ok", "html": page.html, "bytes": len(body)}
        length = page.headers.get("content-length")
        return {**out, "status": "ok", "html": None, "bytes": int(length) if str(length).isdigit() else 0}
