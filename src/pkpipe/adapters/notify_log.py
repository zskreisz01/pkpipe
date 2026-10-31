"""NotifyPort that writes to the log; the default until channel adapters (spec 006) are configured."""

from __future__ import annotations

import logging

log = logging.getLogger("pkpipe.notify")


class LogNotify:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, channel: str, message: str, actions: list[dict[str, str]] | None = None) -> None:
        self.sent.append((channel, message))
        log.info("notify %s: %s", channel, message)
