"""Eval request concurrency (chore test-speed, spec 013 FR-004 reports): ``evals.concurrency`` /
``pkpipe eval --concurrency N`` runs N cases at once. Results, their order, the metrics and the cassettes must
be the same for every N; only the wall time changes."""

from __future__ import annotations

import contextvars
import random
import re
import threading
import time

import pytest
from pydantic import ValidationError

from pkpipe.engine.config_models import EvalsConfig
from pkpipe.engine.registry import load_effective
from pkpipe.evals.cassette import CassetteModel
from pkpipe.evals.cli import add_arguments
from pkpipe.evals.gate import PublishGate
from pkpipe.evals.runner import EvalRunner
from pkpipe.evals.suites import parallel_map
from tests.fakes import FakeModel
from tests.support.evals import IDS, ProfileModel, mini_evals, triage_case


class InFlight:
    """Counts calls in flight; ``peak`` is the most that ran at the same time."""

    def __init__(self):
        self.lock = threading.Lock()
        self.now = self.peak = 0

    def __enter__(self):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)

    def __exit__(self, *exc):
        with self.lock:
            self.now -= 1


def test_parallel_map_keeps_input_order_and_bounds_the_calls_in_flight():
    """Later items finish first, yet results come back in input order; never more than N run at once."""
    flight = InFlight()

    def slow(i: int) -> int:
        with flight:
            time.sleep(0.002 * (20 - i))  # reversed completion order
        return i * i

    assert parallel_map(list(range(20)), slow, 4) == [i * i for i in range(20)]
    assert flight.peak == 4
    flight.peak = 0
    assert parallel_map(list(range(5)), slow, 1) == [i * i for i in range(5)] and flight.peak == 1


def test_parallel_map_raises_the_first_error_in_input_order_and_copies_the_context():
    var = contextvars.ContextVar("var", default="unset")
    var.set("caller")

    def fn(i: int) -> str:
        time.sleep(0.001 * (10 - i))
        if i in (3, 7):
            raise ValueError(f"item {i}")
        return var.get()

    with pytest.raises(ValueError, match="item 3"):
        parallel_map(list(range(10)), fn, 4)
    assert parallel_map([0, 1, 2], fn, 3) == ["caller"] * 3  # spans nest under the caller's span


def _reply(system, user):
    time.sleep(random.uniform(0, 0.01))  # completion order differs from the case order
    cat = "tool" if "zz-cli" in user else "marketing"
    return {"category": cat, "confidence": 0.9, "reason": "r"}


ROWS = [
    triage_case(f"c{i:02}", "zz-cli" if i % 3 else "zz-ad", "tool" if i % 3 else "marketing")
    for i in range(24)
]


@pytest.fixture
def snap(pack_dir):
    return load_effective(pack_dir, None).snapshot()


def test_results_metrics_and_case_order_do_not_depend_on_concurrency(tmp_path, snap):
    """Deterministic reports: concurrency 1 and 8 give the same cases, in dataset order, and the same
    metrics."""
    paths = mini_evals(tmp_path, {"datasets/triage.jsonl": ROWS})
    reports = {
        n: EvalRunner(snap, paths=paths, model=FakeModel(_reply), identities=IDS, concurrency=n)
        .run(["triage"])
        .suite("triage")
        for n in (1, 8)
    }
    assert [c.case_id for c in reports[8].cases] == [r["id"] for r in ROWS]
    assert [c.model_dump() for c in reports[8].cases] == [c.model_dump() for c in reports[1].cases]
    assert reports[8].metrics == reports[1].metrics and reports[8].metrics["accuracy"] == 1.0


def test_concurrency_comes_from_evals_config_and_the_argument_wins(pack_dir, overlay_dir, write_json):
    """Default 1 (production runs); ``evals.concurrency`` sets it; ``--concurrency`` overrides it; < 1 is
    refused."""
    assert EvalRunner(load_effective(pack_dir, None).snapshot()).concurrency == 1
    write_json(overlay_dir, "evals.json", {"concurrency": 3})
    snap = load_effective(pack_dir, overlay_dir).snapshot()
    assert EvalRunner(snap).concurrency == 3
    assert EvalRunner(snap, concurrency=5).concurrency == 5
    assert PublishGate().runner(snap).concurrency == 3
    with pytest.raises(ValueError, match="concurrency must be >= 1"):
        EvalRunner(snap, concurrency=0)
    for bad in (0, 17):
        with pytest.raises(ValidationError):
            EvalsConfig.model_validate({"concurrency": bad})


def test_cli_concurrency_option_and_its_workers_alias():
    import argparse

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    assert parser.parse_args([]).concurrency is None  # -> evals.concurrency
    assert parser.parse_args(["--concurrency", "4"]).concurrency == 4
    assert parser.parse_args(["--workers", "2"]).concurrency == 2  # the older spelling still works
    with pytest.raises(SystemExit):
        parser.parse_args(["--concurrency", "0"])


def _requests(n: int) -> list[tuple[str, str]]:
    return [(f"system {i % 3}", f"user {i}") for i in range(n)]


def _answer(system, user):
    time.sleep(random.uniform(0, 0.005))
    return {"echo": user}


def test_cassette_records_and_replays_safely_from_many_threads(tmp_path):
    """Thread-safe cassettes: 8 threads recording 40 requests store exactly the serial recording (same bytes,
    sorted), and 8 threads replaying it get every answer back, with exact hit counters."""
    serial = CassetteModel(tmp_path / "serial.json", "record", FakeModel(_answer))
    for system, user in _requests(40):
        serial.complete(system, user)
    serial.save()

    parallel = CassetteModel(tmp_path / "parallel.json", "record", FakeModel(_answer))
    replies = parallel_map(_requests(40), lambda r: parallel.complete(*r), 8)
    assert replies == [{"echo": u} for _, u in _requests(40)]
    assert parallel.recorded == 40 and len(parallel.used) == 40
    parallel.save()
    assert (tmp_path / "parallel.json").read_bytes() == (tmp_path / "serial.json").read_bytes()

    replay = CassetteModel(tmp_path / "parallel.json", "replay")
    replayed = parallel_map(_requests(40) * 3, lambda r: replay.complete(*r), 8)
    assert replayed == [{"echo": u} for _, u in _requests(40)] * 3
    assert replay.hits == 120 and replay.misses == 0


def test_recording_through_the_runner_is_the_same_for_every_concurrency(tmp_path, snap):
    """``--cassette record --concurrency 4`` writes the same cassette as a serial recording."""
    files = {}
    for n in (1, 4):
        paths = mini_evals(tmp_path / f"c{n}", {"datasets/triage.jsonl": ROWS})
        run = EvalRunner(
            snap, paths=paths, model=FakeModel(_reply), mode="record", identities=IDS, concurrency=n
        ).run(["triage"])
        assert run.ok
        files[n] = paths.cassette("triage").read_bytes()
    assert files[1] == files[4]


def test_pairwise_comparison_runs_cases_concurrently_in_dataset_order(pack_dir, tmp_path, write_json):
    """FR-003 with concurrency: the A/B outcomes stay in dataset order and each matches its own case."""
    cases = [
        {
            "id": f"s{i}",
            "input": {"extract": f"case-{i} is a key-value store."},
            "expected": {"must_include": ["case"]},
        }
        for i in range(8)
    ]
    paths = mini_evals(tmp_path / "evals", {"datasets/sessions/marketing/synthesize.jsonl": cases})
    overlay = tmp_path / "ov"
    (overlay / "instructions/marketing").mkdir(parents=True)
    (overlay / "instructions/marketing/synthesize.md").write_text("Write a crisp note.", encoding="utf-8")
    write_json(overlay, "evals.json", {"concurrency": 4})
    current, draft = load_effective(pack_dir, None).snapshot(), load_effective(pack_dir, overlay).snapshot()
    flight = InFlight()

    def subject(system, user):
        with flight:
            time.sleep(random.uniform(0.005, 0.02))
        version = "NEW" if "crisp note" in system + user else "OLD"
        case = re.search(r"case-(\d+)", user).group(1)
        return {"title": f"case-{case}", "note": f"{version} note on case-{case}"}

    def judge(system, user):
        if "Which output is better" not in user:
            return {"reasoning": "", "score": 5}
        case = int(re.search(r"case-(\d+)", user).group(1))
        first = user.split("### Output B", 1)[0]  # the text of output A
        a_is_new = "NEW note" in first
        prefer_new = case % 2 == 0  # even cases: the new instruction wins; odd: the old one
        return {"reasoning": "", "winner": "A" if a_is_new == prefer_new else "B"}

    model = ProfileModel(default=FakeModel(subject), judge=FakeModel(judge))
    decision = PublishGate(model=model, paths=paths, identities=IDS).check(current, draft)
    [pw] = decision.pairwise
    assert [o["case_id"] for o in pw["outcomes"]] == [c["id"] for c in cases]
    assert [o["outcome"] for o in pw["outcomes"]] == ["candidate", "baseline"] * 4
    assert pw["score"] == 0.5 and flight.peak > 1
