"""Several ClassifierPorts as one: the maximum score wins (spec 008 FR-004, spec 009 FR-002).

The reference deployment combines Qwen3Guard-Gen (harm categories, jailbreaks) with the ProtectAI DeBERTa
injection classifier (indirect prompt injection), see ``docs/decisions/0001-injection-classifier.md``.
``scan`` returns the usual ``{"model", "max_score", "chunks", "scores"}`` plus ``members``: the result of
every member, so the output guard can tell a harm category (``malicious_content``) from an injection score
(``injection_or_jailbreak``). A member that is unavailable makes the whole classifier unavailable (fail
closed): a combination is never silently reduced to one model.
"""

from __future__ import annotations

from typing import Any

from pkpipe.ports import ClassifierPort, ClassifierUnavailable


class CombinedClassifier:
    def __init__(self, members: list[ClassifierPort]):
        if not members:
            raise ValueError("a combined classifier needs at least one member")
        self.members = list(members)

    @property
    def model(self) -> str:
        return "+".join(str(getattr(m, "model", type(m).__name__)) for m in self.members)

    def available(self) -> bool:
        from pkpipe.adapters.classifiers import is_available

        return all(is_available(m) for m in self.members)

    def scan(self, text: str) -> dict[str, Any]:
        results = []
        for member in self.members:
            try:
                results.append(member.scan(text))
            except ClassifierUnavailable as exc:
                name = getattr(member, "model", type(member).__name__)
                raise ClassifierUnavailable(f"{name}: {exc}") from exc
        best = max(results, key=lambda r: float(r.get("max_score") or 0.0))
        out: dict[str, Any] = {
            "model": self.model,
            "max_score": float(best.get("max_score") or 0.0),
            "chunks": max(int(r.get("chunks") or 0) for r in results),
            "scores": [float(r.get("max_score") or 0.0) for r in results],
            "members": results,
        }
        labelled = [r for r in results if r.get("label")]
        if labelled:
            out["label"] = labelled[0]["label"]
            out["categories"] = list(labelled[0].get("categories") or [])
        return out
