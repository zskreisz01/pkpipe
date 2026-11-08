"""Spec 009 US2, SC-002 (code functions of ``handle_security_flag``): one malicious-URL row per flag, the
URL's domain on the deny-list once with a counter, no domain for an item without a URL; the triage rows of
flagged items are quarantined without a trusted category."""

from __future__ import annotations

from types import SimpleNamespace

from pkpipe.steps import CODE
from pkpipe.steps.base import RunInfo
from tests.fakes import FakeClock, FakeSecurityLog
from tests.support.flows import make_ports, make_snapshot


def _ctx(item, outputs=None, log=None, security=None):
    log = log if log is not None else FakeSecurityLog("denied.example\n")
    ports = make_ports(clock=FakeClock(1_793_520_000.0), security_log=log)
    outputs = outputs or {}
    return SimpleNamespace(
        item=item,
        ports=ports,
        run=RunInfo("run-9", "handle_security_flag", "schedule", "v"),
        snapshot=make_snapshot(**({"security": security} if security else {})),
        resolve=lambda ref, required=True: outputs.get(ref),
    ), log


FLAGS = [
    {"flag": "canary_leak", "detector": "canary", "reason_code": "canary_leak"},
    {"flag": "injection_or_jailbreak", "detector": "heuristic", "reason_code": "heuristic:override"},
]


def test_every_flag_is_logged_and_the_domain_deny_listed_once():
    ctx, log = _ctx(
        {"id": "i1", "path": "0-INBOX/a.md", "security_flags": FLAGS},
        {"read": {"front_matter": {"source": "https://www.evil.example/page"}, "body": "x"}},
    )
    out = CODE["security.record_flag"](ctx).output
    assert out["url"] == "https://www.evil.example/page" and out["domain"] == "evil.example"
    assert out["deny"] == {"domain": "evil.example", "added": True, "count": 1}
    assert [(r["flag"], r["detector"], r["evidence"]) for r in log.rows] == [
        ("canary_leak", "canary", "canary_leak"),
        ("injection_or_jailbreak", "heuristic", "heuristic:override"),
    ]
    assert all(r["run_id"] == "run-9" and r["item_id"] == "i1" for r in log.rows)
    ctx2, _ = _ctx({"id": "i2", "url": "https://evil.example/2", "security_flags": FLAGS[:1]}, log=log)
    assert CODE["security.record_flag"](ctx2).output["deny"]["count"] == 2
    assert log.deny_text.count("evil.example") == 1


def test_url_precedence_item_url_extract_resource_then_note():
    for item, expected in [
        ({"url": "https://a.example/x", "resource": "https://b.example"}, "a.example"),
        ({"extract": {"url": "https://c.example/"}, "resource": "0-INBOX/x.md"}, "c.example"),
        ({"resource": "https://d.example/r"}, "d.example"),
    ]:
        ctx, _ = _ctx({**item, "id": "i", "security_flags": FLAGS[:1]})
        assert CODE["security.record_flag"](ctx).output["domain"] == expected


def test_item_without_url_is_logged_without_deny_list_entry():
    ctx, log = _ctx(
        {"id": "i", "path": "0-INBOX/a.md", "security_flags": FLAGS[:1]}, {"read": {"body": "no url"}}
    )
    out = CODE["security.record_flag"](ctx).output
    assert out["url"] == "" and out["domain"] is None and out["deny"] is None
    assert log.rows[0]["url"] == "" and log.deny_text == "denied.example\n"


def test_handler_without_flag_details_still_logs_a_row():
    ctx, log = _ctx({"id": "i", "url": "https://e.example"})
    CODE["security.record_flag"](ctx)
    assert log.rows[0]["flag"] == "unspecified"


def test_quarantined_picks_the_branch_that_ran():
    ctx, _ = _ctx({}, {"quarantine_url": {"path": "0-INBOX/_quarantine/x.md", "moved": False}})
    assert CODE["security.quarantined"](ctx) == {"path": "0-INBOX/_quarantine/x.md", "moved": False}


def test_flagged_triage_items_become_quarantined_rows_and_unhandled_flags_fail():
    ctx = SimpleNamespace(snapshot=make_snapshot())
    results = [
        {"status": "done", "item": {"id": "a", "resource": "0-INBOX/a.md", "category": "tool"}},
        {
            "status": "done",
            "item": {"id": "b", "resource": "0-INBOX/b.md", "flagged": True, "state": "quarantined"},
        },
        {
            "status": "done",
            "item": {"id": "c", "resource": "0-INBOX/c.md", "flagged": True, "category": "tool"},
        },
    ]
    out = CODE["triage.suggested_rows"](ctx, results)
    assert [(r["item_id"], r["state"], r["ai_suggested_category"]) for r in out["rows"]] == [
        ("a", "suggested", "tool"),
        ("b", "quarantined", "none"),
    ]
    assert out["failed"] == ["c"] and out["quarantined"] == 1
    note = CODE["triage.backlog"](ctx, {"rows": []}, out["rows"])
    assert (
        note["new"] == 1
        and note["quarantined"] == 1
        and "quarantined by the security checks" in note["message"]
    )


def test_a_protected_host_is_logged_but_never_deny_listed():
    """Spec 028: one flagged page of github.com must not block every other github.com link."""
    sec = {"url_filter": {"protected_domains": ["github.com"]}}
    ctx, log = _ctx(
        {"id": "i1", "url": "https://github.com/some/repo", "security_flags": FLAGS[:1]}, security=sec
    )
    out = CODE["security.record_flag"](ctx).output
    assert out["domain"] == "github.com"
    assert out["deny"] == {"domain": "github.com", "added": False, "count": 0, "protected": True}
    assert log.deny_text == "denied.example\n"  # the deny-list file is untouched
    assert log.rows[0]["domain"] == "github.com"  # the flag itself is still logged
    ctx2, log2 = _ctx(
        {"id": "i2", "url": "https://gist.github.com/x", "security_flags": FLAGS[:1]}, security=sec
    )
    assert CODE["security.record_flag"](ctx2).output["deny"]["protected"] is True  # a subdomain is covered
