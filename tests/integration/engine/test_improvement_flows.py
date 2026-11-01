"""Spec 015: the self-improvement flows run end-to-end with fake ports on a synthetic vault.

Tools are backed by a real ``pkpipe.obsidian.Vault`` (real files, the vault's own owner-field rules); the
model, notifier and clock are fakes. Journals are synthetic."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from pkpipe.engine.registry import load_effective
from pkpipe.ports import ModelError
from tests.fakes import FakeClock, FakeModel, FakeNotify, RecordingTracer
from tests.support.flows import make_ports, make_service
from tests.support.improvement import (
    BACKLOG,
    ROUTES,
    ImprovementModel,
    backlog,
    journal_with,
    owner_sets_status,
    write_journal,
)
from tests.support.pack_runtime import vault_tools

DAY = "2026-10-30"
RUN_DAY = date(2026, 11, 1)  # a Sunday
STRETCH = "Stretch for ten minutes every morning."
SLEEP = "Go to bed before 23:00 on weekdays."
ZEN = "Try the zazen timer app for a week."


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    for folder in ("0-INBOX", "2-Areas/Journal", "2-Areas/Self-Improvement"):
        (root / folder).mkdir(parents=True)
    return root


def _runtime(vault, pack_dir, model: ImprovementModel, **ports):
    snap = load_effective(pack_dir, None).snapshot()
    fake = FakeModel(model, schema_check=True)
    notify, tracer = FakeNotify(routes=ROUTES), RecordingTracer()
    clock = FakeClock(datetime(RUN_DAY.year, RUN_DAY.month, RUN_DAY.day, 19, 30).timestamp())
    ports = make_ports(tools=vault_tools(vault), model=fake, notify=notify, tracer=tracer, clock=clock)
    return make_service(snap, ports), fake, notify, tracer


def _extract_all(user: str):
    """A well-behaved extraction session: the bullets of the section, classified."""
    items = []
    for statement, area, kind in ((STRETCH, "personal", "habit"), (SLEEP, "personal", "habit")):
        if statement in user:
            items.append({"statement": statement, "area": area, "type": kind, "success_criterion": ""})
    return {"items": items}


# -- US1: extraction -----------------------------------------------------------------------------------------
def test_extraction_adds_proposed_items_from_the_improvement_section(vault, pack_dir):
    """US1.1, FR-001: the journal's improvement bullets become backlog rows with status proposed."""
    write_journal(vault, DAY, journal_with(f"- {STRETCH}\n- {SLEEP}\n"))
    model = ImprovementModel(extract=_extract_all)
    svc, fake, notify, tracer = _runtime(vault, pack_dir, model)

    run = svc.start("improvement_extract", trigger="schedule", inputs={"date": DAY})

    assert run.ok, run.error
    rows = backlog(vault)
    assert {r["statement"] for r in rows.values()} == {STRETCH, SLEEP}
    assert {r["status"] for r in rows.values()} == {"proposed"}
    assert {r["source_note"] for r in rows.values()} == {f"2-Areas/Journal/{DAY}.md"}
    assert {r["date"] for r in rows.values()} == {DAY}
    assert notify.sent == []


def test_extraction_session_sees_only_the_section_and_has_no_tools(vault, pack_dir):
    """US1.2: no tool, no web call; only the improvement section (not the rest of the private journal)
    reaches the session, and the flow is marked as journal data (trace content capture forced off)."""
    write_journal(vault, DAY, journal_with(f"- {STRETCH}\n", extra="SECRET-DIARY-LINE about my day."))
    model = ImprovementModel(extract=_extract_all)
    svc, fake, *_ = _runtime(vault, pack_dir, model)

    assert svc.start("improvement_extract", inputs={"date": DAY}).ok

    (call,) = fake.calls
    assert STRETCH in call["user"] and "SECRET-DIARY-LINE" not in call["user"]
    assert "Synthetic gratitude" not in call["user"]
    snap = load_effective(pack_dir, None).snapshot()
    for flow_id in ("improvement_extract", "improvement_review", "review_improvement"):
        flow = snap.flow(flow_id)
        assert "journal" in flow.data_classes
        assert not [n for n in flow.nodes if n.type in ("llm_tools",)]
        for node in flow.nodes:
            if node.type == "llm":
                assert not any(i.startswith(("read", "journals")) for i in node.inputs)


def test_invented_and_paraphrased_statements_never_reach_the_backlog(vault, pack_dir):
    """FR-001, SC-001: a statement that is not verbatim in the journal is dropped by code."""
    write_journal(vault, DAY, journal_with(f"- {STRETCH}\n"))

    def liar(user):
        return {
            "items": [
                {"statement": STRETCH, "area": "personal", "type": "habit", "success_criterion": "daily"},
                {
                    "statement": "Learn Rust this quarter.",
                    "area": "learning",
                    "type": "experiment",
                    "success_criterion": "",
                },
                {
                    "statement": "Stretch ten min each morning",
                    "area": "personal",
                    "type": "habit",
                    "success_criterion": "",
                },
            ]
        }

    svc, *_ = _runtime(vault, pack_dir, ImprovementModel(extract=liar))
    run = svc.start("improvement_extract", inputs={"date": DAY})

    assert run.ok
    assert [r["statement"] for r in backlog(vault).values()] == [STRETCH]
    assert run.outputs["rows"]["dropped"] == {"not_in_journal": 2}
    only = next(iter(backlog(vault).values()))
    assert only["success_criterion"] == ""  # "daily" is not in the journal: not kept


def test_rerunning_extraction_does_not_duplicate_and_keeps_owner_edits(vault, pack_dir):
    write_journal(vault, DAY, journal_with(f"- {STRETCH}\n"))
    svc, *_ = _runtime(vault, pack_dir, ImprovementModel(extract=_extract_all))
    assert svc.start("improvement_extract", inputs={"date": DAY}).ok
    (item_id,) = backlog(vault)
    owner_sets_status(vault, item_id, "active")

    second = svc.start("improvement_extract", inputs={"date": DAY})

    assert second.ok and second.outputs["rows"]["dropped"] == {"duplicate": 1}
    assert [r["status"] for r in backlog(vault).values()] == ["active"]  # never reset to proposed


@pytest.mark.parametrize(
    "journal",
    [None, "# Journal\n\nNo improvement section today.\n", "# Journal\n\n## Improvement\n\n\n## Next\n"],
    ids=["no-journal", "no-section", "empty-section"],
)
def test_nothing_to_extract_means_no_model_call_and_no_backlog(vault, pack_dir, journal):
    """US1 edge: a missing or empty journal ends the run quietly; no session sees anything."""
    if journal is not None:
        write_journal(vault, DAY, journal)
    svc, fake, *_ = _runtime(vault, pack_dir, ImprovementModel())

    run = svc.start("improvement_extract", inputs={"date": DAY})

    assert run.ok, run.error
    assert fake.calls == []
    assert not (vault / BACKLOG).exists()


def test_extraction_defaults_to_the_clock_day(vault, pack_dir):
    write_journal(vault, RUN_DAY.isoformat(), journal_with(f"- {STRETCH}\n"))
    svc, *_ = _runtime(vault, pack_dir, ImprovementModel(extract=_extract_all))
    assert svc.start("improvement_extract", trigger="schedule").ok
    assert [r["date"] for r in backlog(vault).values()] == [RUN_DAY.isoformat()]


# -- US2: weekly review --------------------------------------------------------------------------------------
def _seed_backlog(vault, pack_dir, statements, day=DAY):
    write_journal(vault, day, journal_with("\n".join(f"- {s}" for s in statements)))

    def extract(user):
        return {
            "items": [
                {
                    "statement": s,
                    "area": "system" if s == ZEN else "personal",
                    "type": "system-change" if s == ZEN else "habit",
                    "success_criterion": "",
                }
                for s in statements
                if s in user
            ]
        }

    svc, *_ = _runtime(vault, pack_dir, ImprovementModel(extract=extract))
    assert svc.start("improvement_extract", inputs={"date": day}).ok
    return {r["statement"]: i for i, r in backlog(vault).items()}


def test_weekly_review_proposes_evidence_and_verdict_and_notifies(vault, pack_dir):
    """US2.1: active items get evidence and a verdict proposal; the owner is told; the status stays."""
    ids = _seed_backlog(vault, pack_dir, [STRETCH, SLEEP])
    owner_sets_status(vault, ids[STRETCH], "active")  # SLEEP stays proposed: not reviewed yet
    write_journal(vault, "2026-10-31", "# Journal\n\nI stretched on Friday and Saturday, it felt great.\n")
    write_journal(vault, "2026-11-01", "# Journal\n\nSkipped stretching today, too tired.\n")
    evidence = lambda user: {  # noqa: E731
        "evidence": [
            {"quote": "I stretched on Friday and Saturday, it felt great.", "signal": "supports"},
            {"quote": "Skipped stretching today, too tired.", "signal": "contradicts"},
            {"quote": "I stretched for an hour and cured my back.", "signal": "supports"},  # invented
        ]
    }
    verdict = lambda user: {  # noqa: E731
        "verdict": "adjust",
        "reason": "Works on rested days.",
        "next_experiment": "Stretch after dinner.",
    }
    model = ImprovementModel(evidence=evidence, verdict=verdict)
    svc, fake, notify, tracer = _runtime(vault, pack_dir, model)

    run = svc.start("improvement_review", trigger="schedule")

    assert run.ok, run.error
    row = backlog(vault)[ids[STRETCH]]
    assert row["status"] == "active"  # FR-001: the pipeline never changes the status
    assert row["verdict"] == "adjust" and row["next_experiment"] == "Stretch after dinner."
    assert "2026-10-31 (+)" in row["evidence"] and "2026-11-01 (-)" in row["evidence"]
    assert "cured my back" not in row["evidence"]  # unverifiable quote dropped by code
    assert not backlog(vault)[ids[SLEEP]].get("verdict")  # proposed items are not reviewed
    assert model.kinds == ["evidence", "verdict"]
    (sent,) = notify.sent
    assert (
        sent.event == "weekly_review" and "1 item(s)" in sent.message and "0 keep, 1 adjust" in sent.message
    )
    assert STRETCH not in sent.message  # private statements never leave in a notification
    assert "Improvement-Backlog.md" in sent.message


def test_no_evidence_means_keep_without_a_verdict_session(vault, pack_dir):
    """Code decides: no verified evidence is no reason to change or drop an item."""
    ids = _seed_backlog(vault, pack_dir, [STRETCH])
    owner_sets_status(vault, ids[STRETCH], "active")
    write_journal(vault, "2026-11-01", "# Journal\n\nA quiet day, nothing to report.\n")
    model = ImprovementModel(evidence=lambda user: {"evidence": [{"quote": "made up", "signal": "supports"}]})
    svc, *_ = _runtime(vault, pack_dir, model)

    run = svc.start("improvement_review")

    assert run.ok and model.kinds == ["evidence"]
    row = backlog(vault)[ids[STRETCH]]
    assert row["verdict"] == "keep" and row["evidence"] == "" and row["status"] == "active"


def test_review_skipped_or_empty_leaves_the_backlog_untouched(vault, pack_dir):
    """Edge case: items unchanged. Nothing active: no session, no write, no notification."""
    ids = _seed_backlog(vault, pack_dir, [STRETCH])  # proposed only
    before = (vault / BACKLOG).read_bytes()
    model = ImprovementModel()
    svc, fake, notify, _ = _runtime(vault, pack_dir, model)

    run = svc.start("improvement_review")

    assert run.ok and fake.calls == [] and notify.sent == []
    assert (vault / BACKLOG).read_bytes() == before
    assert "write_review" not in run.outputs
    assert ids


def test_a_failed_item_review_leaves_that_item_unchanged_and_others_proceed(vault, pack_dir):
    ids = _seed_backlog(vault, pack_dir, [STRETCH, SLEEP])
    for i in ids.values():
        owner_sets_status(vault, i, "active")
    write_journal(vault, "2026-11-01", "# Journal\n\nI stretched today. Also went to bed at 22:30.\n")

    def evidence(user):
        if SLEEP in user.split("### item.statement", 1)[1].split("### item.success_criterion")[0]:
            raise ModelError("model server fell over")
        return {"evidence": [{"quote": "I stretched today.", "signal": "supports"}]}

    verdict = lambda user: {"verdict": "keep", "reason": "Going well.", "next_experiment": "Continue."}  # noqa: E731
    svc, _, notify, _ = _runtime(vault, pack_dir, ImprovementModel(evidence=evidence, verdict=verdict))

    run = svc.start("improvement_review")

    assert run.ok, run.error
    rows = backlog(vault)
    assert rows[ids[STRETCH]]["verdict"] == "keep"
    assert not rows[ids[SLEEP]].get("verdict")
    assert "1 item(s)" in notify.sent[0].message


def test_kept_system_item_gets_a_change_proposal_for_the_eval_gate(vault, pack_dir):
    """US2.2: a system-area item the owner kept gets a proposal note that names the eval gate; the backlog
    row links it; a second review does not write it again."""
    ids = _seed_backlog(vault, pack_dir, [ZEN])
    owner_sets_status(vault, ids[ZEN], "kept")
    svc, fake, notify, _ = _runtime(vault, pack_dir, ImprovementModel())

    run = svc.start("improvement_review")

    assert run.ok, run.error
    path = f"2-Areas/Self-Improvement/System-Changes/{ids[ZEN]}.md"
    note = (vault / path).read_text(encoding="utf-8")
    assert ZEN in note and "eval gate" in note and "awaiting-eval-gate" in note
    row = backlog(vault)[ids[ZEN]]
    assert row["proposal"] == path and row["status"] == "kept"
    assert "1 system change proposal(s) waiting for the eval gate" in notify.sent[0].message
    assert fake.calls == []  # no active item: no session

    again = svc.start("improvement_review")
    assert again.ok and len(notify.sent) == 1  # nothing new: quiet


def test_personal_kept_items_get_no_system_proposal(vault, pack_dir):
    ids = _seed_backlog(vault, pack_dir, [STRETCH])
    owner_sets_status(vault, ids[STRETCH], "kept")
    svc, _, notify, _ = _runtime(vault, pack_dir, ImprovementModel())
    assert svc.start("improvement_review").ok
    assert not (vault / "2-Areas/Self-Improvement/System-Changes").exists() and notify.sent == []


def test_review_sessions_get_no_tools_and_only_declared_inputs(vault, pack_dir):
    ids = _seed_backlog(vault, pack_dir, [STRETCH])
    owner_sets_status(vault, ids[STRETCH], "active")
    write_journal(vault, "2026-11-01", "# Journal\n\nI stretched today.\n")
    evidence = lambda user: {"evidence": [{"quote": "I stretched today.", "signal": "supports"}]}  # noqa: E731
    verdict = lambda user: {"verdict": "keep", "reason": "ok", "next_experiment": "go on"}  # noqa: E731
    svc, fake, *_ = _runtime(vault, pack_dir, ImprovementModel(evidence=evidence, verdict=verdict))
    assert svc.start("improvement_review").ok
    evidence_call, verdict_call = fake.calls
    assert "journal_days" not in evidence_call["user"] and "I stretched today." in evidence_call["user"]
    assert "## 2026-11-01" in evidence_call["user"]
    assert "### item.journal" not in verdict_call["user"]  # the verdict session sees evidence only
    assert "## 2026-11-01\n" not in verdict_call["user"]
    assert "I stretched today." in verdict_call["user"]


def test_journal_text_never_reaches_a_trace_even_when_the_flow_is_opted_in(vault, pack_dir, otel_factory):
    """US1.2 / FR-002: with real OpenTelemetry and capture opted in for the flow, the journal flow still
    captures no prompt or response content (journal data class forces it off); step spans carry the usual
    attributes."""
    from pkpipe.adapters.tracer_otel import OtelTracer
    from pkpipe.telemetry import ContentPolicy

    otel = otel_factory(ContentPolicy(frozenset({"improvement_extract", "improvement_review"})))
    write_journal(vault, DAY, journal_with(f"- {STRETCH}\n", extra="SECRET-DIARY-LINE"))
    snap = load_effective(pack_dir, None).snapshot()
    ports = make_ports(
        tools=vault_tools(vault),
        model=FakeModel(ImprovementModel(extract=_extract_all), schema_check=True),
        tracer=OtelTracer(),
    )
    assert make_service(snap, ports).start("improvement_extract", inputs={"date": DAY}).ok

    spans = otel.spans()
    assert any(s.name == "llm default" for s in spans)
    assert not any(k.startswith("pkpipe.content.") for s in spans for k in s.attributes)
    assert not any(
        STRETCH in str(v) or "SECRET-DIARY-LINE" in str(v) for s in spans for v in s.attributes.values()
    )
    step = next(s for s in spans if s.name == "step extract")
    assert step.attributes["flow.id"] == "improvement_extract"
    assert step.attributes["bpmn.element"] == "Extract improvement items (local session, no tools)"


def test_the_day_an_item_was_written_is_not_evidence_and_no_later_journal_means_no_session(vault, pack_dir):
    """The journal of the item's own day holds only the statement; with no later entries nothing is sent to a
    model and the verdict is keep."""
    ids = _seed_backlog(vault, pack_dir, [STRETCH])
    owner_sets_status(vault, ids[STRETCH], "active")
    model = ImprovementModel()
    svc, fake, notify, _ = _runtime(vault, pack_dir, model)

    run = svc.start("improvement_review")

    assert run.ok and fake.calls == []
    assert backlog(vault)[ids[STRETCH]]["verdict"] == "keep"
    assert "1 keep" in notify.sent[0].message
