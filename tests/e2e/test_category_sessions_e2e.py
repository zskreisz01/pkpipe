"""Spec 010 end to end: a released Tool item (and Marketing, Misc and an overlay category) goes through
the full
category process through ``pkpipe run process_released`` with the real composition root and real local
processes: the obsidian-vault MCP server (synthetic temp vault), the web-fetch MCP server (Lightpanda
behind the
filtering proxy) serving local fixture pages, and a synthetic websearch MCP server. Secure fetch, the session
plans, the output guard and the vault writes all run for real.

Swapped at the composition root: the classifier (a fake; the real Qwen3Guard + DeBERTa classifier is
covered by
tests/integration/security) and, in the scripted variant, the model. The live variant calls the local model
(LM Studio) for every session; the checks that hold for any model (structure, folders, isolation, the query
guard) are the same.
"""

from __future__ import annotations

import json

import pytest

from pkpipe import app
from pkpipe.cli import main
from pkpipe.domain.triage import item_id_for
from pkpipe.obsidian import lists
from pkpipe.steps.secure_fetch import CLASSIFIER_PORT, TEST_LOOPBACK_PORT
from tests.fakes import FakeClassifier, FakeModel, FakeNotify
from tests.support.fixture_site import fixture_site
from tests.support.pack_runtime import PACK_ROUTES, PROCESSING_LIST, TRIAGE_FILE, fake_model_reply
from tests.support.vault_mcp import free_port, http_server, write_policy
from tests.support.webfetch_mcp import webfetch_server
from tests.support.websearch_mcp import queries, websearch_server

PERSONAL = "Personal projects (synthetic): HERON, a data pipeline that loads CSV exports into SQLite."
ENTERPRISE = "Programmes of ExampleCorp (synthetic): BEACON, an observability programme on OpenTelemetry."
PAPER = {
    "id": "paper",
    "label": "Papers",
    "subflow": "other_resource",
    "para_path": "3-Resources/Papers",
    "triage_hint": "A research paper.",
    "sessions": [
        {
            "id": "summarize",
            "system": "synthesizer",
            "instruction": "other/summarize.md",
            "inputs": ["item.extract"],
            "output_schema": "resource_note",
        }
    ],
}


@pytest.fixture(scope="module")
def site():
    with fixture_site() as s:
        yield s


@pytest.fixture
def world(site, tmp_path, synthetic_vault, write_json, monkeypatch):
    notes = {
        "vectorlite.md": f"---\nsource: {site.url('/clean')}\n---\nRAWMARKER-TOOL vectorlite, a tool.\n",
        "billboard.md": f"---\nsource: {site.url('/campaign')}\n---\nRAWMARKER-MKT a weather billboard.\n",
        "sourdough.md": "RAWMARKER-MISC notes on feeding a sourdough starter.\n",
    }
    for name, text in notes.items():
        (synthetic_vault / "0-INBOX" / name).write_text(text, encoding="utf-8")
    for p in ("0-INBOX/example-tool.md", "0-INBOX/example-video.md"):
        (synthetic_vault / p).unlink()
    for folder in ("3-Resources/Marketing", "3-Resources/Misc", "3-Resources/Papers"):
        (synthetic_vault / folder).mkdir(parents=True, exist_ok=True)
    search_log = tmp_path / "searches.jsonl"
    notify = FakeNotify(routes=PACK_ROUTES)
    monkeypatch.setattr(app, "make_notify", lambda snapshot: notify)
    monkeypatch.delenv("PKPIPE_OVERLAY_DIR", raising=False)
    real_ports = app.ports_for

    def ports(snapshot, *args, **kwargs):
        p = real_ports(snapshot, *args, **kwargs)
        p.extra[CLASSIFIER_PORT] = FakeClassifier()
        p.extra[TEST_LOOPBACK_PORT] = site.allowance
        return p

    monkeypatch.setattr(app, "ports_for", ports)
    with (
        http_server(write_policy(tmp_path, synthetic_vault), free_port()) as vault_url,
        webfetch_server(test_loopback=site.env_allowance) as fetch_url,
        websearch_server(search_log) as search_url,
    ):
        overlay = tmp_path / "overlay"
        write_json(
            overlay,
            "mcp_servers.json",
            {
                "obsidian": {"url": vault_url},
                "webfetch": {"url": fetch_url},
                "websearch": {"url": search_url},
            },
        )
        write_json(overlay, "categories.json", [PAPER])
        write_json(overlay, "security.json", {"query_guard": {"blocked_terms": ["Project Heron"]}})
        (overlay / "contexts").mkdir()
        (overlay / "contexts/personal.md").write_text(PERSONAL, encoding="utf-8")
        (overlay / "contexts/enterprise.md").write_text(ENTERPRISE, encoding="utf-8")
        common = ("--overlay", str(overlay), "--db", str(tmp_path / "app.db"), "--json")

        yield synthetic_vault, notify, search_log, common


IDS = {n: item_id_for(f"0-INBOX/{n}") for n in ("vectorlite.md", "billboard.md", "trap.md", "sourdough.md")}
CLEAN_ITEMS = ("vectorlite.md", "billboard.md", "sourdough.md")
CATEGORY = {
    "vectorlite.md": "tool",
    "billboard.md": "marketing",
    "trap.md": "tool",
    "sourdough.md": "misc",
}


def _run(capsys, common, *argv):
    code = main(list(argv) + list(common))
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _owner_overrides(vault, mapping):
    from tests.support.pack_runtime import owner_decides

    owner_decides(vault, override={IDS[n]: c for n, c in mapping.items()})


def _release(capsys, common, vault, site, names, *, with_trap=False):
    """One evening and morning: triage the new notes, the owner routes them by category, process_released."""
    if (
        with_trap
    ):  # a page with a hidden injection; its host is deny-listed once it is quarantined, so it runs
        # after the clean items (they share the fixture site's host)
        (vault / "0-INBOX/trap.md").write_text(
            f"---\nsource: {site.url('/hidden')}\n---\nRAWMARKER-TOOL kestrel, a task runner.\n",
            encoding="utf-8",
        )
    code, triage = _run(capsys, common, "run", "triage", "--trigger", "schedule")
    assert code == 0 and triage["status"] == "done", triage
    _owner_overrides(vault, {n: CATEGORY[n] for n in names})
    code, process = _run(capsys, common, "run", "process_released", "--trigger", "schedule")
    assert code == 0, process
    return process


def _states(vault):
    return {r["item_id"]: r["state"] for r in lists.read_triage(vault / TRIAGE_FILE)["rows"]}


def _check_notes(vault, search_log, names):
    for name, folder in (
        ("vectorlite.md", "3-Resources/Tools"),
        ("billboard.md", "3-Resources/Marketing"),
        ("sourdough.md", "3-Resources/Misc"),
    ):
        if name not in names:
            continue
        item_id = IDS[name]
        [note] = (vault / folder).glob(f"*-{item_id}.md")
        text = note.read_text(encoding="utf-8")
        assert f"pkpipe_item: {item_id}" in text and text.startswith("---\n") and "\n# " in text, name
        assert (vault / "4-Archive/Inbox" / f"{item_id}-{name}").is_file(), (
            name
        )  # the INBOX note was archived
        assert not (vault / "0-INBOX" / name).exists(), name
    plist = {r.item_id: r for r in lists.read_processing_list(vault / PROCESSING_LIST)}
    assert {i for i, r in plist.items() if r.status.value == "done"} >= {IDS[n] for n in names}
    # constitution IV: no private name left the machine in a web query (and searches happened at all)
    sent = queries(search_log)
    assert sent and not any("heron" in q.lower() or "beacon" in q.lower() for q in sent), sent


def _check_trap(vault, notify):
    """The hidden injection page is stopped by secure fetch: quarantined, no note, no session ran for it."""
    assert _states(vault)[IDS["trap.md"]] == "quarantined"
    assert not (vault / "0-INBOX/trap.md").exists()
    assert (vault / "0-INBOX/_quarantine" / f"{IDS['trap.md']}.md").is_file()
    assert not list((vault / "3-Resources/Tools").glob(f"*-{IDS['trap.md']}.md"))
    assert any(f"Security flag on item {IDS['trap.md']}" in m.message for m in notify.sent)


@pytest.mark.live("lightpanda")
def test_released_items_go_through_the_full_category_process(world, site, capsys, monkeypatch):
    """US1, US2, SC-002 with a scripted model: Tool (five sessions, both context packs from the overlay,
    secure
    fetch of a JavaScript page through Lightpanda), Marketing and Misc; then a page with a hidden
    injection."""
    vault, notify, search_log, common = world
    model = FakeModel(fake_model_reply({"RAWMARKER": "misc"}))
    monkeypatch.setattr(app, "make_model", lambda snapshot: model)
    process = _release(capsys, common, vault, site, CLEAN_ITEMS)
    states = _states(vault)
    assert {states[IDS[n]] for n in CLEAN_ITEMS} == {"processed"}, states
    _check_notes(vault, search_log, CLEAN_ITEMS)
    tool_note = next((vault / "3-Resources/Tools").glob(f"*-{IDS['vectorlite.md']}.md")).read_text("utf-8")
    assert "source: http://127.0.0.1:" in tool_note and "## Recommendation" in tool_note
    assert (
        "## Alternatives" in tool_note and "## Fit personal" in tool_note and "## Fit enterprise" in tool_note
    )
    summary = [m for m in notify.sent if m.event == "daily_summary"][-1]
    assert "**Processed (3):**" in summary.message and "**Todo:** nothing open" in summary.message
    assert process["status"] == "done"
    # isolation (constitution IV and VIII)
    page_seen = [c for c in model.calls if "Rendered by JavaScript" in c["user"]]
    assert page_seen and all("<script" not in c["user"] for c in page_seen)  # only the Reader saw page text
    tool_calls = [c for c in model.calls if "### item.extract" in c["user"] and "vectorlite" in c["user"]]
    assert tool_calls and all("RAWMARKER" not in c["user"] for c in tool_calls)  # no raw note text
    assert all("<html" not in c["user"].lower() for c in tool_calls)  # no HTML
    plan = [
        c for c in model.calls if c["json_schema"] and "maintainer" in c["json_schema"].get("properties", {})
    ]
    assert len(plan) == 1
    enterprise = [c for c in model.calls if "BEACON" in c["user"]]
    assert len(enterprise) == 1 and "adoption_cost" in enterprise[0]["json_schema"]["properties"]
    # the hidden-injection page
    calls_before = len(model.calls)
    process = _release(capsys, common, vault, site, ["trap.md"], with_trap=True)
    _check_trap(vault, notify)
    assert process["status"] == "done"
    sessions = [
        c
        for c in model.calls[calls_before:]
        if c["json_schema"] and "maintainer" in c["json_schema"]["properties"]
    ]
    assert sessions == []  # no session ran for the flagged page
    summary = [m for m in notify.sent if m.event == "daily_summary"][-1]
    assert "**Processed (0):**" in summary.message and "quarantined 1 · failed 0" in summary.message


@pytest.mark.live("lmstudio", "lightpanda")
def test_released_items_go_through_the_full_category_process_with_the_live_model(world, site, capsys):
    """The same process with the real local model answering every session (and the triage, which the
    owner then
    overrides so the categories are fixed): the structure holds whatever the model writes."""
    vault, notify, search_log, common = world
    _release(capsys, common, vault, site, CLEAN_ITEMS)
    states = _states(vault)
    assert states[IDS["vectorlite.md"]] == "processed", states
    done = [n for n in CLEAN_ITEMS if states[IDS[n]] == "processed"]
    _check_notes(vault, search_log, done)
    tool_note = next((vault / "3-Resources/Tools").glob(f"*-{IDS['vectorlite.md']}.md")).read_text("utf-8")
    assert "vectorlite" in tool_note.lower() and "## Recommendation" in tool_note
    _release(capsys, common, vault, site, ["trap.md"], with_trap=True)
    _check_trap(vault, notify)  # secure fetch does not depend on the model's behaviour
