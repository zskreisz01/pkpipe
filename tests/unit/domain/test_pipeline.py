"""Spec 033: the pipeline overview: one bucket per item, what needs the owner, the Markdown note."""

from __future__ import annotations

from datetime import UTC, datetime

from pkpipe.domain import pipeline as pl

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
HELD_ROW = {"item_id": "a" * 12, "state": "processed"}


def row(i: str, state: str, **extra):
    return {
        "item_id": i * 12,
        "state": state,
        "kind": "link",
        "resource": f"https://example.org/{i}",
        "ai_suggested_category": "tool",
        "override": "",
        "needs_owner": False,
        **extra,
    }


def proc(i: str, status: str, **extra):
    return {i * 12: {"item_id": i * 12, "status": status, "note": "", "link": "", "nb_id": "", **extra}}


def sample():
    rows = [
        row("a", "suggested"),
        row("b", "suggested", needs_owner=True, ai_suggested_category=""),
        row("c", "awaiting_category", override="games"),
        row("d", "released"),
        row("e", "released"),
        row("f", "processed"),  # done
        row("g", "processed"),  # ready
        row("h", "processed"),  # held
        row("i", "processed"),  # failed
        row("j", "processed"),  # unprocessed
        row("k", "processed"),  # no processing row
        row("l", "quarantined"),
    ]
    processing = {
        **proc("f", "done", note="4-Resources/Tools/x-ffffffffffff.md"),
        **proc("g", "ready", link="https://notebook.google.com/notebook/n1", nb_id="n1"),
        **proc("h", "failed", link="held for your decision: only the injection detector fired"),
        **proc("i", "failed", link="the page could not be fetched"),
        **proc("j", "unprocessed", link="no source found by web search"),
    }
    return rows, processing


def test_every_item_is_in_exactly_one_bucket_and_the_funnel_adds_up():
    rows, processing = sample()
    ov = pl.build_overview(
        rows=rows,
        processing=processing,
        held=["h" * 12],
        jobs={"e" * 12: {"status": "waiting_quota", "retry_after": 1_793_000_000}},
        now=NOW,
    )
    buckets = {i: it["bucket"] for i, it in ov["items"].items()}
    assert buckets == {
        "a" * 12: "waiting_confirmation",
        "b" * 12: "waiting_confirmation",
        "c" * 12: "awaiting_category",
        "d" * 12: "released",
        "e" * 12: "waiting_quota",
        "f" * 12: "done",
        "g" * 12: "ready",
        "h" * 12: "held",
        "i" * 12: "failed",
        "j" * 12: "unprocessed",
        "k" * 12: "result_unknown",
        "l" * 12: "quarantined",
    }
    f = ov["funnel"]
    assert f["total"] == len(rows) == sum(f["buckets"].values()) == sum(s["count"] for s in f["stages"])
    assert [s["stage"] for s in f["stages"]] == ["triaged", "released", "processed", "stopped"]


def test_needs_you_is_ordered_and_carries_the_action_and_the_next_step():
    rows, processing = sample()
    ov = pl.build_overview(rows=rows, processing=processing, held=["h" * 12], retries={"i" * 12: 3}, now=NOW)
    needs = ov["needs_you"]
    assert [n["bucket"] for n in needs] == [
        "held",
        "quarantined",
        "failed",
        "unprocessed",
        "awaiting_category",
        "waiting_confirmation",
    ]
    by = {n["item_id"]: n for n in needs}
    assert by["h" * 12]["action"]["kind"] == "accept" and by["l" * 12]["action"]["kind"] == "release"
    assert by["i" * 12]["reason"] == "retry_limit" and by["i" * 12]["action"] is None  # the limit is reached
    assert by["j" * 12]["action"]["kind"] == "retry"
    assert by["c" * 12]["reason"] == "new_category_proposal"
    assert by["b" * 12]["reason"] == "needs_category" and "a" * 12 not in by  # a plain suggestion is quiet


def test_the_situation_text_of_the_unfinished_table_is_shown_not_made_up():
    rows, processing = sample()
    progress = {
        "j" * 12: {"situation": "The search found nothing.", "next_step": "Add a link.", "cause": "x"}
    }
    ov = pl.build_overview(rows=rows, processing=processing, progress=progress, now=NOW)
    j = next(n for n in ov["needs_you"] if n["item_id"] == "j" * 12)
    assert j["situation"] == "The search found nothing." and j["next_step"] == "Add a link."


def test_done_recent_has_the_result_links_and_only_the_window():
    rows, processing = sample()
    last = {"f" * 12: "2026-11-09T10:00:00Z", "g" * 12: "2026-09-01T10:00:00Z"}
    ov = pl.build_overview(
        rows=rows,
        processing=processing,
        last_seen=last,
        jobs={"g" * 12: {"status": "ready", "audio": True}},
        now=NOW,
        recent_days=14,
    )
    assert [d["item_id"] for d in ov["done_recent"]] == ["f" * 12]  # g is older than 14 days
    assert ov["done_recent"][0]["note"] == "4-Resources/Tools/x-ffffffffffff.md"
    ov = pl.build_overview(
        rows=rows,
        processing=processing,
        last_seen=last,
        jobs={"g" * 12: {"status": "ready", "audio": True}},
        now=NOW,
        recent_days=90,
    )
    g = next(d for d in ov["done_recent"] if d["item_id"] == "g" * 12)
    assert g["link"].startswith("https://notebook.google.com/") and g["podcast"] == "ready"


def test_waiting_has_the_quota_reset_time_and_the_next_runs():
    rows, processing = sample()
    ov = pl.build_overview(
        rows=rows,
        processing=processing,
        jobs={
            "e" * 12: {"status": "waiting_quota", "retry_after": 1_794_000_000},
            "x" * 12: {"status": "generating", "started_at": 1_793_000_000},
        },
        next_runs=[{"flow": "triage", "cron": "0 20 * * *", "at": "2026-11-10 19:00"}],
        now=NOW,
    )
    w = ov["waiting"]
    assert [i["item_id"] for i in w["waiting_quota"]] == ["e" * 12] and w["waiting_quota"][0]["waiting_until"]
    assert [g["item_id"] for g in w["generating"]] == ["x" * 12]
    assert [i["item_id"] for i in w["released"]] == ["d" * 12]
    assert w["next_runs"][0]["flow"] == "triage"


def test_the_last_run_per_flow_with_its_counts():
    runs = [
        {
            "run_id": "r1",
            "flow_id": "triage",
            "status": "done",
            "trigger": "schedule",
            "started_at": "2026-11-09T19:00:00Z",
        },
        {
            "run_id": "r2",
            "flow_id": "triage",
            "status": "failed",
            "trigger": "ui",
            "started_at": "2026-11-10T19:00:00Z",
        },
    ]
    ov = pl.build_overview(rows=[], runs=runs, run_counts={"r2": {"items": 4, "failed_steps": 1}}, now=NOW)
    assert ov["runs"] == [
        {
            "flow": "triage",
            "run_id": "r2",
            "status": "failed",
            "trigger": "ui",
            "started": "2026-11-10 19:00",
            "ended": "",
            "items": 4,
            "failed_steps": 1,
        }
    ]


def test_position_names_the_stage_of_one_item():
    rows, processing = sample()
    ov = pl.build_overview(rows=rows, processing=processing, now=NOW)
    p = pl.position(ov, "f" * 12)
    assert p["bucket"] == "done" and p["stage"] == "processed" and p["label"] == "done"
    assert pl.position(ov, "z" * 12) is None


# -- Markdown -----------------------------------------------------------------------------------------------
def test_the_note_has_the_sections_in_order_and_links_to_the_ui():
    rows, processing = sample()
    ov = pl.build_overview(rows=rows, processing=processing, held=["h" * 12], now=NOW, captured=3)
    text = pl.render_markdown(ov, ui_url="http://ui.example:8787/")
    heads = [line for line in text.splitlines() if line.startswith("## ")]
    assert [h.split(" (")[0] for h in heads] == [
        "## Funnel",
        "## Last runs",
        "## Needs you",
        "## In progress / waiting",
        "## Done in the last 14 days",
        "## Where things live",
    ]
    assert text.startswith(pl.HEADING)
    assert "captured, not yet triaged: **3**" in text
    assert f"(http://ui.example:8787/items/{'h' * 12})" in text
    assert f"(http://ui.example:8787/pipeline#item-{'h' * 12})" in text


def test_a_cell_cannot_break_the_table_or_run_markup():
    rows = [row("a", "processed", resource="x | y `z` <b>[l](m)</b>")]
    ov = pl.build_overview(
        rows=rows,
        processing=proc("a", "failed", link="the page could not be fetched"),
        progress={
            "a" * 12: {"situation": "bad | pipe\nand `code` and <script>", "next_step": "ok", "cause": "x"}
        },
        now=NOW,
    )
    text = pl.render_markdown(ov)
    line = next(line for line in text.splitlines() if line.startswith("| " + "a" * 12))
    assert line.count(" | ") == 4  # five cells: nothing split the row
    assert "<script>" not in text and "<b>" not in text and "\n and" not in line


def test_two_refreshes_that_differ_only_in_the_generated_line_are_the_same_note():
    rows, processing = sample()
    first = pl.render_markdown(pl.build_overview(rows=rows, processing=processing, now=NOW))
    later = pl.render_markdown(pl.build_overview(rows=rows, processing=processing, now=NOW.replace(hour=15)))
    assert first != later and pl.normalize(first) == pl.normalize(later)
    assert pl.content_hash(first) == pl.content_hash(later)
    changed = pl.render_markdown(pl.build_overview(rows=rows[:-1], processing=processing, now=NOW))
    assert pl.content_hash(changed) != pl.content_hash(first)


def test_a_long_list_is_cut_with_a_pointer_to_the_page():
    rows = [row(f"{n:x}", "quarantined") for n in range(10)]
    rows = [{**r, "item_id": f"{n:012x}"} for n, r in enumerate(rows * 6)]
    ov = pl.build_overview(rows=rows, now=NOW)
    text = pl.render_markdown(ov, ui_url="http://ui.example")
    assert f"+{len(rows) - pl.MAX_NOTE_ROWS} more." in text and len(ov["needs_you"]) == len(rows)
