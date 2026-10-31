"""``log`` channel: writes the message to the log; for development and as the zero-config default."""

from __future__ import annotations

import logging

from pkpipe.adapters.notify_templates import Rendered

log = logging.getLogger("pkpipe.notify")


class LogChannel:
    type = "log"

    def deliver(self, rendered: Rendered, *, event: str) -> None:
        log.info("notify %s: %s", event, rendered.plain())
