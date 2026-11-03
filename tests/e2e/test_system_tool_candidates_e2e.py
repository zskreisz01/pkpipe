"""Spec 016 end to end through the real entry points on synthetic data: the obsidian-vault MCP server
(``read_tool_candidates`` / ``update_tool_candidates``) and the ``pkpipe candidates`` CLI (list, A/B spike,
ADR, status), from a candidate on the list to an adoption that needs an ADR and a won A/B, or a rejection with
a reason that keeps the tool from being proposed again."""

from __future__ import annotations

import json
import sys

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from pkpipe.domain import tool_candidate as tc
from tests.conftest import REPO
from tests.e2e.test_cli_validate import UV, pkpipe
from tests.support.vault_mcp import call, write_policy

pytestmark = pytest.mark.skipif(UV is None, reason="uv not installed")
ENTRY = REPO / "mcp_servers" / "obsidian_vault" / "server.py"
ROW = {
    "tool": "Metasea",
    "evaluation_note": "3-Resources/Tools/metasea-1.md",
    "component": "Web search",
    "benefit": "A metasearch engine with a JSON API could replace the search provider.",
    "effort": "S",
}
NO_MODEL = {"PKPIPE_MODEL_BASE_URL": "http://127.0.0.1:9/v1"}  # nothing listens there


def _add_through_mcp(tmp_path, vault, rows):
    cfg = write_policy(tmp_path, vault)
    transport = StdioTransport(sys.executable, [str(ENTRY), "--config", str(cfg), "--transport", "stdio"])

    async def go():
        async with Client(transport) as c:
            return [await call(c, "update_tool_candidates", rows=r) for r in rows], await call(
                c, "read_tool_candidates"
            )

    import asyncio

    return asyncio.run(go())


def _cli(vault, adr_dir, *args, env=None):
    return pkpipe("candidates", *args, "--vault", str(vault), "--adr-dir", str(adr_dir), env_extra=env)


@pytest.fixture
def listed(tmp_path, synthetic_vault):
    results, _ = _add_through_mcp(tmp_path, synthetic_vault, [[ROW]])
    assert results[0][0] is False
    return synthetic_vault, tmp_path / "adr"


def test_the_vault_server_adds_candidates_but_never_changes_an_existing_row(tmp_path, synthetic_vault):
    """FR-001 / SC-001 through the MCP entry point: new rows are `candidate`; an agent cannot start in another
    status, cannot set owner fields and cannot overwrite a row (a rejected tool stays rejected)."""
    (first,), _ = _add_through_mcp(tmp_path, synthetic_vault, [[ROW]])
    assert first[0] is False and first[1]["inserted"] == ["metasea"]
    (bad_status,), _ = _add_through_mcp(tmp_path, synthetic_vault, [[{**ROW, "status": "adopted"}]])
    assert bad_status[0] and "candidate" in bad_status[1]
    (bad_field,), _ = _add_through_mcp(tmp_path, synthetic_vault, [[{**ROW, "tool": "Other", "reason": "x"}]])
    assert bad_field[0] and "owner" in bad_field[1]
    path = synthetic_vault / tc.CANDIDATES_FILE
    path.write_text(path.read_text("utf-8").replace("| candidate |", "| rejected |", 1), "utf-8")
    (again,), (_, listing) = _add_through_mcp(tmp_path, synthetic_vault, [[{**ROW, "benefit": "new"}]])
    assert again[1]["unchanged"] == ["metasea"] and listing["rows"][0]["status"] == "rejected"
    assert listing["rows"][0]["benefit"] != "new"


def test_cli_list_shows_the_candidates_and_the_rules_hand_edits_break(listed):
    vault, adr = listed
    r = _cli(vault, adr, "list")
    assert (
        r.returncode == 0 and "candidate" in r.stdout and "Metasea" in r.stdout and "Web search" in r.stdout
    )
    path = vault / tc.CANDIDATES_FILE
    path.write_text(path.read_text(encoding="utf-8").replace("| candidate |", "| adopted |", 1), "utf-8")
    r = _cli(vault, adr, "list")
    assert r.returncode == 1 and "adopted without an ADR" in r.stderr
    assert json.loads(_cli(vault, adr, "list", "--json").stdout)["violations"]


def _report(tmp_path, verdict="better", gate_green=True, tool="Metasea"):
    path = tmp_path / "ab.json"
    path.write_text(
        json.dumps(
            {
                "tool": tool,
                "verdict": verdict,
                "gate_green": gate_green,
                "baseline_version": "a",
                "candidate_version": "b",
                "suites": ["sessions"],
                "reasons": ["sessions.judge_mean 4.0 -> 4.5"],
                "metrics": [
                    {
                        "suite": "sessions",
                        "metric": "judge_mean",
                        "direction": "higher",
                        "baseline": 4.0,
                        "candidate": 4.5,
                        "verdict": "better",
                    }
                ],
                "pairwise": [{"session": "tool.compare", "score": 0.75, "outcomes": {}}],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_adoption_needs_the_adr_and_a_won_ab_and_is_recorded_in_the_list(listed, tmp_path):
    """US2 / FR-002: candidate -> investigating -> trial -> adopted, each step checked by code."""
    vault, adr_dir = listed
    ok = _cli(vault, adr_dir, "set", "metasea", "--status", "investigating")
    assert ok.returncode == 0 and "candidate -> investigating" in ok.stdout
    skip = _cli(vault, adr_dir, "set", "metasea", "--status", "adopted")
    assert skip.returncode == 1 and "not allowed" in skip.stderr  # no jumping to adopted
    assert _cli(vault, adr_dir, "set", "metasea", "--status", "trial").returncode == 0
    no_adr = _cli(vault, adr_dir, "set", "metasea", "--status", "adopted")
    assert no_adr.returncode == 1 and "needs an ADR" in no_adr.stderr
    worse = _report(tmp_path, verdict="worse")
    r = _cli(vault, adr_dir, "adr", "metasea", "--ab-report", str(worse))
    assert r.returncode == 0
    adr = adr_dir / "0001-adopt-metasea.md"
    assert adr.is_file() and "# 0001: Adopt Metasea for Web search" in adr.read_text(encoding="utf-8")
    r = _cli(
        vault, adr_dir, "set", "metasea", "--status", "adopted", "--adr", adr.name, "--ab-report", str(worse)
    )
    assert r.returncode == 1 and "needs 'better'" in r.stderr
    red = _report(tmp_path, gate_green=False)
    r = _cli(
        vault, adr_dir, "set", "metasea", "--status", "adopted", "--adr", adr.name, "--ab-report", str(red)
    )
    assert r.returncode == 1 and "gate is not green" in r.stderr
    wrong_tool = _report(tmp_path, tool="Other")
    r = _cli(
        vault,
        adr_dir,
        "set",
        "metasea",
        "--status",
        "adopted",
        "--adr",
        adr.name,
        "--ab-report",
        str(wrong_tool),
    )
    assert r.returncode == 1 and "not 'Metasea'" in r.stderr
    no_tool = _report(tmp_path, tool="")  # a report that names no tool is evidence for none
    r = _cli(
        vault,
        adr_dir,
        "set",
        "metasea",
        "--status",
        "adopted",
        "--adr",
        adr.name,
        "--ab-report",
        str(no_tool),
    )
    assert r.returncode == 1 and "not 'Metasea'" in r.stderr
    good = _report(tmp_path)
    r = _cli(
        vault, adr_dir, "set", "metasea", "--status", "adopted", "--adr", adr.name, "--ab-report", str(good)
    )
    assert r.returncode == 0, r.stderr
    [row] = json.loads(_cli(vault, adr_dir, "list", "--json").stdout)["rows"]
    assert row["status"] == "adopted" and row["adr"] == adr.name and row["ab"].startswith("better")
    assert json.loads(_cli(vault, adr_dir, "list", "--json").stdout)["violations"] == []
    r = _cli(vault, adr_dir, "set", "metasea", "--status", "rejected", "--reason", "changed my mind")
    assert r.returncode == 1  # nothing leaves adopted


def test_a_forged_adr_name_or_a_missing_file_does_not_count(listed, tmp_path):
    vault, adr_dir = listed
    for status in ("investigating", "trial"):
        assert _cli(vault, adr_dir, "set", "metasea", "--status", status).returncode == 0
    good = _report(tmp_path)
    for name in ("0099-nothing.md", "../../etc/passwd.md", "notes.txt"):
        r = _cli(
            vault, adr_dir, "set", "metasea", "--status", "adopted", "--adr", name, "--ab-report", str(good)
        )
        assert r.returncode == 1 and "needs an ADR" in r.stderr, name


def test_a_rejection_needs_a_reason_and_the_tool_comes_back_only_with_evidence(listed):
    """US2-AS1 / SC-001."""
    vault, adr = listed
    assert "reason" in _cli(vault, adr, "set", "metasea", "--status", "rejected").stderr
    r = _cli(vault, adr, "set", "metasea", "--status", "rejected", "--reason", "A/B worse on tool.compare")
    assert r.returncode == 0
    again = _cli(vault, adr, "set", "metasea", "--status", "candidate")
    assert again.returncode == 1 and "new evidence" in again.stderr
    ok = _cli(vault, adr, "set", "metasea", "--status", "candidate", "--evidence", "v2 fixes the failure")
    assert ok.returncode == 0
    assert _cli(vault, adr, "set", "ghost", "--status", "investigating").returncode == 1


def test_ab_cli_compares_two_configs_on_the_eval_suite_and_reports_the_verdict(tmp_path):
    """US2: `pkpipe candidates ab` runs the same suite on both configs. The current config here is a broken
    spike (a category disabled by an overlay), the candidate the shipped pack: the shipped pack is better."""
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "categories.json").write_text(json.dumps([{"id": "tool", "enabled": False}]), encoding="utf-8")
    out = tmp_path / "ab.json"
    r = pkpipe(
        "candidates", "ab", "marketing-tool", "--overlay", str(broken), "--suite", "deterministic",
        "--out", str(out), env_extra=NO_MODEL,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["verdict"] == "better" and report["gate_green"] and report["tool"] == "marketing-tool"
    assert any(m["metric"] == "pass_rate" and m["verdict"] == "better" for m in report["metrics"])
    # the other direction: the shipped pack as the current component, the broken spike as the candidate
    out2 = tmp_path / "ab2.json"
    r = pkpipe(
        "candidates", "ab", "marketing-tool", "--candidate-overlay", str(broken), "--suite", "deterministic",
        "--out", str(out2), env_extra=NO_MODEL,
    )  # fmt: skip
    worse = json.loads(out2.read_text(encoding="utf-8"))
    assert r.returncode == 1 and worse["verdict"] == "worse" and not worse["gate_green"]
    # no difference is not a win
    out3 = tmp_path / "ab3.json"
    r = pkpipe("candidates", "ab", "same", "--suite", "deterministic", "--out", str(out3), env_extra=NO_MODEL)
    assert r.returncode == 1 and json.loads(out3.read_text(encoding="utf-8"))["verdict"] == "equal"


def test_the_full_cycle_from_the_ab_report_to_the_adr_and_the_adoption(listed, tmp_path):
    vault, adr_dir = listed
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "categories.json").write_text(json.dumps([{"id": "tool", "enabled": False}]), encoding="utf-8")
    report = tmp_path / "metasea-ab.json"
    r = pkpipe(
        "candidates", "ab", "Metasea", "--overlay", str(broken), "--suite", "deterministic",
        "--out", str(report), env_extra=NO_MODEL,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    for status in ("investigating", "trial"):
        assert _cli(vault, adr_dir, "set", "metasea", "--status", status).returncode == 0
    made = _cli(vault, adr_dir, "adr", "Metasea", "--ab-report", str(report))
    assert made.returncode == 0
    adr = adr_dir / "0001-adopt-metasea.md"
    assert "| deterministic.pass_rate |" in adr.read_text(encoding="utf-8")
    done = _cli(
        vault, adr_dir, "set", "Metasea", "--status", "adopted", "--adr", adr.name, "--ab-report", str(report)
    )
    assert done.returncode == 0, done.stderr
