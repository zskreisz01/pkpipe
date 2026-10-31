from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pkpipe.ports import Delivery


@dataclass
class Sent:
    event: str
    channel: str
    payload: dict[str, Any]

    @property
    def message(self) -> str:
        return str(self.payload.get("message", ""))


@dataclass
class FakeNotify:
    """NotifyPort fake: routes like the real notifier (``routes``) and collects what would have been sent.

    ``fail`` lists channels whose delivery is reported as failed (the others still receive).
    """

    routes: dict[str, list[str]] = field(default_factory=dict)
    fail: set[str] = field(default_factory=set)
    sent: list[Sent] = field(default_factory=list)

    def send(self, event: str, payload: dict[str, Any], channels: list[str] | None = None) -> list[Delivery]:
        targets = list(channels) if channels is not None else list(self.routes.get(event, []))
        out: list[Delivery] = []
        for channel in targets:
            if channel in self.fail:
                out.append(Delivery(event, channel, False, "simulated failure"))
                continue
            self.sent.append(Sent(event, channel, dict(payload)))
            out.append(Delivery(event, channel, True))
        return out

    def to(self, channel: str) -> list[Sent]:
        return [s for s in self.sent if s.channel == channel]
