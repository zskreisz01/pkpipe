"""Spec 001 FR-003: CLI surface; `validate` is real, other commands are registered stubs."""

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
    with pytest.raises(SystemExit):
        p.parse_args(["run"])


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0 and __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: pkpipe" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["serve"], ["run", "triage"], ["eval"]])
def test_unwired_commands_fail_loudly_not_silently(argv, capsys):
    assert main(argv) == 1
    assert "not implemented" in capsys.readouterr().err


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
        for n in ("models", "mcp_servers", "channels", "schedules", "categories", "security")
    }
    json.loads((tmp_path / "s" / "models.schema.json").read_text())
