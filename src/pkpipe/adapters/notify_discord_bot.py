"""Discord bot channel (spec 018): outgoing messages through the REST API with a bot token.

``POST /channels/{id}/messages`` with ``Authorization: Bot <token>``: no gateway connection, no
``discord.py``, nothing listens. The bot needs *View Channel* and *Send Messages* (and *Embed Links* for
embeds) in the target channel. The body is the same as the webhook's (``content`` or a JSON template such
as embeds);
``allowed_mentions.parse = []`` is always set, because payload text may contain untrusted ``@everyone``.

The API base is a constructor argument for tests only; no config key can change it, so an overlay cannot send
the token to another host.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from pkpipe.adapters.notify_templates import Rendered

API_BASE = "https://discord.com/api/v10"
MAX_RETRY_WAIT_S = 5.0


class DiscordBot:
    type = "discord_bot"

    def __init__(
        self,
        token: str,
        channel_id: str,
        client: httpx.Client,
        *,
        api_base: str = API_BASE,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._token = token
        self._url = f"{api_base.rstrip('/')}/channels/{channel_id}/messages"
        self._client = client
        self._sleep = sleep

    def deliver(self, rendered: Rendered, *, event: str) -> None:
        body = dict(rendered.data) if rendered.data is not None else {"content": rendered.text}
        body["allowed_mentions"] = {"parse": []}
        headers = {"Authorization": f"Bot {self._token}"}
        resp = self._client.post(self._url, json=body, headers=headers)
        if resp.status_code == 429:  # rate limited: wait the advised time once if it is short
            wait = _retry_after(resp)
            if wait is not None and wait <= MAX_RETRY_WAIT_S:
                self._sleep(wait)
                resp = self._client.post(self._url, json=body, headers=headers)
        resp.raise_for_status()


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.json().get("retry_after"))
    except (ValueError, TypeError, AttributeError):
        return None
