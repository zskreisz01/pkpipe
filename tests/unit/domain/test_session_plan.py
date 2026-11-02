"""Spec 010 FR-001..FR-003: session plan validation and the deterministic note rendering (pure code)."""

from __future__ import annotations

import copy

import pytest

from pkpipe.domain import session_plan as sp

SERVERS = {
    "websearch": {"allow": ["search"], "egress": True},
    "obsidian": {"allow": ["read_note"]},
}


def session(id, *, inputs=(), tools=(), context=None, schema="out", instruction="x/y.md", system="analyst"):
    s = {"id": id, "system": system, "instruction": instruction, "inputs": list(inputs), "tools": list(tools)}
    if context:
        s["context"] = context
    if schema:
        s["output_schema"] = schema
    return s


def category(*sessions, **extra):
    return {
        "id": "demo",
        "subflow": "other_resource",
        "para_path": "3-Resources/Demo",
        "sessions": list(sessions),
        **extra,
    }


def check(cat, texts=None):
    texts = texts or {}
    return sp.plan_errors(cat, servers=SERVERS, asset=lambda rel: texts.get(rel))


def test_valid_tool_like_plan_has_no_errors():
    cat = category(
        session("research", inputs=["item.extract"], tools=["websearch:search"]),
        session("compare", inputs=["research"], tools=["websearch:search"]),
        session("fit", inputs=["research", "compare"], context="personal"),
        session("synthesize", inputs=["*"]),
    )
    assert check(cat) == ([], [])
    info = sp.plan_info(cat)
    assert info.needs_fetch and not info.uses_text and info.note_session == "synthesize"
    assert info.sessions == ("research", "compare", "fit", "synthesize")


@pytest.mark.parametrize("raw", ["item.text", "item", "*", "item.body"])
def test_a_session_with_tools_never_reads_raw_text(raw):
    """Constitution IV: raw note text (and `*`, which would carry it) never reaches a session with tools."""
    errors, _ = check(category(session("research", inputs=[raw], tools=["websearch:search"])))
    assert any("raw untrusted input" in e and raw in e for e in errors), errors


def test_a_session_with_tools_reads_only_validated_session_output():
    cat = category(
        session("a", inputs=["item.extract"], schema=None),  # free text output: not validated JSON
        session("b", inputs=["a"], tools=["websearch:search"]),
    )
    errors, _ = check(cat)
    assert any("'a', which has no output_schema" in e for e in errors), errors
    cat["sessions"][0]["output_schema"] = "validated"
    assert check(cat)[0] == []


def test_enterprise_context_has_no_web_search_tools():
    """FR-002: a session with the enterprise context must not have an egress tool."""
    errors, _ = check(
        category(session("fit", inputs=["item.extract"], context="enterprise", tools=["websearch:search"]))
    )
    assert any("enterprise context must not have web search tools" in e for e in errors), errors
    # a tool on a local server is fine
    ok, _ = check(
        category(session("fit", inputs=["item.extract"], context="enterprise", tools=["obsidian:read_note"]))
    )
    assert ok == []


def test_enterprise_derived_data_never_reaches_a_web_search_session():
    """FR-002 transitively: the output of an enterprise session (or `*` after it) does not feed a web
    search."""
    cat = category(
        session("fit", inputs=["item.extract"], context="enterprise"),
        session("check", inputs=["fit"], tools=["websearch:search"]),
    )
    errors, _ = check(cat)
    assert any("enterprise-derived" in e and "'fit'" in e for e in errors), errors
    cat2 = category(
        session("fit", inputs=["item.extract"], context="enterprise"),
        session("mid", inputs=["fit"]),  # derived too
        session("check", inputs=["mid"], tools=["websearch:search"]),
    )
    assert any("enterprise-derived" in e for e in check(cat2)[0])


def test_personal_context_is_not_enterprise():
    ok, _ = check(
        category(
            session("fit", inputs=["item.extract"], context="personal"),
            session("check", inputs=["fit"], tools=["websearch:search"]),
        )
    )
    assert ok == []  # the query guard covers personal names; the hard rule is for the enterprise context


def test_unknown_forward_and_malformed_inputs_are_errors():
    cat = category(session("a", inputs=["b"]), session("b", inputs=["nope", "item.bad-ref", "item.extract"]))
    errors = check(cat)[0]
    assert any("input 'b'" in e for e in errors)  # a session cannot read a later one
    assert any("'nope'" in e for e in errors) and any("bad reference" in e for e in errors)


def test_instruction_budget_is_about_800_tokens():
    """FR-003: about 800 tokens (a quarter of the characters)."""
    cat = category(session("a", inputs=["item.extract"], instruction="a/long.md"))
    assert check(cat, {"instructions/a/long.md": "word " * 600})[0] == []  # 3000 chars
    errors = check(cat, {"instructions/a/long.md": "word " * 700})[0]  # 3500 chars: over
    assert any("FR-003" in e for e in errors), errors


def test_note_session_defaults_and_must_exist():
    cat = category(session("a", inputs=["item.extract"]), session("b", inputs=["a"]))
    assert sp.note_session_id(cat) == "b"  # no 'synthesize': the last session
    cat["sessions"].append(session("synthesize", inputs=["*"]))
    assert sp.note_session_id(cat) == "synthesize"
    cat["sessions"].append(session("late", inputs=["synthesize"]))  # an extension session after the note
    assert sp.note_session_id(cat) == "synthesize"
    cat["note_session"] = "a"
    assert sp.note_session_id(cat) == "a"
    cat["note_session"] = "ghost"
    assert any("note_session 'ghost'" in e for e in check(cat)[0])


def test_categories_that_do_not_run_the_plan_are_not_checked():
    """The learning category's session is descriptive (it has tools and no inputs or schema)."""
    learning = {
        "id": "learning-material",
        "subflow": "learning_material",
        "sessions": [session("n", tools=["websearch:search"], schema=None)],
    }
    assert check(learning) == ([], [])
    assert sp.plan_errors(learning, servers=SERVERS, asset=lambda r: None, force=True)[1] == []


def test_other_resource_category_without_sessions_is_a_warning():
    errors, warnings = check({"id": "x", "subflow": "other_resource", "sessions": []})
    assert errors == [] and any("without a session plan" in w for w in warnings)


def test_misc_style_plan_reads_the_note_text_and_needs_no_fetch():
    info = sp.plan_info(category(session("evaluate", inputs=["item.text"])))
    assert info.uses_text and not info.needs_fetch


def test_plan_errors_do_not_mutate_the_category():
    cat = category(session("a", inputs=["item.extract"]))
    before = copy.deepcopy(cat)
    check(cat)
    assert cat == before


# -- rendering ----------------------------------------------------------------------------------------------
def test_slugify_is_ascii_bounded_and_never_empty():
    assert sp.slugify("Héllo, Wörld! Tool v2") == "hello-world-tool-v2"
    assert sp.slugify("   ") == "note" and sp.slugify("../../etc/passwd") == "etc-passwd"
    assert len(sp.slugify("a" * 200)) == 60 and not sp.slugify("a" * 59 + "-b").endswith("-")


def test_note_path_is_inside_the_para_folder():
    assert sp.note_path("3-Resources/Tools/", "../../x", "abc123") == "3-Resources/Tools/x-abc123.md"


def test_render_note_body_sections_in_schema_order():
    out = {
        "title": "minikv",
        "summary": "An embeddable store.",
        "alternatives": ["badgerkv: also embeddable", ""],
        "fit_personal": "",
        "recommendation": "trial",
        "extra": {"a": "1", "b": ""},
        "flag": True,
        "nothing": None,
    }
    body = sp.render_note_body(out, source="https://example.org/minikv")
    assert body.startswith(
        "# minikv\n\nSource: https://example.org/minikv\n\n## Summary\n\nAn embeddable store."
    )
    assert "## Alternatives\n\n- badgerkv: also embeddable\n" in body
    assert body.index("## Summary") < body.index("## Alternatives") < body.index("## Recommendation")
    assert "Fit personal" not in body and "## Nothing" not in body and "flag" not in body.lower()
    assert "- **A:** 1" in body and "**B" not in body


def test_render_plain_note_and_text_fields_have_no_heading():
    assert sp.render_note_body({"title": "T", "note": "Body text."}) == "# T\n\nBody text.\n"
    assert sp.render_note_body({"text": "Free text.", "tool_calls": 2}) == "# Note\n\nFree text.\n"


def test_render_strips_control_characters_from_model_text():
    body = sp.render_note_body({"title": "T\x00itle", "note": "a\x07b\nline two"})
    assert "\x00" not in body and "\x07" not in body and "ab\nline two" in body


def test_suggestion_of_accepts_a_valid_new_category_only():
    known = {"tool", "misc"}
    ok = {"evaluate": {"suggestion": {"name": "Recipes", "triage_hint": " Cooking\nand baking. "}}}
    assert sp.suggestion_of(ok, known) == {
        "id": "recipes",
        "name": "Recipes",
        "triage_hint": "Cooking and baking.",
    }
    assert sp.suggestion_of({"e": {"suggestion": None}}, known) is None
    assert (
        sp.suggestion_of({"e": {"suggestion": {"name": "Tool", "triage_hint": "x"}}}, known) is None
    )  # exists
    assert (
        sp.suggestion_of({"e": {"suggestion": {"name": "A", "triage_hint": "x"}}}, known) is None
    )  # too short
    assert sp.suggestion_of({"e": {"suggestion": {"name": "Recipes", "triage_hint": ""}}}, known) is None
    assert sp.suggestion_of({"e": "text", "f": {"note": "x"}}, known) is None


def test_agent_task_note_is_an_open_task_in_the_agent_tasks_folder():
    path, body, front = sp.agent_task_note(
        {"id": "recipes", "name": "Recipes", "triage_hint": "Cooking."},
        item_id="abc",
        note="3-Resources/Misc/n-abc.md",
    )
    assert path == "2-Areas/Agent-Tasks/new-category-recipes-abc.md"
    assert "- [ ] Decide whether to add the category `recipes`" in body and "Triage hint: Cooking." in body
    assert front["type"] == "agent_task" and front["status"] == "proposed" and front["pkpipe_item"] == "abc"
