from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Sent:
    channel: str
    message: str
    actions: list[dict[str, str]] | None = None


@dataclass
class FakeNotify:
    """NotifyPort fake: collects messages so tests can assert what would have been sent."""

    sent: list[Sent] = field(default_factory=list)

    def send(self, channel: str, message: str, actions: list[dict[str, str]] | None = None) -> None:
        self.sent.append(Sent(channel, message, actions))

    def to(self, channel: str) -> list[Sent]:
        return [s for s in self.sent if s.channel == channel]
