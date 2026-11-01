"""Message templates per channel type (spec 006 FR-002).

``templates/<event>.<channel type>.md|json`` are Jinja2 files (sandboxed) rendered with the event payload as
variables (``{{ message }}``, ``{{ count }}``, ...). A ``.json`` template is a channel-native body (Discord
embeds, a Teams Adaptive Card) and must use ``{{ value | tojson }}`` for every interpolated value, so
a payload can never break the JSON structure. Without a template the payload's ``message`` (or its JSON)
is sent.
Texts longer than the channel allows are truncated and end with a link to the UI.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment

#: Conservative per-channel text limits (Discord 2000 content chars, Teams / Apprise services vary).
LIMITS = {"discord_webhook": 2000, "teams_webhook": 4000, "apprise": 4000, "log": 4000}
TEXT_KEYS = ("content", "description", "text", "body")

_env = SandboxedEnvironment(autoescape=False, keep_trailing_newline=False)


@dataclass(frozen=True)
class Rendered:
    """``data`` is set for a JSON template, else ``text``. ``warning`` explains a fallback to plain text."""

    text: str | None
    data: dict[str, Any] | None
    truncated: bool = False
    warning: str | None = None

    def plain(self) -> str:
        if self.text is not None:
            return self.text
        parts = [v for k in TEXT_KEYS if isinstance(v := (self.data or {}).get(k), str)]
        return "\n".join(parts) if parts else json.dumps(self.data, ensure_ascii=False)


def _fallback_text(event: str, payload: Mapping[str, Any]) -> str:
    message = payload.get("message")
    if isinstance(message, str) and message:
        return message
    return f"{event}: {json.dumps(dict(payload), ensure_ascii=False, default=str)}"


def truncate(text: str, limit: int, ui_url: str | None) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    suffix = f"\n... truncated, see {ui_url}" if ui_url else "\n... truncated"
    return text[: max(0, limit - len(suffix))] + suffix, True


def _truncate_data(node: Any, limit: int, ui_url: str | None) -> bool:
    cut = False
    if isinstance(node, dict):
        for key, value in node.items():
            if key in TEXT_KEYS and isinstance(value, str):
                node[key], did = truncate(value, limit, ui_url)
                cut = cut or did
            else:
                cut = _truncate_data(value, limit, ui_url) or cut
    elif isinstance(node, list):
        for value in node:
            cut = _truncate_data(value, limit, ui_url) or cut
    return cut


def render_event(
    event: str,
    channel_type: str,
    payload: Mapping[str, Any],
    assets: Mapping[str, str],
    ui_url: str | None = None,
) -> Rendered:
    limit = LIMITS.get(channel_type, 2000)
    warning = None
    for suffix in ("json", "md"):
        source = assets.get(f"templates/{event}.{channel_type}.{suffix}")
        if source is None:
            continue
        try:
            body = _env.from_string(source).render(**payload)
            if suffix == "md":
                text, cut = truncate(body.strip(), limit, ui_url)
                return Rendered(text=text, data=None, truncated=cut)
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("a JSON template must render a JSON object")
            return Rendered(text=None, data=data, truncated=_truncate_data(data, limit, ui_url))
        except (TemplateError, ValueError) as exc:  # a broken template must not swallow the notification
            warning = (
                f"template {event}.{channel_type}.{suffix} failed ({type(exc).__name__}); sent plain text"
            )
            break
    text, cut = truncate(_fallback_text(event, payload), limit, ui_url)
    return Rendered(text=text, data=None, truncated=cut, warning=warning)
