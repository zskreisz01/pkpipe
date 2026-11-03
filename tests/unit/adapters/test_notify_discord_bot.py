"""Spec 018: the discord_bot channel with a mock HTTP transport (no network)."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from pkpipe.adapters.notify_discord_bot import DiscordBot
from pkpipe.adapters.notify_router import ChannelNotifier
from pkpipe.adapters.notify_templates import Rendered, render_event
from pkpipe.engine.config_models import ChannelsConfig
from pkpipe.engine.registry import load_effective

TOKEN = "TOPSECRET.BOT.TOKEN"
CFG = {
    "channels": {"bot": {"type": "discord_bot", "token": "env:BOT_TOKEN", "channel_id": "env:CHAN"}},
    "routes": {"triage_ready": ["bot"]},
}
ENV = {"BOT_TOKEN": TOKEN, "CHAN": "123456789"}


def client(handler, seen=None):
    def wrapped(request):
        if seen is not None:
            seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(wrapped))


def test_posts_to_the_channel_with_the_bot_header_and_no_mentions():
    seen = []
    bot = DiscordBot(TOKEN, "42", client(lambda r: httpx.Response(200, json={}), seen))
    bot.deliver(Rendered(text="hi @everyone", data=None), event="x")
    (req,) = seen
    assert str(req.url) == "https://discord.com/api/v10/channels/42/messages"
    assert req.headers["Authorization"] == f"Bot {TOKEN}"
    assert json.loads(req.content) == {"content": "hi @everyone", "allowed_mentions": {"parse": []}}


def test_json_template_body_is_sent_with_mentions_disabled():
    seen = []
    bot = DiscordBot(TOKEN, "42", client(lambda r: httpx.Response(200, json={}), seen))
    bot.deliver(Rendered(text=None, data={"embeds": [{"title": "t"}]}), event="x")
    assert json.loads(seen[0].content) == {"embeds": [{"title": "t"}], "allowed_mentions": {"parse": []}}


def test_short_rate_limit_is_retried_once():
    answers = iter([httpx.Response(429, json={"retry_after": 0.5}), httpx.Response(200, json={})])
    slept = []
    bot = DiscordBot(TOKEN, "42", client(lambda r: next(answers)), sleep=slept.append)
    bot.deliver(Rendered(text="x", data=None), event="e")
    assert slept == [0.5]


@pytest.mark.parametrize("body", [{"retry_after": 60}, {}, "garbage"])
def test_long_or_unknown_rate_limit_fails(body):
    bot = DiscordBot(TOKEN, "42", client(lambda r: httpx.Response(429, json=body)), sleep=lambda s: None)
    with pytest.raises(httpx.HTTPStatusError):
        bot.deliver(Rendered(text="x", data=None), event="e")


def test_notifier_uses_the_webhook_templates_and_never_leaks_the_token(pack_dir):
    assets = load_effective(pack_dir, None).snapshot().assets
    seen = []
    n = ChannelNotifier(CFG, assets, environ=ENV, client=client(lambda r: httpx.Response(200, json={}), seen))
    (d,) = n.send("triage_ready", {"message": "2 new item(s)"})
    assert d.ok and json.loads(seen[0].content)["content"] == "**Triage ready**\n2 new item(s)"
    assert seen[0].url.path == "/api/v10/channels/123456789/messages"


@pytest.mark.parametrize(("status", "error"), [(401, "HTTP 401"), (403, "HTTP 403"), (404, "HTTP 404")])
def test_failures_are_status_codes_without_the_token(status, error):
    n = ChannelNotifier(CFG, {}, environ=ENV, client=client(lambda r: httpx.Response(status, text=TOKEN)))
    (d,) = n.send("triage_ready", {"message": "m"})
    assert not d.ok and d.error == error and TOKEN not in repr(d)


def test_missing_env_names_the_variable_only():
    n = ChannelNotifier(CFG, {}, environ={"CHAN": "1"}, client=client(lambda r: httpx.Response(200)))
    (d,) = n.send("triage_ready", {"message": "m"})
    assert not d.ok and "BOT_TOKEN" in d.error


def test_a_specific_bot_template_overrides_the_alias():
    assets = {
        "templates/e.discord_webhook.md": "webhook",
        "templates/e.discord_bot.md": "bot",
    }
    assert render_event("e", "discord_bot", {}, assets).text == "bot"
    del assets["templates/e.discord_bot.md"]
    assert render_event("e", "discord_bot", {}, assets).text == "webhook"


@pytest.mark.parametrize(
    "channel",
    [
        {"type": "discord_bot"},
        {"type": "discord_bot", "token": "abc.def", "channel_id": "1"},  # inline token
        {"type": "discord_bot", "token": "env:T"},
        {"type": "discord_bot", "token": "env:T", "channel_id": "general"},
    ],
)
def test_invalid_bot_channels_are_rejected(channel):
    with pytest.raises(ValidationError):
        ChannelsConfig.model_validate({"channels": {"b": channel}})


def test_valid_bot_channel_forms():
    ChannelsConfig.model_validate(
        {"channels": {"b": {"type": "discord_bot", "token": "env:T", "channel_id": "123"}}}
    )
    ChannelsConfig.model_validate(
        {"channels": {"b": {"type": "discord_bot", "token": "env:T", "channel_id": "env:C"}}}
    )
