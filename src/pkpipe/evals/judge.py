"""LLM judge: rubric scores (1-5 with reasoning), pairwise A/B comparison and calibration (spec 013 FR-002/3).

The judge scores quality only; schema, required elements and forbidden content are checked by code first
(:mod:`pkpipe.evals.scoring`). The judge is never the model under test: :class:`Judge` refuses to start when
its profile resolves to the same endpoint and model as the profile being evaluated (FR-002). Inputs and
outputs reach the judge datamarked, like any untrusted text (constitution IV).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pkpipe.ports import ModelError, ModelPort
from pkpipe.steps.base import datamark, datamark_notice, datamark_token

JUDGE_SYSTEM = (
    "You are a strict, fair evaluator of outputs produced by another assistant. Judge only against the "
    "rubric. Do not reward length. Answer only with JSON that matches the schema."
)
SCORE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reasoning", "score"],
    "properties": {
        "reasoning": {"type": "string", "maxLength": 800},
        "score": {"type": "integer", "minimum": 1, "maximum": 5},
    },
}
PAIRWISE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reasoning", "winner"],
    "properties": {
        "reasoning": {"type": "string", "maxLength": 800},
        "winner": {"type": "string", "enum": ["A", "B", "tie"]},
    },
}


class JudgeUnavailable(Exception):
    """No usable judge: not configured, same model as the one under test (FR-002), or unreachable."""


@dataclass(frozen=True)
class JudgeScore:
    score: int
    reasoning: str


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1, default=str)


def _marked(sections: Sequence[tuple[str, Any]]) -> tuple[str, str]:
    texts = [_text(v) for _, v in sections]
    token = datamark_token(*texts)
    body = "\n\n".join(
        f"### {title}\n{datamark(t, token)}" for (title, _), t in zip(sections, texts, strict=True)
    )
    return body, token


class Judge:
    def __init__(self, model: ModelPort, profile: str, *, identity: str, subject_identities: Sequence[str]):
        """``identity`` is the resolved ``base_url|model`` of the judge profile; ``subject_identities`` those
        of every profile under test. Equal identities raise :class:`JudgeUnavailable` (FR-002)."""
        if identity in set(subject_identities):
            raise JudgeUnavailable(
                f"judge profile '{profile}' resolves to the model under test ({identity}); "
                "configure a different judge model (PKPIPE_JUDGE_MODEL)"
            )
        self.model = model
        self.profile = profile
        self.identity = identity

    def _ask(self, user: str, token: str, schema: dict[str, Any]) -> dict[str, Any]:
        system = JUDGE_SYSTEM + "\n\n" + datamark_notice(token)
        try:
            return self.model.complete(system, user, schema, profile=self.profile)
        except ModelError as exc:
            raise JudgeUnavailable(f"judge call failed: {type(exc).__name__}: {exc}") from exc

    def score(self, rubric: str, inputs: Mapping[str, Any], output: Any) -> JudgeScore:
        body, token = _marked([*((f"Input: {k}", v) for k, v in inputs.items()), ("Output to judge", output)])
        user = (
            "## Rubric\n\n" + rubric.strip() + "\n\n## Material\n\n" + body + "\n\n"
            "Give one overall score from 1 (unusable) to 5 (excellent) for the output, with short reasoning."
        )
        reply = self._ask(user, token, SCORE_SCHEMA)
        return JudgeScore(int(reply["score"]), str(reply.get("reasoning", "")))

    def _prefer(self, rubric: str, inputs: Mapping[str, Any], first: Any, second: Any) -> str:
        body, token = _marked(
            [*((f"Input: {k}", v) for k, v in inputs.items()), ("Output A", first), ("Output B", second)]
        )
        user = (
            "## Rubric\n\n" + rubric.strip() + "\n\n## Material\n\n" + body + "\n\n"
            "Which output is better according to the rubric? Answer A, B or tie."
        )
        return str(self._ask(user, token, PAIRWISE_SCHEMA)["winner"])

    def compare(self, rubric: str, inputs: Mapping[str, Any], baseline: Any, candidate: Any) -> str:
        """Pairwise A/B (FR-003): ``"candidate"``, ``"baseline"`` or ``"tie"``. Asked twice with the order
        swapped; only a consistent preference counts (position bias becomes a tie)."""
        first = self._prefer(rubric, inputs, baseline, candidate)  # A = baseline
        second = self._prefer(rubric, inputs, candidate, baseline)  # A = candidate
        one = {"A": "baseline", "B": "candidate"}.get(first, "tie")
        two = {"A": "candidate", "B": "baseline"}.get(second, "tie")
        return one if one == two else "tie"


def pairwise_score(outcomes: Sequence[str]) -> float:
    """Share of wins for the candidate, ties counting half (0.5 = no difference)."""
    if not outcomes:
        return 0.0
    return round(
        sum(1.0 if o == "candidate" else 0.5 if o == "tie" else 0.0 for o in outcomes) / len(outcomes), 4
    )


def calibration_metrics(pairs: Sequence[tuple[int, int]], max_gap: int = 1) -> dict[str, Any]:
    """(owner, judge) score pairs -> agreement statistics. ``disagreement_rate`` is the share of items where
    the judge is more than ``max_gap`` points from the owner; above the configured limit the rubric needs
    refining (docs/evaluation.md "Calibration")."""
    n = len(pairs)
    if not n:
        return {"n": 0, "mean_abs_diff": 0.0, "exact_agreement": 0.0, "disagreement_rate": 0.0, "bias": 0.0}
    diffs = [j - o for o, j in pairs]
    return {
        "n": n,
        "mean_abs_diff": round(sum(abs(d) for d in diffs) / n, 4),
        "exact_agreement": round(sum(1 for d in diffs if d == 0) / n, 4),
        "disagreement_rate": round(sum(1 for d in diffs if abs(d) > max_gap) / n, 4),
        "bias": round(sum(diffs) / n, 4),  # > 0: the judge is more lenient than the owner
    }
