"""Spec 021 T007a (FR-014, SC-004): the time guard of the image batch as a pure function with a fake clock."""

from __future__ import annotations

import random

import pytest

from pkpipe.domain import image_batch as ib


def new_state(n=5, *, budget=600.0, seed=60.0, started=1000.0, uncached=None):
    queue = [f"img{i}" for i in range(n)]
    return ib.start_batch(
        queue,
        started=started,
        batch_started=started,
        budget_s=budget,
        seed_s=seed,
        to_describe=n if uncached is None else uncached,
    )


# -- the estimate ---------------------------------------------------------------------------------------------


def test_the_seed_is_the_median_of_the_recent_runs_else_the_default():
    assert ib.seed_estimate([10, 50, 30], default=60) == 30
    assert ib.seed_estimate([10, 20, 30, 50], default=60) == 25
    assert ib.seed_estimate([], default=60) == 60
    assert ib.seed_estimate([0, -3], default=60) == 60  # garbage rows never give a zero estimate


def test_the_estimate_is_the_seed_until_an_image_is_done_then_the_running_mean():
    s = new_state(seed=60)
    assert ib.running_estimate(s) == 60
    s, _ = ib.next_image(s, now=1000)
    s = ib.record_image(s, now=1030, status="described", used_model=True)
    assert ib.running_estimate(s) == 30
    s, _ = ib.next_image(s, now=1030)
    s = ib.record_image(s, now=1130, status="described", used_model=True)
    assert ib.running_estimate(s) == 65  # (30 + 100) / 2


def test_a_cached_image_is_free_and_does_not_move_the_estimate():
    s = new_state(seed=60)
    s, _ = ib.next_image(s, now=1000)
    s = ib.record_image(s, now=1000.2, status="cached", used_model=False)
    assert ib.running_estimate(s) == 60 and s["durations"] == []


# -- the stop rule -------------------------------------------------------------------------------------------


def test_it_stops_before_an_image_when_elapsed_plus_estimate_reaches_the_budget():
    s = new_state(n=10, budget=200, seed=60, started=0)
    # elapsed 0 + 60 < 200: go; after 60 s of work elapsed 60 + 60 < 200: go; 120 + 60 < 200: go; 180 + 60 >= 200: stop
    now = 0.0
    started = 0
    while True:
        s, decision = ib.next_image(s, now=now)
        if decision != ib.CONTINUE:
            break
        started += 1
        now += 60
        s = ib.record_image(s, now=now, status="described", used_model=True)
    assert started == 3 and decision == ib.STOP
    assert s["deferred"] == [f"img{i}" for i in range(3, 10)] and s["stopped_by_budget"] is True


def test_reaching_the_budget_exactly_stops():
    s = new_state(n=2, budget=120, seed=60, started=0)
    s, d = ib.next_image(s, now=60)  # 60 + 60 == 120 -> stop (reaches the budget)
    assert d == ib.STOP and s["deferred"] == ["img0", "img1"]


def test_the_text_phase_counts_against_the_budget_through_the_cycle_start():
    s = ib.start_batch(["a", "b"], started=0, batch_started=5000, budget_s=7200, seed_s=60, to_describe=2)
    s, d = ib.next_image(s, now=7150)  # the text phase took almost the whole cycle: 7150 + 60 >= 7200
    assert d == ib.STOP and s["deferred"] == ["a", "b"]


def test_an_empty_queue_is_done_not_stopped_by_budget():
    s = new_state(n=0)
    s, d = ib.next_image(s, now=1e9)
    assert d == ib.DONE and s["stopped_by_budget"] is False and s["deferred"] == []


def test_the_last_image_ends_the_batch_as_done():
    s = new_state(n=1, budget=1000)
    s, d = ib.next_image(s, now=1000)
    assert d == ib.CONTINUE
    s = ib.record_image(s, now=1010, status="described", used_model=True)
    s, d = ib.next_image(s, now=1010)
    assert d == ib.DONE and s["stopped_by_budget"] is False


# -- the report ----------------------------------------------------------------------------------------------


def test_the_report_counts_every_outcome_and_the_estimates():
    s = new_state(n=6, budget=100_000, seed=60, started=0, uncached=4)
    plan = [("described", True, 40), ("cached", False, 0), ("failed", True, 50), ("quarantined", True, 30)]
    now = 0.0
    for status, used, secs in plan:
        s, d = ib.next_image(s, now=now)
        assert d == ib.CONTINUE
        now += secs
        s = ib.record_image(s, now=now, status=status, used_model=used)
    s["budget_s"] = 100  # the budget is checked against the cycle clock: 120 s elapsed, so it stops now
    s, d = ib.next_image(s, now=now)
    assert d == ib.STOP
    r = ib.report(s, now=now)
    assert r["images_total"] == 6 and r["cached"] == 1 and r["described"] == 1
    assert r["failed"] == 1 and r["quarantined"] == 1 and r["deferred"] == 2
    assert r["stopped_by_budget"] is True
    assert r["estimate_start_s"] == 240  # four images that needed a model call at 60 s each
    assert r["elapsed_s"] == 120 and r["estimate_end_s"] == pytest.approx(40.0 * 4)
    assert set(r) >= {"estimate_error_s", "per_image_estimate_s"}


def test_the_message_names_the_deferred_images_and_the_budget():
    s = new_state(n=3, budget=100, seed=60, started=0)
    s, _ = ib.next_image(s, now=90)
    msg = ib.describe_report(ib.report(s, now=90))
    assert "3 image(s) deferred" in msg and "time budget" in msg and "next run" in msg


def test_the_estimate_message_before_the_batch():
    msg = ib.describe_estimate(count=7, seed_s=60, budget_s=7200)
    assert "7 image(s)" in msg and "7 min" in msg and "120 min" in msg


# -- SC-004: the property over random durations ----------------------------------------------------------------


@pytest.mark.parametrize("seed", range(300))
def test_the_batch_never_starts_an_image_whose_expected_end_reaches_the_budget(seed):
    rng = random.Random(seed)
    n = rng.randint(0, 40)
    budget = rng.choice([30, 120, 600, 1800, 7200])
    seed_s = rng.choice([10, 60, 120, 400])
    text_phase = rng.choice([0, 0, 50, budget // 2])  # elapsed before the batch starts
    durations = [rng.choice([1, 20, 53, 120, 197, 300, 2.5]) * rng.uniform(0.5, 1.5) for _ in range(n)]
    cached = [rng.random() < 0.25 for _ in range(n)]
    queue = [f"i{k}" for k in range(n)]
    started = 5000.0
    s = ib.start_batch(
        queue,
        started=started,
        batch_started=started + text_phase,
        budget_s=budget,
        seed_s=seed_s,
        to_describe=sum(1 for c in cached if not c),
    )
    now = started + text_phase
    begun: list[str] = []
    while True:
        elapsed_before = now - started
        estimate_before = ib.running_estimate(s)
        s, decision = ib.next_image(s, now=now)
        if decision != ib.CONTINUE:
            break
        # the property: the expected end of every image that starts is inside the budget
        assert elapsed_before + estimate_before < budget
        idx = len(begun)
        begun.append(s["current"])
        now += 0.01 if cached[idx] else durations[idx]
        s = ib.record_image(
            s, now=now, status="cached" if cached[idx] else "described", used_model=not cached[idx]
        )
    rest = queue[len(begun) :]
    assert s["deferred"] == (rest if decision == ib.STOP else [])
    assert begun == queue[: len(begun)]  # list order, nothing skipped
    if decision == ib.DONE:
        assert begun == queue and not s["stopped_by_budget"]
    else:
        assert s["stopped_by_budget"] and rest  # a stop with nothing left is a DONE
        # no image that could have started was left behind: the stop rule holds at the stopping point
        assert (now - started) + ib.running_estimate(s) >= budget
    r = ib.report(s, now=now)
    assert r["images_total"] == n and r["deferred"] == len(rest)
    assert r["described"] + r["cached"] + r["failed"] + r["quarantined"] + r["deferred"] == n


@pytest.mark.parametrize("seed", range(50))
def test_the_next_run_starts_with_the_deferred_images_first_in_list_order(seed):
    rng = random.Random(seed)
    queue = [f"i{k}" for k in range(rng.randint(5, 20))]
    s = ib.start_batch(queue, started=0, batch_started=0, budget_s=300, seed_s=60, to_describe=len(queue))
    now = 0.0
    while True:
        s, d = ib.next_image(s, now=now)
        if d != ib.CONTINUE:
            break
        now += rng.uniform(30, 120)
        s = ib.record_image(s, now=now, status="described", used_model=True)
    deferred = s["deferred"]
    # the done images have a triage row; select_new offers the rest again in the order of the list
    done = set(queue) - set(deferred)
    again = [i for i in queue if i not in done]
    assert again == deferred
