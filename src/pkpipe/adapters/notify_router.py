"""NotifyPort implementation: event -> channels (``channels.json`` routes) -> template -> channel adapter.

Spec 006 FR-001..FR-003. Per delivery a ``notify.send`` span records channel id, channel type and event plus
the flow / step / item attributes of the step that sent it (constitution X); a
failure marks the span as an error and never stops the other channels. Webhook URLs are resolved from their
``env:NAME`` reference at send time and appear in no message, span or error text.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import httpx
from opentelemetry.trace import Status, StatusCode

from pkpipe import telemetry
from pkpipe.adapters.notify_apprise import AppriseChannel
from pkpipe.adapters.notify_discord import DiscordWebhook
from pkpipe.adapters.notify_discord_bot import DiscordBot
from pkpipe.adapters.notify_log import LogChannel
from pkpipe.adapters.notify_teams import TeamsWorkflow
from pkpipe.adapters.notify_templates import Rendered, render_event
from pkpipe.ports import Delivery

log = logging.getLogger("pkpipe.notify")


class ChannelAdapter(Protocol):
    type: str

    def deliver(self, rendered: Rendered, *, event: str) -> None: ...


class SecretError(Exception):
    """An ``env:NAME`` reference could not be resolved (names the variable, never a value)."""


def resolve_env(ref: str, environ: Mapping[str, str]) -> str:
    if not ref.startswith("env:"):
        raise SecretError("secret is not an env: reference")
    name = ref[4:]
    value = environ.get(name)
    if not value:
        raise SecretError(f"environment variable '{name}' is not set")
    return value


def describe_error(exc: BaseException) -> str:
    """A short, secret-free description (httpx errors embed the request URL, so they are not stringified)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return type(exc).__name__
    if isinstance(exc, SecretError):
        return str(exc)
    return (
        f"{type(exc).__name__}: {exc}" if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
    )


def _without_http_spans(client: httpx.Client) -> None:
    """Webhook URLs carry the secret token and httpx auto-instrumentation records the URL on its span, so the
    notifier's client opts out; the ``notify.send`` span records the delivery instead."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor.uninstrument_client(client)
    except Exception:  # noqa: BLE001 - instrumentation is optional
        log.debug("could not opt the notify client out of HTTP instrumentation", exc_info=True)


class ChannelNotifier:
    def __init__(
        self,
        config: Mapping[str, Any] | None,
        assets: Mapping[str, str] | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        client: httpx.Client | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        config = config or {}
        self._channels: dict[str, Mapping[str, Any]] = dict(config.get("channels") or {})
        self._routes: dict[str, list[str]] = {k: list(v) for k, v in (config.get("routes") or {}).items()}
        self._ui_url: str | None = config.get("ui_url")
        self._assets = assets or {}
        self._environ = os.environ if environ is None else environ
        self._client = client or httpx.Client(timeout=timeout_s, follow_redirects=False)
        _without_http_spans(self._client)
        self._factories: dict[str, Callable[[Mapping[str, Any]], ChannelAdapter]] = {
            "discord_webhook": lambda c: DiscordWebhook(self._secret(c["url"]), self._client),
            "discord_bot": lambda c: DiscordBot(
                self._secret(c["token"]), self._channel_id(c["channel_id"]), self._client
            ),
            "teams_webhook": lambda c: TeamsWorkflow(self._secret(c["url"]), self._client),
            "apprise": lambda c: AppriseChannel([self._secret(u) for u in c["urls"]]),
            "log": lambda c: LogChannel(),
        }

    def _secret(self, ref: str) -> str:
        return resolve_env(ref, self._environ)

    def _channel_id(self, value: str) -> str:
        return resolve_env(value, self._environ) if value.startswith("env:") else value

    def targets(self, event: str, channels: list[str] | None = None) -> list[str]:
        return list(channels) if channels is not None else list(self._routes.get(event, []))

    def send(self, event: str, payload: dict[str, Any], channels: list[str] | None = None) -> list[Delivery]:
        return [self._send_one(event, name, payload) for name in self.targets(event, channels)]

    def _send_one(self, event: str, name: str, payload: dict[str, Any]) -> Delivery:
        cfg = self._channels.get(name)
        ctype = str(cfg.get("type")) if cfg else "unknown"
        with telemetry.notify_span(event, name, ctype) as span:
            try:
                if cfg is None or ctype not in self._factories:
                    raise ValueError(f"unknown channel '{name}'")
                rendered = render_event(event, ctype, payload, self._assets, self._ui_url)
                if rendered.warning:
                    span.add_event("notify.template_fallback", {"reason": rendered.warning})
                _without_http_spans(self._client)  # again per send: instrumentation may start after __init__
                self._factories[ctype](cfg).deliver(rendered, event=event)
            except Exception as exc:  # noqa: BLE001 - one channel must never stop the others
                error = describe_error(exc)
                span.set_status(Status(StatusCode.ERROR, error))
                span.set_attribute("error.type", type(exc).__name__)
                log.warning("notify %s via %s failed: %s", event, name, error)
                return Delivery(event, name, False, error)
            span.set_attribute("notify.truncated", rendered.truncated)
            return Delivery(event, name, True, truncated=rendered.truncated)
