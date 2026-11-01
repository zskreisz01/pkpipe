"""Spec 015, live: the extraction and weekly review flows with the real local model (LM Studio) on a
synthetic vault with synthetic journals. The model's exact wording is not asserted; the guarantees the code
gives are: verbatim statements and quotes, status never touched, no private text in the notification."""

from __future__ import annotations

from pathlib import Path

import pytest

from pkpipe import app
from pkpipe.engine.registry import load_effective
from tests.fakes import FakeClock, FakeNotify, RecordingTracer
from tests.support.flows import make_ports, make_service
from tests.support.improvement import (
    ROUTES,
    backlog,
    journal_with,
    owner_sets_status,
    write_journal,
)
from tests.support.pack_runtime import vault_tools

STRETCH = "Stretch for ten minutes every morning."


@pytest.mark.live
def test_live_extraction_and_weekly_review_with_the_local_model(
    tmp_path: Path, pack_dir, lmstudio, monkeypatch
):
    monkeypatch.setenv("PKPIPE_MODEL_BASE_URL", lmstudio["base_url"])
    monkeypatch.setenv("PKPIPE_MODEL_NAME", lmstudio["model"])
    vault = tmp_path / "vault"
    (vault / "2-Areas/Journal").mkdir(parents=True)
    write_journal(
        vault,
        "2026-10-30",
        journal_with(
            f"- {STRETCH}\n- Go to bed before 23:00 on weekdays.\n", extra="Quiet day, rain outside."
        ),
    )
    write_journal(vault, "2026-10-31", "# Journal\n\nStretched before breakfast, my back felt looser.\n")
    write_journal(vault, "2026-11-01", "# Journal\n\nSkipped stretching, too tired. Went to bed at 22:30.\n")
    snap = load_effective(pack_dir, None).snapshot()
    notify = FakeNotify(routes=ROUTES)
    ports = make_ports(
        model=app.make_model(snap),
        tools=vault_tools(vault),
        notify=notify,
        clock=FakeClock(),
        tracer=RecordingTracer(),
    )
    svc = make_service(snap, ports)

    run = svc.start("improvement_extract", trigger="schedule", inputs={"date": "2026-10-30"})
    assert run.ok, run.error
    rows = backlog(vault)
    section = (vault / "2-Areas/Journal/2026-10-30.md").read_text(encoding="utf-8")
    assert rows and all(r["status"] == "proposed" and r["statement"] in section for r in rows.values())

    for item_id in rows:
        owner_sets_status(vault, item_id, "active")
    review = svc.start("improvement_review", trigger="schedule", inputs={"date": "2026-11-01"})
    assert review.ok, review.error
    after = backlog(vault)
    journals = "".join(
        (vault / "2-Areas/Journal" / f"{d}.md").read_text() for d in ("2026-10-31", "2026-11-01")
    )
    assert {r["status"] for r in after.values()} == {"active"}
    for row in after.values():
        assert row["verdict"] in ("keep", "adjust", "drop")
        for line in filter(None, (e.strip() for e in row.get("evidence", "").split(" / "))):
            quote = line.split('"', 1)[1].rsplit('"', 1)[0]
            assert " ".join(quote.split()).lower() in " ".join(journals.split()).lower()
    assert notify.sent and all(r["statement"] not in notify.sent[-1].message for r in after.values())
