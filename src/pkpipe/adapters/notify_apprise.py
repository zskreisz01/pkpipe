"""Apprise channel: any of Apprise's services by URL, configuration only (spec 006 T004, SC-001)."""

from __future__ import annotations

from collections.abc import Sequence

import apprise

from pkpipe.adapters.notify_templates import Rendered


class AppriseChannel:
    type = "apprise"

    def __init__(self, urls: Sequence[str]):
        self._apprise = apprise.Apprise()
        for url in urls:
            if not self._apprise.add(url):
                raise ValueError("an Apprise URL is not valid for any supported service")

    def deliver(self, rendered: Rendered, *, event: str) -> None:
        ok = self._apprise.notify(body=rendered.plain() or event, title=event)
        if not ok:
            raise RuntimeError("apprise reported a failed delivery")
