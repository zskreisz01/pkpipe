"""Spec 001 T003: exported JSON Schemas (what the config UI uses) agree with the pack and the loader."""

from __future__ import annotations

import json

import jsonschema

from pkpipe.cli import main


def test_exported_schema_files_validate_the_pack_and_reject_a_broken_overlay(tmp_path, pack_dir):
    out = tmp_path / "schemas"
    assert main(["schema", "--out", str(out)]) == 0
    for schema_file in out.glob("*.schema.json"):
        schema = json.loads(schema_file.read_text())
        stem = schema_file.name.removesuffix(".schema.json")
        jsonschema.validate(json.loads((pack_dir / f"{stem}.json").read_text()), schema)
    schedules = json.loads((out / "schedules.schema.json").read_text())
    bad = [{"flow": "triage", "cron": "0 6 * * *", "surprise": True}]
    try:
        jsonschema.validate(bad, schedules)
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError("schema must forbid unknown keys like the model does")
