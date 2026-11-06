"""SecurityLogPort on files (spec 009 US2): ``malicious_urls.jsonl`` and the deny-list file.

The reference deployment points both into the private overlay (``<overlay>/security/``), so the deny-list
the handler extends is the same file the URL filter, the web-fetch server and the learning gateway read
(``security.url_filter.deny_list``); without an overlay they live in ``$PKPIPE_DATA_DIR/security/`` and the
in-process URL filter merges :meth:`denied_domains`. A new overlay deny-list starts from the pack's list
(``seed``), so creating it never drops a domain the pack denies. Writes are atomic and lock-protected.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from pkpipe.obsidian.atomic import dir_lock, update_text
from pkpipe.security.quarantine import MaliciousUrlEntry, add_to_deny_list
from pkpipe.security.urlfilter import parse_deny_list

LOG_NAME = "malicious_urls.jsonl"


class FileSecurityLog:
    def __init__(self, directory: Path, deny_file: Path | None = None, *, seed: str | None = None):
        self.directory = Path(directory)
        self.deny_file = Path(deny_file) if deny_file else self.directory / "deny_list.txt"
        self.seed = seed
        self._lock = threading.Lock()

    @property
    def log_file(self) -> Path:
        return self.directory / LOG_NAME

    def log_malicious_url(self, entry: dict[str, Any]) -> None:
        row = MaliciousUrlEntry.model_validate(entry).model_dump(mode="json")
        line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        self.directory.mkdir(parents=True, exist_ok=True)
        with self._lock, dir_lock(self.directory), open(self.log_file, "a", encoding="utf-8") as fh:
            fh.write(line)

    def deny_domain(self, domain: str, *, day: str) -> dict[str, Any]:
        result: dict[str, Any] = {}

        def transform(text: str | None) -> str:
            new, added, count = add_to_deny_list(text if text is not None else self.seed, domain, day)
            result.update(domain=domain, added=added, count=count)
            return new

        with self._lock:
            update_text(self.deny_file, transform)
        return result

    def denied_domains(self) -> frozenset[str]:
        try:
            return parse_deny_list(self.deny_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return frozenset()

    def entries(self) -> list[dict[str, Any]]:
        try:
            lines = self.log_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in lines if line.strip()]


def append_eval_candidate(path: Path, case: dict[str, Any]) -> bool:
    """Append an eval case candidate (spec 009 US3) to a ``.jsonl`` file once per id; False if present."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(case, ensure_ascii=False, sort_keys=True)
    added = False

    def transform(text: str | None) -> str:
        nonlocal added
        existing = text or ""
        ids = {json.loads(x).get("id") for x in existing.splitlines() if x.strip()}
        if case.get("id") in ids:
            added = False
            return existing
        added = True
        return existing + ("" if not existing or existing.endswith("\n") else "\n") + line + "\n"

    update_text(path, transform)
    return added


def upsert_eval_candidate(path: Path, case: dict[str, Any]) -> str:
    """Add an eval case candidate, or replace the one with the same id (spec 023: an override candidate
    gets its reason when the owner writes the comment later). Returns ``added``, ``updated`` or ``unchanged``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(case, ensure_ascii=False, sort_keys=True)
    outcome = "added"

    def transform(text: str | None) -> str:
        nonlocal outcome
        lines = [x for x in (text or "").splitlines() if x.strip()]
        for i, existing in enumerate(lines):
            if json.loads(existing).get("id") == case.get("id"):
                outcome = "unchanged" if existing == line else "updated"
                lines[i] = line
                break
        else:
            outcome = "added"
            lines.append(line)
        return "\n".join(lines) + "\n"

    update_text(path, transform)
    return outcome
