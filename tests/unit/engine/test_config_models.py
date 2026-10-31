"""Spec 001 T003: Pydantic models and JSON Schema export for every config file."""

from __future__ import annotations

import json

import jsonschema
import pytest
from pydantic import ValidationError

from pkpipe.engine.config_models import (
    CONFIG_MODELS,
    CategoriesConfig,
    ChannelsConfig,
    McpServersConfig,
    ModelsConfig,
    SchedulesConfig,
    SecurityConfig,
    config_json_schemas,
    validate_effective,
)


def test_every_pack_json_file_has_a_model_and_validates(pack_dir):
    files = {p.stem for p in pack_dir.glob("*.json")}
    assert files == set(CONFIG_MODELS), "a config file without model (or a model without file)"
    for stem in files:
        CONFIG_MODELS[stem].model_validate(json.loads((pack_dir / f"{stem}.json").read_text()))


def test_exported_json_schemas_are_valid_and_accept_the_pack(pack_dir):
    schemas = config_json_schemas()
    assert set(schemas) == set(CONFIG_MODELS) | {"flow"}
    for stem, schema in schemas.items():
        jsonschema.Draft202012Validator.check_schema(schema)
        files = sorted((pack_dir / "flows").glob("*.json")) if stem == "flow" else [pack_dir / f"{stem}.json"]
        assert files
        for f in files:
            jsonschema.validate(json.loads(f.read_text()), schema)


def test_exported_json_schemas_reject_what_the_models_reject():
    bad = {"prompt_guard": {"model": "m", "threshold": 7, "chunk_tokens": 1}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, config_json_schemas()["security"])


def test_unknown_keys_are_rejected_everywhere():
    with pytest.raises(ValidationError):
        ModelsConfig.model_validate({"default": {"base_url": "x", "model": "y", "typo": 1}})
    with pytest.raises(ValidationError):
        SchedulesConfig.model_validate([{"cron": "0 1 * * *", "flow": "a", "when": "now"}])


def test_model_profile_fallback_must_exist():
    ok = {"a": {"base_url": "x", "model": "m", "fallback": "b"}, "b": {"base_url": "x", "model": "n"}}
    ModelsConfig.model_validate(ok)
    del ok["b"]
    with pytest.raises(ValidationError, match="unknown fallback 'b'"):
        ModelsConfig.model_validate(ok)


def test_model_temperature_range():
    with pytest.raises(ValidationError):
        ModelsConfig.model_validate({"a": {"base_url": "x", "model": "m", "temperature": 5}})


@pytest.mark.parametrize("cron", ["0 6 * * *", "*/5 * * * *", "0 19 * * 0", "0 8-18 * * 1-5", "0,30 * * * *"])
def test_valid_cron(cron):
    SchedulesConfig.model_validate([{"cron": cron, "flow": "f"}])


@pytest.mark.parametrize("cron", ["every day", "0 6 * *", "0 6 * * * *", "a b c d e", ""])
def test_invalid_cron(cron):
    with pytest.raises(ValidationError, match="cron"):
        SchedulesConfig.model_validate([{"cron": cron, "flow": "f"}])


def test_duplicate_schedule_flow_rejected():
    with pytest.raises(ValidationError, match="duplicate schedule"):
        SchedulesConfig.model_validate(
            [{"cron": "0 1 * * *", "flow": "f"}, {"cron": "0 2 * * *", "flow": "f"}]
        )


def test_mcp_transport_requirements():
    McpServersConfig.model_validate({"s": {"transport": "streamable_http", "url": "http://127.0.0.1:1/mcp"}})
    McpServersConfig.model_validate({"s": {"transport": "stdio", "command": "uv", "args": ["run", "x"]}})
    with pytest.raises(ValidationError, match="requires 'url'"):
        McpServersConfig.model_validate({"s": {"transport": "streamable_http"}})
    with pytest.raises(ValidationError, match="requires 'command'"):
        McpServersConfig.model_validate({"s": {"transport": "stdio"}})
    with pytest.raises(ValidationError):
        McpServersConfig.model_validate({"s": {"transport": "carrier_pigeon", "url": "x"}})


def test_channel_routes_must_reference_defined_channels():
    ChannelsConfig.model_validate({"channels": {"d": {"type": "discord_webhook"}}, "routes": {"e": ["d"]}})
    with pytest.raises(ValidationError, match="unknown channel 'zzz'"):
        ChannelsConfig.model_validate({"channels": {"d": {"type": "x"}}, "routes": {"e": ["zzz"]}})


def test_channel_url_must_not_inline_credentials():
    """Secrets are never in config (docs/configuration.md): only env:NAME references."""
    ChannelsConfig.model_validate({"channels": {"d": {"type": "x", "url": "env:DISCORD_WEBHOOK_URL"}}})
    with pytest.raises(ValidationError, match="credentials"):
        ChannelsConfig.model_validate({"channels": {"d": {"type": "x", "url": "https://user:pw@host/hook"}}})


def _cat(**over):
    base = {"id": "c", "label": "C", "subflow": "s", "para_path": "p", "triage_hint": "h", "sessions": []}
    return {**base, **over}


def test_category_requires_core_fields_and_defaults_enabled():
    cat = CategoriesConfig.model_validate([_cat()]).root[0]
    assert cat.enabled is True
    bad = _cat()
    del bad["para_path"]
    with pytest.raises(ValidationError, match="para_path"):
        CategoriesConfig.model_validate([bad])


def test_session_tool_format_and_duplicate_ids():
    s = {"id": "a", "system": "analyst", "instruction": "x.md"}
    CategoriesConfig.model_validate([_cat(sessions=[{**s, "tools": ["websearch:search"]}])])
    for tool in ("websearch", "a:b:c", ":search", "websearch:"):
        with pytest.raises(ValidationError, match="<server>:<tool>"):
            CategoriesConfig.model_validate([_cat(sessions=[{**s, "tools": [tool]}])])
    with pytest.raises(ValidationError, match="duplicate session"):
        CategoriesConfig.model_validate([_cat(sessions=[s, s])])


def test_security_thresholds_are_bounded():
    good = json.loads(
        json.dumps(
            {
                "prompt_guard": {"model": "m", "threshold": 0.5, "chunk_tokens": 512},
                "output_guard": {"model": "m", "block_on": ["Unsafe"]},
                "canary": {"rotate": "per_run"},
                "url_filter": {"max_bytes": 10},
                "quarantine_dir": "q",
            }
        )
    )
    cfg = SecurityConfig.model_validate(good)
    assert cfg.url_filter.block_private_networks is True  # secure by default
    good["prompt_guard"]["threshold"] = 1.5
    with pytest.raises(ValidationError):
        SecurityConfig.model_validate(good)


def test_validate_effective_reports_file_and_path():
    errors = validate_effective({"schedules": [{"cron": "nope", "flow": "f"}]})
    assert errors and errors[0].startswith("schedules.json: 0.cron")


def test_validate_effective_valid_config_has_no_errors(pack_dir):
    from pkpipe.engine.registry import load_config

    assert validate_effective(load_config(pack_dir)) == []
