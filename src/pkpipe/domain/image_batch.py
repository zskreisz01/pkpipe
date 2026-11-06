"""The time guard of the image batch (spec 021 clarification 7, FR-014): pure functions over a plain dict.

One processing cycle describes its images one after the other. Before every image the guard stops when the
expected end, ``elapsed + running estimate``, reaches the time budget; that image and the rest are *deferred*
(they stay in the INBOX list untouched: no triage row, no removal) and come first in the next run. The
estimate is the median of the last runs (the timing table, or ``images.estimate_s`` on a first run) until
images of this run are done, then their mean. ``elapsed`` runs from the start of the cycle, so the text
phase that precedes the images has used its part of the budget already.

The state is a JSON-able dict (it travels through the flow state and its checkpoints) and every function
takes the time as an argument: the caller reads the clock port, so a test drives the guard with a fake clock.
"""

from __future__ import annotations

import statistics
from typing import Any

CONTINUE = "continue"
STOP = "stop"
DONE = "done"

#: Outcomes of one image (the ``status`` of a result)
DESCRIBED = "described"
CACHED = "cached"
FAILED = "failed"
QUARANTINED = "quarantined"
STATUSES = (DESCRIBED, CACHED, FAILED, QUARANTINED)


def seed_estimate(recent: list[float], default: float) -> float:
    """Median of the recent per-image seconds (rows that are not positive are ignored), else ``default``."""
    usable = [float(s) for s in recent if s and s > 0]
    return float(statistics.median(usable)) if usable else float(default)


def start_batch(
    queue: list[str],
    *,
    started: float,
    batch_started: float,
    budget_s: float,
    seed_s: float,
    to_describe: int,
) -> dict[str, Any]:
    """The state at the start. ``queue`` holds the item ids in list order; ``started`` is the start of the
    cycle (the budget clock), ``batch_started`` the start of the image batch, ``to_describe`` the number of
    images that need a model call (cached ones are free)."""
    return {
        "queue": list(queue),
        "cursor": 0,
        "started": float(started),
        "batch_started": float(batch_started),
        "budget_s": float(budget_s),
        "seed_s": float(seed_s),
        "to_describe": int(to_describe),
        "durations": [],
        "results": [],
        "deferred": [],
        "current": None,
        "t0": None,
        "stopped_by_budget": False,
    }


def running_estimate(state: dict[str, Any]) -> float:
    """Seconds one image is expected to take: the mean of this run's model calls, else the seed."""
    durations = state["durations"]
    return float(sum(durations) / len(durations)) if durations else float(state["seed_s"])


def next_image(state: dict[str, Any], *, now: float) -> tuple[dict[str, Any], str]:
    """Decide before the next image: ``continue`` (``current`` is set, the clock starts), ``stop`` (the budget
    would be reached: the rest is deferred) or ``done`` (nothing left)."""
    s = dict(state)
    if s["cursor"] >= len(s["queue"]):
        s.update(current=None, t0=None)
        return s, DONE
    elapsed = now - s["started"]
    if elapsed + running_estimate(s) >= s["budget_s"]:
        s.update(deferred=list(s["queue"][s["cursor"] :]), cursor=len(s["queue"]), current=None, t0=None)
        s["stopped_by_budget"] = True
        return s, STOP
    s.update(current=s["queue"][s["cursor"]], cursor=s["cursor"] + 1, t0=float(now))
    return s, CONTINUE


def record_image(
    state: dict[str, Any],
    *,
    now: float,
    status: str,
    used_model: bool,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The outcome of the current image. A call that used the model adds its seconds to the estimate (also
    a failed or timed-out one: it took that long); a cached image costs nothing."""
    if status not in STATUSES:
        raise ValueError(f"unknown image status '{status}'")
    s = dict(state)
    seconds = max(0.0, now - s["t0"]) if s.get("t0") is not None else 0.0
    if used_model:
        s["durations"] = [*s["durations"], seconds]
    s["results"] = [
        *s["results"],
        {**(result or {}), "id": s["current"], "status": status, "seconds": seconds},
    ]
    s.update(current=None, t0=None)
    return s


def report(state: dict[str, Any], *, now: float) -> dict[str, Any]:
    """The ``ImageBatchReport`` (data-model.md): counts, the estimates and the elapsed time."""
    counts = {k: 0 for k in STATUSES}
    for r in state["results"]:
        counts[r["status"]] += 1
    elapsed = max(0.0, now - state["batch_started"])
    start_s = state["seed_s"] * state["to_describe"]
    per_image = running_estimate(state)
    end_s = per_image * state["to_describe"]
    return {
        "images_total": len(state["queue"]),
        "cached": counts[CACHED],
        "described": counts[DESCRIBED],
        "failed": counts[FAILED],
        "quarantined": counts[QUARANTINED],
        "deferred": len(state["deferred"]),
        "estimate_start_s": round(start_s, 1),
        "estimate_end_s": round(end_s, 1),
        "estimate_error_s": round(start_s - elapsed, 1),
        "per_image_estimate_s": round(per_image, 1),
        "elapsed_s": round(elapsed, 1),
        "cycle_elapsed_s": round(max(0.0, now - state["started"]), 1),
        "budget_s": round(state["budget_s"], 1),
        "stopped_by_budget": bool(state["stopped_by_budget"]),
    }


def _minutes(seconds: float) -> int:
    return max(1, round(seconds / 60)) if seconds > 0 else 0


def describe_estimate(*, count: int, seed_s: float, budget_s: float, elapsed_s: float = 0.0) -> str:
    """The message before the batch (log and notification): how many images need a model call and how long.
    ``elapsed_s`` is what the cycle has used already (the text phase): the budget shown is what is left."""
    text = (
        f"Image batch: {count} image(s) need a model call, expected about {_minutes(count * seed_s)} min "
        f"({round(seed_s)} s each); "
    )
    if elapsed_s >= 30:
        left = max(0.0, budget_s - elapsed_s)
        return text + (
            f"time budget {_minutes(budget_s)} min, {_minutes(elapsed_s)} min already used by the text "
            f"phase, {_minutes(left) if left else 0} min left."
        )
    return text + f"time budget {_minutes(budget_s)} min."


def describe_report(rep: dict[str, Any]) -> str:
    """Counts only, never a description or a file name (spec 021 SC-003)."""
    parts = [f"{rep['described']} described", f"{rep['cached']} from cache"]
    if rep.get("failed"):
        parts.append(f"{rep['failed']} failed")
    if rep.get("quarantined"):
        parts.append(f"{rep['quarantined']} quarantined")
    text = f"Images: {', '.join(parts)} in {_minutes(rep['elapsed_s'])} min."
    done = rep["described"] + rep["cached"] + rep["failed"] + rep["quarantined"]
    if rep["deferred"] and rep["stopped_by_budget"] and not done:
        return (
            f"Images: all {rep['deferred']} image(s) deferred before the first image: the time budget of "
            f"{_minutes(rep['budget_s'])} min was already used up ({_minutes(rep['cycle_elapsed_s'])} min, "
            "the text phase took the time). They stay in the INBOX list and are picked up first in the "
            "next run."
        )
    if rep["deferred"]:
        text += (
            f" {rep['deferred']} image(s) deferred: the time budget is reached; they stay in the INBOX list "
            "and are picked up first in the next run."
        )
    return text
