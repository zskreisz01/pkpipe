"""Spec 006 US1, FR-001..FR-003, SC-001: real adapters against a real local HTTP server."""

from __future__ import annotations

import httpx
import pytest

from pkpipe import telemetry
from pkpipe.adapters.notify_router import ChannelNotifier
from pkpipe.engine.registry import load_effective
from pkpipe.ports import NotifyPort
from tests.support.webhook_server import webhook_server

SECRET_PATH = "/api/webhooks/123/TOPSECRETTOKEN"


@pytest.fixture
def server():
    with webhook_server() as s:
        yield s


def _config(server, **extra):
    return {
        "channels": {
            "discord_main": {"type": "discord_webhook", "url": "env:DISCORD_URL"},
            "teams_main": {"type": "teams_webhook", "url": "env:TEAMS_URL"},
            "apprise_json": {"type": "apprise", "urls": ["env:APPRISE_URL"]},
        },
        "routes": {"triage_ready": ["discord_main", "teams_main"], "daily_summary": ["discord_main"]},
        **extra,
    }


def _env(server):
    return {
        "DISCORD_URL": server.base_url + SECRET_PATH,
        "TEAMS_URL": server.base_url + "/teams/workflow",
        "APPRISE_URL": "json://" + server.base_url.removeprefix("http://") + "/apprise",
    }


def _notifier(server, pack_dir, **kw):
    assets = load_effective(pack_dir, None).snapshot().assets
    return ChannelNotifier(kw.pop("config", None) or _config(server), assets, environ=_env(server), **kw)


def test_notifier_satisfies_the_port(server, pack_dir):
    assert isinstance(_notifier(server, pack_dir), NotifyPort)


def test_triage_ready_reaches_every_routed_channel_with_its_template(server, pack_dir):
    """US1-AS1: Discord gets content, Teams gets an Adaptive Card message."""
    out = _notifier(server, pack_dir).send("triage_ready", {"message": "2 new item(s)"})
    assert [(d.channel, d.ok) for d in out] == [("discord_main", True), ("teams_main", True)]
    (discord,) = server.on(SECRET_PATH)
    assert discord.body["content"] == "**Triage ready**\n2 new item(s)"
    assert discord.body["allowed_mentions"] == {"parse": []}
    (teams,) = server.on("/teams/workflow")
    card = teams.body["attachments"][0]
    assert (
        teams.body["type"] == "message" and card["contentType"] == "application/vnd.microsoft.card.adaptive"
    )
    assert card["content"]["type"] == "AdaptiveCard"
    assert card["content"]["body"][1]["text"] == "2 new item(s)"


def test_event_without_route_sends_nothing(server, pack_dir):
    assert _notifier(server, pack_dir).send("weekly_review", {"message": "x"}) == []
    assert server.requests == []


def test_explicit_channels_override_the_route(server, pack_dir):
    out = _notifier(server, pack_dir).send("daily_summary", {"message": "x"}, ["teams_main"])
    assert [d.channel for d in out] == ["teams_main"] and len(server.requests) == 1


def test_mentions_in_untrusted_payload_do_not_ping(server, pack_dir):
    """Security: an item title containing @everyone must not notify the whole server."""
    _notifier(server, pack_dir).send("daily_summary", {"message": "@everyone <@123> hi"})
    assert server.on(SECRET_PATH)[0].body["allowed_mentions"] == {"parse": []}


def test_channel_failure_does_not_stop_others_and_is_traced(server, pack_dir, otel):
    """US1-AS2: Discord answers 500; Teams still receives; the failing span is an error without the URL."""
    server.status[SECRET_PATH] = 500
    out = _notifier(server, pack_dir).send("triage_ready", {"message": "m"})
    assert [(d.channel, d.ok) for d in out] == [("discord_main", False), ("teams_main", True)]
    assert out[0].error == "HTTP 500"
    assert len(server.on("/teams/workflow")) == 1
    spans = {s.attributes["notify.channel"]: s for s in otel.spans("notify.send")}
    assert spans["discord_main"].status.status_code.name == "ERROR"
    assert spans["teams_main"].status.status_code.name == "UNSET"
    assert spans["discord_main"].attributes["notify.event"] == "triage_ready"
    assert spans["discord_main"].attributes["notify.channel_type"] == "discord_webhook"
    assert "TOPSECRETTOKEN" not in repr([(s.name, dict(s.attributes), s.status) for s in otel.spans()])


def test_unreachable_endpoint_is_a_failure_without_leaking_the_url(server, pack_dir):
    cfg = _config(server)
    env = _env(server) | {"DISCORD_URL": "http://127.0.0.1:1/secret/TOPSECRETTOKEN"}
    assets = load_effective(pack_dir, None).snapshot().assets
    out = ChannelNotifier(cfg, assets, environ=env, timeout_s=2).send("daily_summary", {"message": "m"})
    assert not out[0].ok and "TOPSECRETTOKEN" not in out[0].error and "127.0.0.1" not in out[0].error


def test_unset_secret_names_the_variable_and_other_channels_still_send(server, pack_dir):
    """FR-003: env: reference resolved at send time; a missing variable fails only its channel."""
    env = _env(server)
    del env["DISCORD_URL"]
    assets = load_effective(pack_dir, None).snapshot().assets
    out = ChannelNotifier(_config(server), assets, environ=env).send("triage_ready", {"message": "m"})
    assert out[0].error == "environment variable 'DISCORD_URL' is not set" and out[1].ok


def test_secret_is_resolved_from_the_environment_not_the_config(server, pack_dir):
    n = _notifier(server, pack_dir)
    n.send("daily_summary", {"message": "m"})
    assert server.requests[0].path == SECRET_PATH  # value came from DISCORD_URL


def test_apprise_channel_is_configuration_only(server, pack_dir):
    """SC-001: a new Apprise channel is channels.json plus an env var, no code."""
    cfg = _config(server)
    cfg["routes"]["security_flag"] = ["apprise_json"]
    out = _notifier(server, pack_dir, config=cfg).send("security_flag", {"message": "canary seen"})
    assert [(d.channel, d.ok) for d in out] == [("apprise_json", True)]
    (req,) = server.on("/apprise")
    assert req.body["message"] == "canary seen" and req.body["title"] == "security_flag"


def test_apprise_failure_is_reported(server, pack_dir):
    server.status["/apprise"] = 500
    cfg = _config(server)
    out = _notifier(server, pack_dir, config=cfg).send("daily_summary", {"message": "m"}, ["apprise_json"])
    assert not out[0].ok and out[0].error


def test_invalid_apprise_url_fails_that_channel_only(server, pack_dir):
    env = _env(server) | {"APPRISE_URL": "notaservice://x"}
    assets = load_effective(pack_dir, None).snapshot().assets
    out = ChannelNotifier(_config(server), assets, environ=env).send(
        "daily_summary", {"message": "m"}, ["apprise_json", "discord_main"]
    )
    assert [d.ok for d in out] == [False, True]


def test_overlong_message_is_truncated_with_ui_link(server, pack_dir):
    cfg = _config(server, ui_url="http://ui.local")
    out = _notifier(server, pack_dir, config=cfg).send("daily_summary", {"message": "z" * 5000})
    content = server.on(SECRET_PATH)[0].body["content"]
    assert out[0].truncated and len(content) <= 2000 and content.endswith("see http://ui.local")


def test_overlay_template_replaces_the_pack_template(server, tmp_path, pack_dir, write_json):
    overlay = tmp_path / "ov"
    (overlay / "templates").mkdir(parents=True)
    (overlay / "templates" / "daily_summary.discord_webhook.md").write_text("OVERLAY {{ message }}")
    assets = load_effective(pack_dir, overlay).snapshot().assets
    ChannelNotifier(_config(server), assets, environ=_env(server)).send("daily_summary", {"message": "m"})
    assert server.on(SECRET_PATH)[0].body["content"] == "OVERLAY m"


def test_redirects_are_not_followed(server, pack_dir):
    """A webhook answering 3xx must not bounce the secret payload to another host."""
    server.status[SECRET_PATH] = 307
    out = _notifier(server, pack_dir).send("daily_summary", {"message": "m"})
    assert not out[0].ok and len(server.requests) == 1


def test_client_is_injectable(server, pack_dir):
    with httpx.Client() as client:
        out = _notifier(server, pack_dir, client=client).send("daily_summary", {"message": "m"})
    assert out[0].ok


def test_webhook_secret_never_reaches_http_client_spans(server, pack_dir):
    """Security: httpx auto-instrumentation (enabled by the composition root) must not export the webhook
    URL, which contains the secret token."""
    from tests.support.otel import make_in_memory

    o = make_in_memory()
    telemetry.configure(o.telemetry)
    o.telemetry.instrument(langchain=False, httpx=True)
    try:
        out = _notifier(server, pack_dir).send("daily_summary", {"message": "m"})
        assert out[0].ok
        text = repr([(s.name, dict(s.attributes)) for s in o.spans()])
        assert "TOPSECRETTOKEN" not in text and "notify.send" in text
    finally:
        telemetry.reset()
        o.telemetry.shutdown()


def test_notify_span_carries_the_step_attributes_of_its_sender(server, pack_dir, otel):
    """Constitution X: the delivery span (an HTTP call) has flow.id, step.id, bpmn.element, item.id,
    item.category and config.version like every LLM / tool span below a step."""
    notifier = _notifier(server, pack_dir)
    with telemetry.flow_span("triage", trigger="manual", config_version="cfg-1"):
        with telemetry.step_span("notify", "Task_NotifyTriageReady", item_id="it-1", item_category="tool"):
            notifier.send("triage_ready", {"message": "m"})
    spans = otel.spans("notify.send")
    assert len(spans) == 2
    for span in spans:
        a = span.attributes
        assert (a["flow.id"], a["step.id"]) == ("triage", "notify")
        assert a["bpmn.element"] == "Task_NotifyTriageReady"
        assert (a["item.id"], a["item.category"], a["config.version"]) == ("it-1", "tool", "cfg-1")
        assert a["pkpipe.span.kind"] == "notify"


def test_secret_stays_out_of_http_spans_when_instrumentation_starts_after_the_notifier(server, pack_dir):
    """Security: a long-lived notifier built before ``instrument()`` must still keep its client opted out."""
    from tests.support.otel import make_in_memory

    o = make_in_memory()
    telemetry.configure(o.telemetry)
    notifier = _notifier(server, pack_dir)
    o.telemetry.instrument(langchain=False, httpx=True)
    try:
        assert notifier.send("daily_summary", {"message": "m"})[0].ok
        text = repr([(s.name, dict(s.attributes)) for s in o.spans()])
        assert "TOPSECRETTOKEN" not in text and "notify.send" in text
    finally:
        telemetry.reset()
        o.telemetry.shutdown()
