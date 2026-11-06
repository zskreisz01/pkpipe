"""Spec 019 FR-009: ``pkpipe doctor`` reports the snapshot directory (free space, newest snapshot, size)."""

from __future__ import annotations

import json

import pytest

from pkpipe import app
from pkpipe.cli import main
from pkpipe.engine.registry import load_effective
from pkpipe.system import cli as doctor_cli
from tests.fakes import FakeSystemProbe


@pytest.fixture(autouse=True)
def probe(monkeypatch, tmp_path):
    fake = FakeSystemProbe()
    monkeypatch.setattr(doctor_cli, "probe_factory", lambda base_url: fake)
    monkeypatch.setenv("PKPIPE_DATA_DIR", str(tmp_path / "data"))
    return fake


def test_doctor_without_snapshots_says_none_are_kept(capsys):
    assert main(["doctor", "--no-llm"]) in (0, 1)
    line = next(x for x in capsys.readouterr().out.splitlines() if x.startswith("snapshots"))
    assert "0 kept" in line and "free" in line and "limit 1,024 MiB, 14 days" in line


def test_doctor_reports_size_count_and_the_newest_snapshot(capsys, tmp_path, synthetic_vault, pack_dir):
    port = app.make_snapshots(load_effective(pack_dir, None).snapshot(), tmp_path / "app.db", synthetic_vault)
    port.take("5f0c2c3e-1111-4000-8000-000000000001", "triage")
    assert main(["doctor", "--no-llm"]) in (0, 1)
    line = next(x for x in capsys.readouterr().out.splitlines() if x.startswith("snapshots"))
    assert (
        "1 kept" in line
        and "newest 5f0c2c3e (triage)" in line
        and str(tmp_path / "data" / "snapshots") in line
    )
    assert main(["doctor", "--no-llm", "--json"]) in (0, 1)
    info = json.loads(capsys.readouterr().out)["snapshots"]
    assert info["snapshots"] == 1 and info["bytes"] > 0 and info["free_bytes"] > 0
    assert info["newest"]["flow_id"] == "triage"
