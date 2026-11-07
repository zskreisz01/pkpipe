"""Spec 026: the schema-driven form engine (no web layer)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from pkpipe.api import forms
from pkpipe.engine import config_models
from pkpipe.engine.config_models import CONFIG_MODELS

PACK = Path(__file__).resolve().parents[3] / "packs" / "learning-inbox"


def post_of(node: forms.Node) -> dict[str, str]:
    """What a browser posts for the rendered fields (every input, as it is shown)."""
    flat: dict[str, str] = {}

    def walk(n: forms.Node) -> None:
        if n.kind == "object":
            for c in n.children:
                walk(c)
        elif n.kind in ("array", "map"):
            for c in n.children:
                if n.kind == "map":
                    flat[c.extra["key_name"]] = c.key or ""
                walk(c)
        elif n.kind == "boolean":
            flat[n.name] = "" if not n.present else ("true" if n.value else "false")
        else:
            flat[n.name] = "" if n.value is None else str(n.value)

    walk(node)
    return flat


@pytest.mark.parametrize("stem", [s for s in CONFIG_MODELS if (PACK / f"{s}.json").exists()])
def test_every_pack_config_file_round_trips_through_its_form(stem):
    data = json.loads((PACK / f"{stem}.json").read_text())
    parsed = forms.parse_form(stem, post_of(forms.build_form(stem, data)))
    assert forms.validate_parsed(stem, parsed) == {}
    model = CONFIG_MODELS[stem]
    assert model.model_validate(parsed.data).model_dump() == model.model_validate(data).model_dump()


def test_a_changed_field_is_the_only_change_and_defaults_are_not_written_out():
    data = json.loads((PACK / "schedules.json").read_text())
    flat = post_of(forms.build_form("schedules", data))
    flat["f.0.cron"] = "15 7 * * *"
    parsed = forms.parse_form("schedules", flat)
    assert parsed.data[0]["cron"] == "15 7 * * *" and parsed.data[1:] == data[1:]
    assert "misfire_grace_s" not in parsed.data[0] or parsed.data[0]["misfire_grace_s"] == data[0].get(
        "misfire_grace_s"
    )


def test_field_errors_are_attached_to_the_field_that_caused_them():
    data = json.loads((PACK / "categories.json").read_text())
    flat = post_of(forms.build_form("categories", data))
    flat["f.0.label"] = ""  # required
    flat["f.1.enabled"] = "maybe"
    parsed = forms.parse_form("categories", flat)
    errors = forms.validate_parsed("categories", parsed)
    assert "f.0.label" in errors
    node = forms.build_form("categories", parsed.data)
    forms.apply_errors(node, errors)
    assert node.children[0].children[1].error


def test_numbers_and_enums_are_checked_and_bad_json_in_a_free_field_is_reported():
    data = json.loads((PACK / "images.json").read_text())
    flat = post_of(forms.build_form("images", data))
    flat["f.max_side"] = "wide"
    assert forms.parse_form("images", flat).errors["f.max_side"] == "a whole number is required"
    flat["f.max_side"] = "-5"
    assert "f.max_side" in forms.validate_parsed("images", forms.parse_form("images", flat))
    flat = post_of(forms.build_form("triage", {"surface": "web"}))
    flat["f.surface"] = "carrier-pigeon"
    assert "f.surface" in forms.parse_form("triage", flat).errors


def test_rows_are_added_removed_and_moved_without_losing_what_was_typed():
    flat = {
        "f.0.cron": "0 20 * * *",
        "f.0.flow": "triage",
        "f.1.cron": "0 6 * * *",
        "f.1.flow": "process_released",
    }
    added = forms.apply_action("schedules", flat, "add:f")
    assert added["f.0.flow"] == "triage" and "f.2.__k" in added
    moved = forms.apply_action("schedules", flat, "up:f:1")
    assert moved["f.0.flow"] == "process_released" and moved["f.1.flow"] == "triage"
    down = forms.apply_action("schedules", flat, "down:f:0")
    assert down["f.0.flow"] == "process_released"
    removed = forms.apply_action("schedules", flat, "del:f:0")
    assert removed == {"f.0.cron": "0 6 * * *", "f.0.flow": "process_released"}
    with pytest.raises(forms.FormError):
        forms.apply_action("schedules", flat, "del:f:9")
    with pytest.raises(forms.FormError):
        forms.apply_action("schedules", flat, "add:../../etc")
    with pytest.raises(forms.FormError):
        forms.apply_action("schedules", flat, "drop:f:0")


def test_a_map_keeps_its_names_and_refuses_duplicates_and_empty_names():
    data = {"channels": {"main": {"type": "log"}, "side": {"type": "log"}}, "routes": {}}
    flat = post_of(forms.build_form("channels", data))
    renamed = dict(flat, **{"f.channels.1.__k": "main"})
    assert "is used twice" in forms.parse_form("channels", renamed).errors["f.channels.1.__k"]
    parsed = forms.parse_form("channels", dict(flat, **{"f.channels.0.__k": "primary"}))
    assert list(parsed.data["channels"]) == ["primary", "side"]


def test_secrets_are_only_env_references_and_literals_are_never_shown():
    flat = post_of(forms.build_form("channels", {"channels": {"d": {"type": "discord_webhook"}}}))
    flat["f.channels.0.url"] = "https://discord.com/api/webhooks/123/abcdef"
    errors = forms.parse_form("channels", flat).errors
    assert "never the secret itself" in errors["f.channels.0.url"]
    flat["f.channels.0.url"] = "env:DISCORD_WEBHOOK_URL"
    assert forms.parse_form("channels", flat).errors == {}
    apprise = dict(flat, **{"f.channels.0.urls": "env:A\nmailto://me:pw@example.org"})
    assert "f.channels.0.urls" in forms.parse_form("channels", apprise).errors
    stored = {"channels": {"d": {"type": "discord_webhook", "url": "https://hooks.example/SECRET-LITERAL"}}}
    node = forms.build_form("channels", stored)
    rendered = json.dumps(post_of(node))
    assert "SECRET-LITERAL" not in rendered
    assert forms.literal_secrets("channels", stored) == ["channels.d.url"]
    assert forms.literal_secrets("channels", {"channels": {"d": {"type": "log", "token": "env:T"}}}) == []


def test_channel_id_is_not_a_secret():
    flat = post_of(forms.build_form("channels", {"channels": {"b": {"type": "discord_bot"}}}))
    flat["f.channels.0.channel_id"] = "123456789"
    flat["f.channels.0.token"] = "env:DISCORD_BOT_TOKEN"
    assert forms.parse_form("channels", flat).errors == {}


def test_a_section_without_an_overlay_file_saves_only_what_changed():
    pack = [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]
    changed = [{"id": "a", "label": "A"}, {"id": "b", "label": "Bee"}, {"id": "c", "label": "C"}]
    assert forms.minimize("categories", pack, changed) == [
        {"id": "b", "label": "Bee"},
        {"id": "c", "label": "C"},
    ]
    assert forms.minimize("categories", pack, pack) == []
    assert forms.minimize("ui", {"public_url": None, "allowed_hosts": []}, {"public_url": "http://x"}) == {
        "public_url": "http://x"
    }
    nested = forms.minimize("security", {"url_filter": {"a": 1, "b": 2}}, {"url_filter": {"a": 1, "b": 3}})
    assert nested == {"url_filter": {"b": 3}}


def test_cron_preview_lists_the_next_runs_or_says_why_not():
    ok = forms.cron_preview("0 6 * * *")
    assert ok["ok"] and len(ok["times"]) == 3 and "06:00" in ok["times"][0]
    bad = forms.cron_preview("every day")
    assert not bad["ok"] and bad["error"]
    assert not forms.cron_preview("")["ok"]


def test_instruction_hint_counts_tokens_against_the_budget():
    assert forms.instruction_hint("x" * 400) == {"tokens": 100, "budget": 800, "over": False}
    assert forms.instruction_hint("x" * 4000)["over"] is True


def test_asset_paths_follow_the_session_conventions():
    assert forms.asset_path("instruction", "tool/research.md") == "instructions/tool/research.md"
    assert forms.asset_path("system", "analyst") == "prompts/system/analyst.md"
    assert forms.asset_path("context", "personal") == "contexts/personal.md"


def test_a_new_config_model_gets_a_form_without_new_code(monkeypatch):
    class Demo(BaseModel):
        model_config = ConfigDict(extra="forbid")
        enabled: bool = True
        per_category: dict[str, list[str]] = Field(default_factory=dict)
        evaluate: list[dict[str, str]] = Field(default_factory=list)  # a shape without a widget: JSON field

    monkeypatch.setitem(CONFIG_MODELS, "source_discovery_demo", Demo)
    monkeypatch.setattr(forms, "CONFIG_MODELS", CONFIG_MODELS)
    monkeypatch.setattr(forms, "config_json_schemas", config_models.config_json_schemas)
    node = forms.build_form("source_discovery_demo", {"per_category": {"tool": ["evaluate"]}})
    kinds = {c.label: c.kind for c in node.children}
    assert kinds == {"enabled": "boolean", "per category": "map", "evaluate": "json"}
    parsed = forms.parse_form("source_discovery_demo", post_of(node))
    assert parsed.data == {"per_category": {"tool": ["evaluate"]}}


def test_an_overlay_layer_is_validated_merged_onto_the_pack_file():
    pack = {"channels": {"discord_main": {"type": "log"}}, "routes": {}}
    layer = {"channels": {"extra": {"type": "log"}}, "routes": {"triage_ready": ["discord_main"]}}
    flat = post_of(forms.build_form("channels", layer))
    parsed = forms.parse_form("channels", flat)
    assert forms.validate_parsed("channels", parsed)  # alone, the route names an unknown channel
    assert forms.validate_parsed("channels", parsed, base=pack) == {}  # with the pack channel it is fine


def test_an_error_of_a_merged_entry_is_attached_to_its_row_by_key():
    pack = [{"id": "a", "label": "A", "subflow": "s", "para_path": "p", "triage_hint": "h"}]
    layer = [
        {
            "id": "b",
            "label": "B",
            "subflow": "s",
            "para_path": "p",
            "triage_hint": "h",
            "sessions": [{"id": "x"}],
        }
    ]
    parsed = forms.parse_form("categories", post_of(forms.build_form("categories", layer)))
    errors = forms.validate_parsed("categories", parsed, base=pack)
    assert any(name.startswith("f.0.sessions.0") for name in errors), errors
