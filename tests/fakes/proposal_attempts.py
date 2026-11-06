from __future__ import annotations


class FakeProposalAttempts:
    """ProposalAttemptsPort fake: failed new-category evaluations per item in memory (spec 022)."""

    def __init__(self, counts: dict[str, tuple[str, int]] | None = None):
        self.counts: dict[str, tuple[str, int]] = dict(counts or {})

    def get(self, item_id: str, requested: str) -> int:
        name, n = self.counts.get(item_id, ("", 0))
        return n if name == requested else 0

    def bump(self, item_id: str, requested: str) -> int:
        n = self.get(item_id, requested) + 1
        self.counts[item_id] = (requested, n)
        return n

    def clear(self, item_id: str) -> None:
        self.counts.pop(item_id, None)
