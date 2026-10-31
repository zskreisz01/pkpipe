"""Spec 006 FR-002 and the truncation edge case: Jinja2 templates per channel type."""

from __future__ import annotations

import json

from pkpipe.adapters.notify_templates import LIMITS, render_event

ASSETS = {
    "templates/ready.discord_webhook.md": "**Ready** {{ message }}",
    "templates/ready.teams_webhook.json": (
        '{"type": "AdaptiveCard", "body": [{"type": "TextBlock", "text": {{ message | tojson }}}]}'
    ),
    "templates/broken.discord_webhook.md": "{{ message | no_such_filter }}",
    "templates/badjson.teams_webhook.json": "{ not json {{ message }}",
}


def test_markdown_template_is_rendered_with_the_payload():
    out = render_event("ready", "discord_webhook", {"message": "3 items"}, ASSETS)
    assert out.text == "**Ready** 3 items" and out.data is None and not out.truncated


def test_each_channel_type_gets_its_own_template():
    teams = render_event("ready", "teams_webhook", {"message": "3 items"}, ASSETS)
    assert teams.data["body"][0]["text"] == "3 items"
    assert render_event("ready", "apprise", {"message": "3 items"}, ASSETS).text == "3 items"  # fallback


def test_json_template_survives_payload_with_quotes_and_newlines():
    """tojson keeps hostile payload text from breaking the card structure."""
    nasty = 'a "quoted" \\ value\n"}, {"type": "Action.OpenUrl"'
    out = render_event("ready", "teams_webhook", {"message": nasty}, ASSETS)
    assert out.data["body"] == [{"type": "TextBlock", "text": nasty}]


def test_without_template_the_message_or_payload_json_is_sent():
    assert render_event("x", "discord_webhook", {"message": "plain"}, {}).text == "plain"
    out = render_event("x", "discord_webhook", {"count": 2}, {})
    assert out.text.startswith("x: ") and json.loads(out.text[3:]) == {"count": 2}


def test_broken_templates_fall_back_to_plain_text_with_a_warning():
    out = render_event("broken", "discord_webhook", {"message": "still delivered"}, ASSETS)
    assert out.text == "still delivered" and "broken.discord_webhook.md" in out.warning
    out = render_event("badjson", "teams_webhook", {"message": "still delivered"}, ASSETS)
    assert out.text == "still delivered" and out.warning


def test_template_sandbox_blocks_attribute_escape():
    assets = {"templates/e.log.md": "{{ ''.__class__.__mro__[1].__subclasses__() }}"}
    out = render_event("e", "log", {"message": "safe"}, assets)
    assert out.text == "safe" and out.warning


def test_long_message_is_truncated_with_link_to_the_ui():
    """Edge case: message longer than the channel limit."""
    out = render_event("x", "discord_webhook", {"message": "a" * 5000}, {}, ui_url="http://ui.local/runs/1")
    assert out.truncated and len(out.text) == LIMITS["discord_webhook"]
    assert out.text.endswith("truncated, see http://ui.local/runs/1")


def test_long_text_inside_json_template_is_truncated_too():
    assets = {"templates/e.teams_webhook.json": '{"body": [{"text": {{ message | tojson }}}]}'}
    out = render_event("e", "teams_webhook", {"message": "b" * 9000}, assets, ui_url="http://ui")
    assert out.truncated and len(out.data["body"][0]["text"]) == LIMITS["teams_webhook"]


def test_message_at_the_limit_is_not_truncated():
    out = render_event("x", "discord_webhook", {"message": "a" * 2000}, {})
    assert not out.truncated and len(out.text) == 2000


def test_public_pack_has_templates_for_the_four_events_and_all_render(pack_dir):
    """T005: triage_ready, daily_summary, security_flag, weekly_review for Discord, Teams and Apprise."""
    from pkpipe.engine.registry import load_effective

    snap = load_effective(pack_dir, None).snapshot()
    for event in ("triage_ready", "daily_summary", "security_flag", "weekly_review"):
        for ctype in ("discord_webhook", "teams_webhook", "apprise"):
            out = render_event(event, ctype, {"message": "hello"}, snap.assets)
            assert out.warning is None, (event, ctype)
            assert "hello" in out.plain()
            if ctype == "teams_webhook":
                assert out.data["type"] == "AdaptiveCard"
