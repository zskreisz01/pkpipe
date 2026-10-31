"""Microsoft Teams workflow webhook channel with Adaptive Cards (spec 006 T003)."""

from __future__ import annotations

from typing import Any

import httpx

from pkpipe.adapters.notify_templates import Rendered

CARD = "application/vnd.microsoft.card.adaptive"


def wrap_card(card: dict[str, Any]) -> dict[str, Any]:
    return {"type": "message", "attachments": [{"contentType": CARD, "contentUrl": None, "content": card}]}


def text_card(text: str, title: str | None = None) -> dict[str, Any]:
    body: list[dict[str, Any]] = []
    if title:
        body.append({"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True})
    body.append({"type": "TextBlock", "text": text, "wrap": True})
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }


class TeamsWorkflow:
    """Posts to a Teams "Workflows" webhook. A JSON template may be a bare Adaptive Card or a full message
    envelope; a Markdown / plain text message becomes a card with the event as title."""

    type = "teams_webhook"

    def __init__(self, url: str, client: httpx.Client):
        self._url = url
        self._client = client

    def build(self, rendered: Rendered, event: str) -> dict[str, Any]:
        data = rendered.data
        if data is not None:
            if "attachments" in data:
                return data
            if data.get("type") == "AdaptiveCard":
                return wrap_card(data)
        return wrap_card(text_card(rendered.plain(), title=event))

    def deliver(self, rendered: Rendered, *, event: str) -> None:
        self._client.post(self._url, json=self.build(rendered, event)).raise_for_status()
