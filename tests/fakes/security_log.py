"""SecurityLogPort fake (spec 009): rows and deny-list entries in memory, same rules as the file adapter."""

from __future__ import annotations

from typing import Any

from pkpipe.security.quarantine import MaliciousUrlEntry, add_to_deny_list
from pkpipe.security.urlfilter import parse_deny_list


class FakeSecurityLog:
    def __init__(self, deny_text: str = ""):
        self.rows: list[dict[str, Any]] = []
        self.deny_text = deny_text

    def log_malicious_url(self, entry: dict[str, Any]) -> None:
        self.rows.append(MaliciousUrlEntry.model_validate(entry).model_dump(mode="json"))

    def deny_domain(self, domain: str, *, day: str) -> dict[str, Any]:
        self.deny_text, added, count = add_to_deny_list(self.deny_text, domain, day)
        return {"domain": domain, "added": added, "count": count}

    def denied_domains(self) -> frozenset[str]:
        return parse_deny_list(self.deny_text)
