"""Discord webhook channel (spec 006 T002)."""

from __future__ import annotations

import httpx

from pkpipe.adapters.notify_templates import Rendered


class DiscordWebhook:
    """POSTs ``{"content": ...}`` (or the JSON template body, e.g. embeds) to the webhook URL.

    ``allowed_mentions.parse = []`` is always set: payload text may contain untrusted words such as
    ``@everyone`` and must never ping anybody.
    """

    type = "discord_webhook"

    def __init__(self, url: str, client: httpx.Client):
        self._url = url
        self._client = client

    def deliver(self, rendered: Rendered, *, event: str) -> None:
        body = dict(rendered.data) if rendered.data is not None else {"content": rendered.text}
        body["allowed_mentions"] = {"parse": []}
        self._client.post(self._url, json=body).raise_for_status()
