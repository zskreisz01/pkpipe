"""Constitution X, spec 004 follow-up: vault operations emit tool spans without note content."""

from __future__ import annotations

import pytest

from pkpipe.obsidian.errors import NoteNotFound
from pkpipe.obsidian.models import item_id_for
from pkpipe.obsidian.policy import VaultPolicy
from pkpipe.obsidian.vault import Vault
from pkpipe.telemetry import ContentPolicy, flow_span

CANARY = "NOTE-CANARY-91c2d7"


@pytest.fixture
def vault(synthetic_vault) -> Vault:
    return Vault(VaultPolicy.load(None, synthetic_vault))


def _all_values(span):
    return [str(v) for v in span.attributes.values()] + [
        str(v) for e in span.events for v in e.attributes.values()
    ]


def test_each_operation_emits_a_tool_span_named_after_the_tool(otel, vault):
    vault.write_note("3-Resources/Tools/t.md", f"{CANARY} body", {"k": CANARY})
    vault.read_note("3-Resources/Tools/t.md")
    vault.append_note("3-Resources/Tools/t.md", CANARY)
    vault.move_note("3-Resources/Tools/t.md", "3-Resources/Tools/u.md")
    vault.list_folder("3-Resources/Tools")
    vault.read_triage()
    names = [s.name for s in otel.spans("tool ")]
    assert names == [
        f"tool {n}"
        for n in ("write_note", "read_note", "append_note", "move_note", "list_folder", "read_triage")
    ]
    for s in otel.spans("tool "):
        assert s.attributes["tool.name"] == s.name.removeprefix("tool ")
        assert s.attributes["tool.server"] == "obsidian-vault"


def test_content_is_not_recorded_by_default(otel, vault):
    vault.write_note("3-Resources/Tools/t.md", f"{CANARY} body", {"k": CANARY})
    vault.append_note("3-Resources/Tools/t.md", CANARY)
    vault.read_note("3-Resources/Tools/t.md")
    vault.update_triage(
        [{"item_id": item_id_for(CANARY), "resource": CANARY, "ai_suggested_category": "tool"}]
    )
    for s in otel.spans():
        assert not any(CANARY in v for v in _all_values(s)), s.name


def test_content_is_not_recorded_even_when_flow_capture_is_allowed(otel_factory, vault):
    o = otel_factory(ContentPolicy(frozenset({"flow"})))
    with flow_span("learning", trigger="manual", config_version="v", data_classes=()):
        vault.write_note("3-Resources/Tools/t.md", CANARY)
    for s in o.spans():
        assert not any(CANARY in v for v in _all_values(s))


def test_single_row_upsert_records_item_id_and_row_count(otel, vault):
    iid = item_id_for("https://e.invalid")
    vault.update_triage([{"item_id": iid, "resource": "https://e.invalid", "ai_suggested_category": "tool"}])
    span = otel.spans("tool update_triage")[0]
    assert span.attributes["item.id"] == iid and span.attributes["pkpipe.vault.rows"] == 1
    vault.upsert_processing_list(
        [
            {"item_id": "a", "category": "tool", "obsidian_link": "[[x]]"},
            {"item_id": "b", "category": "tool", "obsidian_link": "[[y]]"},
        ]
    )
    span = otel.spans("tool upsert_processing_list")[0]
    assert span.attributes["pkpipe.vault.rows"] == 2 and "item.id" not in span.attributes


def test_failing_operation_marks_span_error_with_type_only(otel, vault):
    with pytest.raises(NoteNotFound):
        vault.read_note("3-Resources/Tools/missing-" + CANARY + ".md")
    span = otel.spans("tool read_note")[0]
    assert span.status.status_code.name == "ERROR"
    assert span.events[0].attributes["exception.type"] == "NoteNotFound"
    assert not any(CANARY in v for v in _all_values(span))


def test_tool_span_nests_under_flow_with_flow_attributes(otel, vault):
    with flow_span("inbox", trigger="manual", config_version="v9"):
        vault.read_triage()
    span = otel.spans("tool read_triage")[0]
    assert span.attributes["flow.id"] == "inbox" and span.attributes["config.version"] == "v9"
