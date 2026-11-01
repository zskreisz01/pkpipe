"""Spec 001 FR-003: CLI surface; validate, schema and (spec 005) run/runs are real, the rest stubs."""

from __future__ import annotations

import json

import pytest

from pkpipe import __version__
from pkpipe.cli import HANDLERS, build_parser, main

CAT = {"id": "job", "label": "Jobs", "subflow": "s", "para_path": "p", "triage_hint": "h", "sessions": []}


def test_cli_exposes_serve_run_eval_validate():
    sub = next(a for a in build_parser()._actions if a.dest == "cmd")
    assert {"serve", "run", "eval", "validate"} <= set(sub.choices)


def test_run_takes_a_flow_and_eval_a_suite():
    p = build_parser()
    assert p.parse_args(["run", "triage"]).flow == "triage"
    assert p.parse_args(["eval", "--suite", "triage"]).suite == "triage"
    assert p.parse_args(["eval"]).suite == "all"
    assert p.parse_args(["run", "--resume", "abc"]).resume == "abc"


def test_run_without_flow_or_resume_is_a_usage_error(capsys):
    assert main(["run"]) == 2
    assert "name a flow" in capsys.readouterr().err


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0 and __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: pkpipe" in capsys.readouterr().out


def test_later_specs_can_replace_a_handler(monkeypatch):
    monkeypatch.setitem(HANDLERS, "run", lambda args: 0 if args.flow == "triage" else 3)
    assert main(["run", "triage"]) == 0 and main(["run", "x"]) == 3


def test_validate_ok(pack_dir, capsys, monkeypatch):
    monkeypatch.delenv("PKPIPE_OVERLAY_DIR", raising=False)
    assert main(["validate", "--pack", str(pack_dir)]) == 0
    out = capsys.readouterr()
    assert "config ok (version " in out.out and "warning:" in out.err


def test_validate_json_output_has_full_version_hash(pack_dir, capsys, monkeypatch):
    monkeypatch.delenv("PKPIPE_OVERLAY_DIR", raising=False)
    assert main(["validate", "--pack", str(pack_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and len(payload["version"]) == 64


def test_validate_reports_each_error_and_exits_1(pack_dir, overlay_dir, write_json, capsys):
    write_json(overlay_dir, "schedules.json", [{"flow": "triage", "cron": "not a cron"}])
    assert main(["validate", "--pack", str(pack_dir), "--overlay", str(overlay_dir)]) == 1
    err = capsys.readouterr().err
    assert "schedules.json" in err and "cron" in err and "invalid config" in err


def test_validate_names_the_file_of_a_duplicate_category(pack_dir, overlay_dir, write_json, capsys):
    write_json(overlay_dir, "categories.json", [CAT, CAT])
    assert main(["validate", "--pack", str(pack_dir), "--overlay", str(overlay_dir), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and "categories.json" in payload["errors"][0]


def test_schema_command_writes_one_schema_per_config_file(tmp_path, capsys):
    assert main(["schema", "--out", str(tmp_path / "s")]) == 0
    names = {p.name for p in (tmp_path / "s").iterdir()}
    assert names == {
        f"{n}.schema.json"
        for n in (
            "models",
            "mcp_servers",
            "channels",
            "schedules",
            "categories",
            "security",
            "telemetry",
            "evals",
            "flow",
        )
    } | {
        "triage-row.schema.json",
        "processing-list-row.schema.json",
        "improvement-item.schema.json",
        "fetch-result.schema.json",
        "reader-extract.schema.json",
        "verdict.schema.json",
        "output-guard-result.schema.json",
        "malicious-url-entry.schema.json",
    }
    json.loads((tmp_path / "s" / "models.schema.json").read_text())


def test_schema_command_exports_triage_and_processing_row_schemas(tmp_path):
    """Spec 004 data-model.md: row schemas are exported and match the models (required fields, state enum)."""
    from pkpipe.domain.triage import ItemState
    from pkpipe.obsidian.models import ProcessingListRow, ProcessingStatus, TriageRow

    assert main(["schema", "--out", str(tmp_path)]) == 0
    triage = json.loads((tmp_path / "triage-row.schema.json").read_text())
    proc = json.loads((tmp_path / "processing-list-row.schema.json").read_text())
    assert triage == TriageRow.model_json_schema() and proc == ProcessingListRow.model_json_schema()
    assert set(triage["required"]) == {"item_id", "resource", "ai_suggested_category"}
    assert set(proc["required"]) == {"item_id", "category", "obsidian_link"}
    assert set(triage["$defs"]["ItemState"]["enum"]) == {s.value for s in ItemState}
    assert set(proc["$defs"]["ProcessingStatus"]["enum"]) == {s.value for s in ProcessingStatus}


def test_serve_is_wired_with_loopback_defaults():
    """Spec 014 FR-001: ``serve`` is a real command that listens on 127.0.0.1 unless told otherwise."""
    args = build_parser().parse_args(["serve"])
    assert args.host == "127.0.0.1" and args.port == 8787 and args.no_scheduler is False
    assert HANDLERS["serve"].__name__ == "_serve"


def test_schema_command_exports_the_improvement_item_schema(tmp_path):
    """Spec 015 data-model.md: the backlog item schema matches the model (required fields, enums)."""
    from pkpipe.domain.improvement import Area, ImprovementItem, ItemType, Status

    assert main(["schema", "--out", str(tmp_path)]) == 0
    item = json.loads((tmp_path / "improvement-item.schema.json").read_text())
    assert item == ImprovementItem.model_json_schema()
    assert set(item["required"]) == {"id", "source_note", "date", "statement", "area", "type"}
    assert set(item["$defs"]["Status"]["enum"]) == {s.value for s in Status}
    assert set(item["$defs"]["Area"]["enum"]) == {a.value for a in Area}
    assert set(item["$defs"]["ItemType"]["enum"]) == {t.value for t in ItemType}
