"""What the classifier models said about a page and how to read it (spec 029). Pure logic.

A flag used to carry only the reason code ``classifier_threshold``. The owner could not tell which model
fired, how high, on how much of the page, or whether the other model agreed. This module builds that
evidence from the scores alone (numbers, labels, chunk numbers; never page text) and turns it into fixed
sentences, so the quarantine note, the log, the UI and the review rule all read the same facts.

Two kinds of member: an *injection* detector (ProtectAI DeBERTa: "does this read like an instruction to an
AI") has no label; a *safety* model (Qwen3Guard-Gen: Safe / Controversial / Unsafe plus categories) has one.
"""

from __future__ import annotations

from typing import Any

MAX_CHUNKS_LISTED = 20
SAFE_LABELS = frozenset({"safe"})

READING_INJECTION_ONLY = "injection_detector_only"
READING_SAFETY_ONLY = "safety_model_only"
READING_BOTH = "both_over_threshold"
READING_NONE = "none_over_threshold"
READING_SINGLE = "single_model"


def _short(model: Any) -> str:
    return str(model or "model").split("/")[-1]


def member_evidence(member: dict[str, Any], threshold: float) -> dict[str, Any]:
    """One model's facts: its kind, its top score, the label, how many chunks scored over the threshold."""
    scores = [float(s) for s in member.get("scores") or []]
    top = float(member.get("max_score") or (max(scores) if scores else 0.0))
    over = [i + 1 for i, s in enumerate(scores) if s >= threshold]
    out: dict[str, Any] = {
        "model": _short(member.get("model")),
        "kind": "safety" if member.get("label") else "injection",
        "max_score": round(top, 3),
        "chunks": int(member.get("chunks") or len(scores)),
        "over": top >= threshold,
        "flagged_chunks": over[:MAX_CHUNKS_LISTED],
        "flagged_chunk_count": len(over),
    }
    if member.get("label"):
        out["label"] = str(member["label"])
        out["categories"] = [str(c) for c in member.get("categories") or []][:8]
    return out


def classifier_evidence(classifier: dict[str, Any], threshold: float) -> dict[str, Any]:
    """The evidence of a ``scan_page`` classifier result (``available`` true), for the verdict."""
    members = [member_evidence(m, threshold) for m in classifier.get("members") or [] if isinstance(m, dict)]
    top = float(classifier.get("max_score") or 0.0)
    if not members:
        members = [member_evidence({**classifier}, threshold)]
        reading = READING_SINGLE
    else:
        inj = [m for m in members if m["kind"] == "injection"]
        saf = [m for m in members if m["kind"] == "safety"]
        inj_over = any(m["over"] for m in inj)
        saf_over = any(m["over"] or str(m.get("label", "")).lower() not in SAFE_LABELS for m in saf)
        if inj_over and saf_over:
            reading = READING_BOTH
        elif inj_over:
            reading = READING_INJECTION_ONLY if saf else READING_SINGLE
        elif saf_over:
            reading = READING_SAFETY_ONLY
        else:
            reading = READING_NONE
    return {
        "threshold": threshold,
        "max_score": round(top, 3),
        "members": members,
        "reading": reading,
    }


def interpret(evidence: dict[str, Any]) -> list[str]:
    """Fixed sentences that explain the evidence to the owner (no page text)."""
    thr = float(evidence.get("threshold") or 0.0)
    lines: list[str] = []
    for m in evidence.get("members") or []:
        where = ""
        if m.get("flagged_chunk_count"):
            shown = ", ".join(str(i) for i in m["flagged_chunks"])
            more = "" if m["flagged_chunk_count"] <= len(m["flagged_chunks"]) else ", ..."
            where = f" Over the threshold in chunk(s) {shown}{more} of {m['chunks']}."
        if m["kind"] == "injection":
            lines.append(
                f"{m['model']} (prompt-injection detector): top score {m['max_score']:.2f}, threshold "
                f"{thr:.2f}.{where} It scores how much a text reads like an instruction to an AI. It looks "
                "at wording and layout, not at intent, so HTML markup, badge rows and templated pages can "
                "score high."
            )
        else:
            cats = f" ({', '.join(m['categories'])})" if m.get("categories") else ""
            verdict = (
                "no harmful or jailbreak content found"
                if str(m.get("label", "")).lower() in SAFE_LABELS and not m["over"]
                else "it found harmful or jailbreak content"
            )
            lines.append(
                f"{m['model']} (safety labeller): label {m.get('label')}{cats}, top score "
                f"{m['max_score']:.2f}: {verdict}."
            )
    reading = evidence.get("reading")
    lines.append(
        {
            READING_INJECTION_ONLY: "Reading: only the injection detector fires and the safety model says "
            "Safe. This is the pattern of a false positive on markup-heavy pages; check the page yourself.",
            READING_BOTH: "Reading: both models fire. Treat it as a real finding.",
            READING_SAFETY_ONLY: "Reading: only the safety model fires. Treat it as a real finding.",
            READING_SINGLE: "Reading: one model only, so there is no second opinion.",
            READING_NONE: "Reading: no model is over the threshold (another detector flagged the item).",
        }.get(str(reading), "")
    )
    return [line for line in lines if line]


def review_applies(evidence: dict[str, Any] | None) -> bool:
    """The review rule (spec 029): the injection detector alone fires and every safety model says Safe."""
    return bool(evidence) and evidence.get("reading") == READING_INJECTION_ONLY
