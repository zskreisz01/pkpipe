"""Spec 005 T001/T003: flow JSON schema, references, templates and conditions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pkpipe.engine.expr import MISSING, ReferenceError_, evaluate, lookup, references_in, render
from pkpipe.engine.flow_models import Condition, Flow, FlowsConfig


def _flow(**kw):
    base = {"id": "f", "start": "a", "nodes": [{"id": "a", "type": "code"}], "edges": []}
    base.update(kw)
    return base


def test_minimal_flow_is_valid_and_defaults_parallelism_to_two():
    """FR-003: fan-out parallelism defaults to 2."""
    flow = Flow.model_validate(_flow())
    assert flow.max_parallel == 2 and flow.node("a").type == "code"


@pytest.mark.parametrize(
    "change, message",
    [
        ({"nodes": [{"id": "a", "type": "code"}, {"id": "a", "type": "tool"}]}, "duplicate node id"),
        ({"edges": [{"from": "a", "to": "nowhere"}]}, "unknown node 'nowhere'"),
        ({"edges": [{"from": "ghost", "to": "a"}]}, "unknown node 'ghost'"),
        ({"start": None}, "set 'start'"),
        ({"start": "zzz"}, "start node 'zzz'"),
        ({"edges": [{"from": "a", "to": "END", "default": True}]}, "without any 'when'"),
        ({"nodes": [{"id": "item", "type": "code"}], "start": "item"}, "reserved"),
        ({"nodes": [{"id": "Bad-Id", "type": "code"}], "start": "Bad-Id"}, "must match"),
        ({"max_parallel": 0}, "greater than or equal to 1"),
    ],
)
def test_structural_errors_name_the_problem(change, message):
    with pytest.raises(ValidationError, match=message):
        Flow.model_validate(_flow(**change))


def test_start_and_start_edges_are_exclusive():
    with pytest.raises(ValidationError, match="not both"):
        Flow.model_validate(_flow(edges=[{"from": "START", "to": "a"}]))


def test_two_default_edges_rejected():
    nodes = [{"id": "a", "type": "code"}, {"id": "b", "type": "code"}]
    edges = [
        {"from": "a", "to": "b", "when": {"path": "a.x", "eq": 1}},
        {"from": "a", "to": "END", "default": True},
        {"from": "a", "to": "b", "default": True},
    ]
    with pytest.raises(ValidationError, match="more than one default"):
        Flow.model_validate(_flow(nodes=nodes, edges=edges))


def test_error_edges_take_no_condition():
    with pytest.raises(ValidationError, match="error edges"):
        Flow.model_validate(
            _flow(edges=[{"from": "a", "to": "END", "on": "error", "when": {"path": "a", "exists": True}}])
        )


def test_on_item_error_requires_for_each():
    with pytest.raises(ValidationError, match="for_each"):
        Flow.model_validate(_flow(nodes=[{"id": "a", "type": "code", "on_item_error": "continue"}]))


def test_flow_id_must_equal_file_name():
    with pytest.raises(ValidationError, match="must equal the file name"):
        FlowsConfig.model_validate({"other": _flow()})


@pytest.mark.parametrize(
    "cond",
    [
        {"path": "a"},
        {"path": "a", "eq": 1, "ne": 2},
        {"eq": 1},
        {"all": [], "path": "a"},
        {"all": [], "any": []},
    ],
)
def test_condition_needs_exactly_one_form(cond):
    with pytest.raises(ValidationError):
        Condition.model_validate(cond)


STATE = {"item": {"category": "tool", "score": 0.7, "tags": ["a", "b"]}, "n": 0, "flag": False}


def _r(ref):
    return lookup(STATE, ref)


@pytest.mark.parametrize(
    "cond, expected",
    [
        ({"path": "item.category", "eq": "tool"}, True),
        ({"path": "item.category", "eq": "misc"}, False),
        ({"path": "item.category", "in": ["misc", "tool"]}, True),
        ({"path": "item.category", "not_in": ["tool"]}, False),
        ({"path": "item.missing", "ne": "x"}, True),
        ({"path": "item.missing", "eq": None}, False),
        ({"path": "item.missing", "exists": False}, True),
        ({"path": "item.score", "gte": 0.7}, True),
        ({"path": "item.score", "gt": 0.7}, False),
        ({"path": "item.category", "gt": 1}, False),
        ({"path": "flag", "truthy": False}, True),
        ({"path": "n", "truthy": True}, False),
        ({"path": "item.tags.1", "eq": "b"}, True),
        ({"all": [{"path": "item.category", "eq": "tool"}, {"path": "item.score", "lt": 0.5}]}, False),
        ({"any": [{"path": "item.category", "eq": "misc"}, {"path": "item.score", "lt": 0.8}]}, True),
        ({"not": {"path": "item.category", "eq": "tool"}}, False),
    ],
)
def test_conditions_are_deterministic_predicates(cond, expected):
    """Constitution V: routing is code, evaluated on state values."""
    assert evaluate(Condition.model_validate(cond), _r) is expected


def test_render_keeps_whole_references_typed_and_fills_strings():
    assert render("${item.tags}", _r) == ["a", "b"]
    assert render({"p": "x/${item.category}.md", "n": ["${item.score}"]}, _r) == {
        "p": "x/tool.md",
        "n": [0.7],
    }
    assert render("tags=${item.tags}", _r) == 'tags=["a", "b"]'
    with pytest.raises(ReferenceError_, match="item.nope"):
        render("${item.nope}", _r)


def test_lookup_and_reference_listing():
    assert lookup(STATE, "item.tags.-1") == "b"
    assert lookup(STATE, "item.tags.5") is MISSING
    assert lookup(STATE, "item.category.x") is MISSING
    assert references_in({"a": "${x.y} and ${z}", "b": ["${w}"]}) == ["x.y", "z", "w"]
