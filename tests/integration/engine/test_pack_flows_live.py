"""Spec 005 SC-001, live: the pack's triage and process_released flows with the real local model (LM Studio)
for every LLM step: triage ``suggest``, the secure-fetch Reader and the Tool category's session plan
(spec 010).

Tools are backed by a real ``Vault`` on a synthetic temp vault, the web by a fake that serves the note's page
(the real web-fetch server has its own live tests); notifier and clock are fakes. The owner's decision is
simulated by overriding the tool note to ``tool``, so the item takes the other_resource path (secure fetch ->
research, compare and synthesize sessions -> output guard -> note written) whatever the model suggested. The
fit sessions are skipped: the public pack has no context packs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pkpipe import app
from pkpipe.domain.triage import item_id_for
from pkpipe.engine.registry import load_effective
from pkpipe.obsidian import lists
from tests.fakes import FakeClock, FakeNotify, RecordingTracer
from tests.support.flows import make_ports, make_service
from tests.support.pack_runtime import PACK_ROUTES, TRIAGE_FILE, owner_decides, synthetic_web, vault_tools

TOOL = item_id_for("0-INBOX/example-tool.md")


@pytest.mark.live
def test_live_triage_then_process_released_with_the_local_model(
    synthetic_vault: Path, pack_dir: Path, lmstudio, monkeypatch
):
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", lmstudio["base_url"])
    monkeypatch.setenv("PKPIPE_MODEL_NAME", lmstudio["model"])
    snap = load_effective(pack_dir, None).snapshot()
    categories = {c["id"] for c in snap.data["categories"]}
    tracer, notify = RecordingTracer(), FakeNotify(routes=PACK_ROUTES)
    web = synthetic_web()
    ports = make_ports(
        model=app.make_model(snap),
        tools=vault_tools(synthetic_vault, web=web),
        notify=notify,
        clock=FakeClock(),
        tracer=tracer,
        resolver=web.resolve,
    )
    svc = make_service(snap, ports)

    triage = svc.start("triage", trigger="schedule")
    assert triage.ok, triage.error
    rows = {r["item_id"]: r for r in lists.read_triage(synthetic_vault / TRIAGE_FILE)["rows"]}
    assert TOOL in rows and all(r["ai_suggested_category"] in categories for r in rows.values())

    owner_decides(synthetic_vault, override={TOOL: "tool"})
    run = svc.start("process_released", trigger="schedule")
    assert run.ok, run.error
    [result] = run.outputs["process_item"]
    assert result["status"] == "done", result
    item = result["item"]
    states = {r["item_id"]: r["state"] for r in lists.read_triage(synthetic_vault / TRIAGE_FILE)["rows"]}
    if item.get("flagged"):  # the guard may flag a model output; then nothing may be written
        assert states[TOOL] == "quarantined"
        assert not list((synthetic_vault / "3-Resources/Tools").glob("*.md"))
    else:
        assert states[TOOL] == "processed"
        [path] = (synthetic_vault / "3-Resources/Tools").glob(f"*-{TOOL}.md")
        note = path.read_text(encoding="utf-8")
        assert "source: https://example.invalid/tool" in note and f"pkpipe_item: {TOOL}" in note
        body = note.split("---", 2)[-1].strip()
        assert body.startswith("# ") and "## Recommendation" in body  # a real note from the model's sessions
    steps = {s.name for s in tracer.of("step")}
    assert {
        "suggest",
        "read",
        "run_sessions.research",
        "run_sessions.compare",
        "run_sessions.synthesize",
    } <= steps
    assert "run_sessions.fit_personal" not in steps  # no context pack: skipped
    assert notify.sent and "**Processed (" in notify.sent[-1].message
