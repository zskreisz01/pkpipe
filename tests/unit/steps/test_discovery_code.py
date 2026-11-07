"""Spec 025: the code functions of source discovery (query, guard, search, candidates, next page)."""

from __future__ import annotations

from typing import Any

import pytest

from pkpipe.steps import CODE, RunInfo, StepContext
from pkpipe.steps.discovery_code import discovery_applies
from tests.fakes import FakeSearch, FakeTools, RecordingTracer
from tests.support.flows import make_ports, make_snapshot

CONFIG = {"enabled": True, "categories": ["tool"]}
IMAGE = {
    "id": "i1",
    "kind": "image",
    "category": "tool",
    "description": {
        "topic": "A screenshot of the Vectorlite tool",
        "visible_text": "vector search for SQLite",
    },
}


def _ctx(item, *, search=None, discovery=CONFIG, terms=(), tracer=None, resolve=None):
    snap = make_snapshot(
        allow=["search", "fetch_page"],
        source_discovery=discovery,
        security={"query_guard": {"blocked_terms": list(terms)}},
    )
    snap.data["mcp_servers"]["websearch"] = {
        "transport": "stdio",
        "command": "unused",
        "allow": ["search"],
        "egress": True,
    }
    tools = FakeTools({"websearch:search": (search or FakeSearch()).search})
    ports = make_ports(
        tools=tools, tracer=tracer or RecordingTracer(), resolver=resolve or (lambda h: ["93.184.216.34"])
    )
    return StepContext(
        flow=None,
        node=None,
        run=RunInfo("r", "f", "manual", snap.version),
        snapshot=snap,
        ports=ports,
        item=item,
        inputs={},
        resolver=lambda ref: None,
    ), tools


def _find(item, **kw):
    ctx, tools = _ctx(item, **kw)
    return CODE["item.find_sources"](ctx, note={"body": "ignored for images"}), tools


def _out(result):
    return result.output if hasattr(result, "output") else result


def test_an_image_description_becomes_one_guarded_query_and_the_candidates_are_kept():
    search = FakeSearch(
        [
            {"url": "https://a.example/one", "title": "t", "snippet": "s"},
            {"url": "https://b.example/two"},
            {"url": "https://c.example/three"},
        ]
    )
    res, tools = _find(IMAGE, search=search)
    assert search.queries == ["A screenshot of the Vectorlite tool vector search for SQLite"]
    assert _out(res) == {"status": "ok", "found": 2, "results": 3, "dropped": 1}
    assert res.item == {
        "source_url": "https://a.example/one",
        "source_candidates": ["https://b.example/two"],
        "source_discovered": True,
    }


def test_a_note_without_a_link_searches_with_its_text():
    search = FakeSearch()
    ctx, _ = _ctx({"id": "n", "kind": "note", "category": "tool"}, search=search)
    res = CODE["item.find_sources"](ctx, note={"body": "remember to look at the vectorlite sqlite thing"})
    assert search.queries == ["remember to look at the vectorlite sqlite thing"]
    assert _out(res)["status"] == "ok"


def test_a_blocked_term_in_the_query_refuses_the_search_before_it_leaves():
    search = FakeSearch()
    res, _ = _find(IMAGE, search=search, terms=["vectorlite"])
    assert search.queries == []
    assert _out(res)["status"] == "query_blocked" and "web query guard" in _out(res)["reason"]
    assert res.item is None


def test_the_failures_are_fixed_texts():
    res, _ = _find({**IMAGE, "description": None})
    assert _out(res)["status"] == "no_description"
    res, _ = _find({**IMAGE, "description": {"topic": "!!!", "visible_text": ""}})
    assert _out(res)["status"] == "nothing_to_search"
    res, _ = _find(IMAGE, search=FakeSearch([]))
    assert _out(res)["status"] == "no_results"
    res, _ = _find(IMAGE, search=FakeSearch([{"url": "ftp://x.example/a"}]))
    assert _out(res)["status"] == "no_results"


def test_a_failing_search_tool_is_search_failed_without_its_message():
    class Down(FakeSearch):
        def search(self, query: str, max_results: int = 5) -> dict[str, Any]:
            raise ConnectionError("secret-host.internal refused the connection")

    res, _ = _find(IMAGE, search=Down())
    assert _out(res)["status"] == "search_failed"
    assert "secret-host" not in str(_out(res))


def test_the_urls_must_pass_the_url_policy():
    search = FakeSearch([{"url": "http://127.0.0.1/admin"}, {"url": "https://ok.example/page"}])
    res, _ = _find(
        IMAGE, search=search, resolve=lambda h: ["127.0.0.1"] if h == "127.0.0.1" else ["93.184.216.34"]
    )
    assert res.item["source_url"] == "https://ok.example/page"


def test_neither_the_query_nor_a_result_text_reaches_a_span():
    tracer = RecordingTracer()
    secret = {"id": "i", "kind": "image", "category": "tool", "description": {"topic": "SECRETWORD thing"}}
    _find(secret, tracer=tracer, search=FakeSearch([{"url": "https://a.example/x", "title": "RESULTTITLE"}]))
    dump = repr([(s.name, s.attributes, s.events) for s in tracer.spans])
    assert "SECRETWORD" not in dump and "RESULTTITLE" not in dump
    [span] = [s for s in tracer.spans if s.name == "source.discover"]
    assert span.attributes["discovery.candidates"] == 1


def test_next_source_moves_on_and_runs_out():
    ctx, _ = _ctx({"source_url": "https://a/", "source_candidates": ["https://b/", "https://c/"]})
    res = CODE["item.next_source"](ctx)
    assert _out(res) == {"status": "ok", "remaining": 1}
    assert res.item == {"source_url": "https://b/", "source_candidates": ["https://c/"]}
    ctx, _ = _ctx({"source_url": "https://c/", "source_candidates": []})
    assert _out(CODE["item.next_source"](ctx)) == {"status": "exhausted"}


@pytest.mark.parametrize(
    ("discovery", "category", "item", "expected"),
    [
        (CONFIG, {"id": "tool", "subflow": "other_resource"}, {"kind": "image"}, True),
        (CONFIG, {"id": "tool", "subflow": "other_resource"}, {"kind": "note"}, True),
        (
            {**CONFIG, "from_images": False},
            {"id": "tool", "subflow": "other_resource"},
            {"kind": "image"},
            False,
        ),
        (
            {**CONFIG, "from_notes": False},
            {"id": "tool", "subflow": "other_resource"},
            {"kind": "text"},
            False,
        ),
        ({"enabled": False}, {"id": "tool", "subflow": "other_resource"}, {"kind": "image"}, False),
        (CONFIG, {"id": "business", "subflow": "other_resource"}, {"kind": "image"}, False),
        (
            {"enabled": True, "categories": ["learning-material"]},
            {"id": "learning-material", "subflow": "learning_material"},
            {},
            False,
        ),
        (CONFIG, None, {}, False),
    ],
)
def test_discovery_applies_only_where_the_config_says(discovery, category, item, expected):
    ctx, _ = _ctx(item, discovery=discovery)
    assert discovery_applies(ctx, category, item) is expected
