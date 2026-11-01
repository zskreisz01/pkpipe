"""Spec 009 US2: the file adapter of SecurityLogPort appends malicious URL rows as JSON lines and edits the
deny-list atomically, starting a new overlay deny-list from the pack's list (seed)."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pkpipe.adapters.security_log_files import FileSecurityLog, append_eval_candidate
from pkpipe.ports import SecurityLogPort


def _row(**kw):
    base = {
        "url": "https://evil.example/x",
        "domain": "evil.example",
        "flag": "canary_leak",
        "detector": "canary",
        "run_id": "r1",
        "timestamp": datetime(2026, 11, 1, 6, 0, tzinfo=UTC),
    }
    return {**base, **kw}


def test_rows_are_json_lines_and_invalid_rows_are_refused(tmp_path):
    log = FileSecurityLog(tmp_path / "security")
    assert isinstance(log, SecurityLogPort)
    log.log_malicious_url(_row())
    log.log_malicious_url(_row(flag="heuristic", domain=None, url=""))
    lines = (tmp_path / "security" / "malicious_urls.jsonl").read_text().splitlines()
    assert [json.loads(x)["flag"] for x in lines] == ["canary_leak", "heuristic"]
    with pytest.raises(ValidationError):
        log.log_malicious_url({**_row(), "page_text": "raw"})  # unknown field: never stored


def test_new_deny_list_starts_from_the_seed_and_counts(tmp_path):
    deny = tmp_path / "overlay" / "security" / "deny_list.txt"
    log = FileSecurityLog(tmp_path / "overlay" / "security", deny, seed="denied.example\n")
    assert log.deny_domain("evil.example", day="2026-11-01") == {
        "domain": "evil.example",
        "added": True,
        "count": 1,
    }
    assert log.deny_domain("evil.example", day="2026-11-01")["count"] == 2
    assert log.denied_domains() == {"denied.example", "evil.example"}
    assert deny.read_text().startswith("denied.example\n")


def test_concurrent_flags_of_one_domain_count_every_flag(tmp_path):
    log = FileSecurityLog(tmp_path)
    threads = [
        threading.Thread(target=log.deny_domain, args=("evil.example",), kwargs={"day": "d"})
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    text = (tmp_path / "deny_list.txt").read_text()
    assert text.count("evil.example") == 1 and "flagged 8," in text


def test_eval_candidates_are_appended_once_per_id(tmp_path):
    path = tmp_path / "candidates" / "security.jsonl"
    case = {"id": "release-a", "input": {"text": "t"}, "expected": {"label": "benign"}, "source": "release"}
    assert append_eval_candidate(path, case) is True
    assert append_eval_candidate(path, case) is False
    assert len(path.read_text().splitlines()) == 1
