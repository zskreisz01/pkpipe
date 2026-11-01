"""Output guard (spec 009 FR-001, FR-002): the code-made check of every session output that derives from
external content, before anything is written to the vault.

A session output that carries an injected instruction would attack every agent that reads the vault later
(stored injection), so it is checked like external input:

==========================  =================================================================================
flag                        detector
==========================  =================================================================================
``canary_leak``             the run's canary (or its hex part) anywhere in the output: deterministic string
                            match, independent of every model
``injection_or_jailbreak``  active markup (script, iframe, ``javascript:`` URLs, event handler attributes),
                            heuristics (``security.heuristics.threshold``), an injection classifier score
                            (``security.classifier.threshold``), a ``Jailbreak`` category of the safety
                            classifier, or no usable classifier while ``security.classifier.required``
``malicious_content``       a safety label in ``security.output_guard.block_on`` (default ``Unsafe``) with a
                            harm category (Violent, PII, ...) or with no category at all (fail closed)
==========================  =================================================================================

Models only report scores, labels and categories; this module decides (constitution V). Only flag names,
detectors, reason codes and scores leave it, never the text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pkpipe.ports import ClassifierUnavailable
from pkpipe.security import heuristics
from pkpipe.security.canary import leaked

OutputFlag = Literal["malicious_content", "injection_or_jailbreak", "canary_leak"]
JAILBREAK = "Jailbreak"
#: Markup that runs code when a note is rendered (a plugin, an HTML export): never part of a clean note.
ACTIVE_MARKUP = re.compile(
    r"<\s*(script|iframe|object|embed)\b|javascript\s*:|<[a-z][^>]*\son[a-z]+\s*=", re.IGNORECASE
)


class GuardFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flag: OutputFlag
    detector: str = Field(description="canary, heuristic, classifier or a classifier model name")
    reason_code: str
    score: float | None = None


class GuardResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flagged: bool
    flags: list[GuardFlag] = Field(default_factory=list)
    scores: dict[str, Any] = Field(default_factory=dict)

    def flag_names(self) -> list[str]:
        out: list[str] = []
        for f in self.flags:
            if f.flag not in out:
                out.append(f.flag)
        return out


def output_text(value: Any) -> str:
    """Every string in an output (dict values, list items, nested), one per line, in a stable order. Keys are
    the schema's own field names and not checked; numbers and booleans cannot carry an instruction."""
    parts: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            if v.strip():
                parts.append(v)
        elif isinstance(v, Mapping):
            for k in v:
                walk(v[k])
        elif isinstance(v, list | tuple):
            for x in v:
                walk(x)

    walk(value)
    return "\n".join(parts)


def _members(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    members = result.get("members")
    return list(members) if isinstance(members, list) and members else [result]


def classifier_flags(
    result: Mapping[str, Any], *, threshold: float, block_on: Iterable[str]
) -> list[GuardFlag]:
    """Flags from one classifier result (a single model or the members of a combination). A safety
    moderator (``label`` present) flags harm categories under ``block_on`` as ``malicious_content`` and a
    ``Jailbreak`` category at or above ``threshold`` as ``injection_or_jailbreak``; an injection detector
    (no ``label``) flags its score at or above ``threshold``."""
    flags: list[GuardFlag] = []
    block = set(block_on)
    for member in _members(result):
        model = str(member.get("model") or "classifier")
        score = float(member.get("max_score") or 0.0)
        label = member.get("label")
        categories = [str(c) for c in member.get("categories") or []]
        if label is None:
            if score >= threshold:
                flags.append(
                    GuardFlag(
                        flag="injection_or_jailbreak",
                        detector=model,
                        reason_code="classifier_threshold",
                        score=score,
                    )
                )
            continue
        harm = [c for c in categories if c != JAILBREAK]
        if label in block and harm:
            flags.append(
                GuardFlag(
                    flag="malicious_content",
                    detector=model,
                    reason_code="category:" + ",".join(sorted(harm)),
                    score=score,
                )
            )
        elif label in block and JAILBREAK not in categories:
            # a blocking label without a known category (the model named none): fail closed
            flags.append(
                GuardFlag(
                    flag="malicious_content",
                    detector=model,
                    reason_code=f"label:{label}",
                    score=score,
                )
            )
        if JAILBREAK in categories and score >= threshold:
            flags.append(
                GuardFlag(
                    flag="injection_or_jailbreak",
                    detector=model,
                    reason_code="category:Jailbreak",
                    score=score,
                )
            )
        elif label == "Unparsed":
            flags.append(
                GuardFlag(
                    flag="injection_or_jailbreak",
                    detector=model,
                    reason_code="classifier_unparsed",
                    score=score,
                )
            )
    return flags


def check_output(
    value: Any,
    *,
    canary: str | None,
    classifier: Any | None,
    security: Mapping[str, Any] | None = None,
) -> GuardResult:
    """Run every detector on ``value`` (a session output: dict, list or text) and decide in code.

    ``security`` is the ``security`` config section (thresholds, ``block_on``, ``classifier.required``).
    ``classifier=None`` or an unavailable classifier fails closed unless ``classifier.required`` is false."""
    sec = dict(security or {})
    cls_cfg = dict(sec.get("classifier") or {})
    threshold = float(cls_cfg.get("threshold", 0.5))
    required = bool(cls_cfg.get("required", True))
    block_on = list((sec.get("output_guard") or {}).get("block_on") or ["Unsafe"])
    h_threshold = float((sec.get("heuristics") or {}).get("threshold", 1.0))

    text = output_text(value)
    flags: list[GuardFlag] = []
    scores: dict[str, Any] = {}
    if canary:
        dumped = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        if leaked(canary, dumped, text):
            flags.append(GuardFlag(flag="canary_leak", detector="canary", reason_code="canary_leak"))
    if ACTIVE_MARKUP.search(text):
        flags.append(GuardFlag(flag="injection_or_jailbreak", detector="markup", reason_code="active_markup"))
    found = heuristics.scan(text)
    scores["heuristic"] = found.score
    if found.score >= h_threshold:
        flags.append(
            GuardFlag(
                flag="injection_or_jailbreak",
                detector="heuristic",
                reason_code="heuristic:" + ",".join(found.matches),
                score=found.score,
            )
        )
    if classifier is None:
        scores["classifier"] = None
        if required:
            flags.append(
                GuardFlag(
                    flag="injection_or_jailbreak", detector="classifier", reason_code="classifier_unavailable"
                )
            )
    else:
        try:
            result = classifier.scan(text)
        except ClassifierUnavailable:
            scores["classifier"] = None
            if required:
                flags.append(
                    GuardFlag(
                        flag="injection_or_jailbreak",
                        detector="classifier",
                        reason_code="classifier_unavailable",
                    )
                )
        else:
            scores["classifier"] = float(result.get("max_score") or 0.0)
            if result.get("label"):
                scores["label"] = result["label"]
            flags += classifier_flags(result, threshold=threshold, block_on=block_on)
    return GuardResult(flagged=bool(flags), flags=flags, scores=scores)


def guard_json_schemas() -> dict[str, dict[str, Any]]:
    """Exchanged spec 009 structures for ``pkpipe schema`` (data-model): the output guard's result and a row
    of ``security/malicious_urls.jsonl``."""
    from pkpipe.security.quarantine import MaliciousUrlEntry

    return {
        "output-guard-result": GuardResult.model_json_schema(),
        "malicious-url-entry": MaliciousUrlEntry.model_json_schema(),
    }
