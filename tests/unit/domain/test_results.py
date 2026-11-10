"""Spec 032: learning notes hold only our own facts, image notes come from the description, the index lists
every pkpipe note of a folder."""

from __future__ import annotations

from pkpipe.domain import results


def learning(**over):
    base = dict(
        item_id="a5ebe3d627ed",
        category="learning-material",
        para_path="4-Resources/Learning",
        source="https://bbycroft.net/llm?utm_source=x#frag",
        notebook_url="https://notebook.google.com/notebook/abc",
        audio_file="/data/audio/x/ep.m4a",
        audio_note=None,
        ui_url="http://100.1.2.3:8787/",
        created="2026-11-10",
    )
    return results.learning_note(**{**base, **over})


def test_a_learning_note_names_the_source_notebook_podcast_and_history_only():
    n = learning()
    assert n["path"] == "4-Resources/Learning/learning-bbycroft-net-llm-a5ebe3d627ed.md"
    assert n["front_matter"]["type"] == "learning_note" and n["front_matter"]["pkpipe_item"] == "a5ebe3d627ed"
    assert n["front_matter"]["notebook_url"].startswith("https://notebook.google.com/notebook/")
    body = n["body"]
    assert "Notebook: https://notebook.google.com/notebook/abc" in body
    assert (
        "Podcast: `/data/audio/x/ep.m4a`" in body
        and "History: http://100.1.2.3:8787/items/a5ebe3d627ed" in body
    )
    assert "utm_source" in n["front_matter"]["source"]  # the source is kept as the item had it


def test_a_copied_podcast_is_embedded_and_a_missing_one_is_said():
    assert "![[a5ebe3d627ed-ep.m4a]]" in learning(audio_note="a5ebe3d627ed-ep.m4a")["body"]
    none = learning(audio_file=None, ui_url=None, source="")
    assert "Podcast: not available" in none["body"] and "History" not in none["body"]
    assert none["path"].endswith("learning-note-a5ebe3d627ed.md")


def test_audio_names_are_plain_and_unique_per_item():
    assert results.audio_vault_name("abc", "Episode One.m4a") == "abc-episode-one.m4a"
    assert results.audio_vault_name("abc", "noext") == "abc-noext.m4a"


DESC = {
    "topic": "A screenshot of the Vectorlite tool.",
    "visible_text": "vector search\nfor SQLite",
    "urls": ["https://x.example"],
    "language": "en",
    "kind": "screenshot",
}


def test_an_image_note_holds_what_the_image_shows():
    n = results.image_note(
        item_id="1c797f38b5c7",
        category="tool",
        para_path="4-Resources/Tools",
        resource="shot.jpg",
        description=DESC,
        created="2026-11-10",
    )
    assert n["path"] == "4-Resources/Tools/a-screenshot-of-the-vectorlite-tool-1c797f38b5c7.md"
    assert (
        "> vector search" in n["body"] and "> for SQLite" in n["body"] and "- https://x.example" in n["body"]
    )
    assert n["front_matter"]["origin"] == "image_description" and n["front_matter"]["source"] == ""
    assert "release the item again" in n["body"]


def test_an_empty_description_makes_no_note():
    assert (
        results.image_note(
            item_id="x",
            category="tool",
            para_path="p",
            resource="r",
            description={"topic": " ", "visible_text": ""},
            created="d",
        )
        is None
    )


def test_the_index_row_reads_pkpipe_notes_only():
    front = {
        "type": "resource_note",
        "pkpipe_item": "abc",
        "category": "tool",
        "source": "https://x.example/a",
    }
    row = results.index_row("tool-abc.md", front, "# Tool\n\n## Recommendation\n\nadopt\n\n## More\n", 0.0)
    assert row and row["note"] == "adopt" and row["title"] == "Tool" and row["date"] == "1970-01-01"
    assert results.index_row("x.md", {"type": "journal"}, "", 0.0) is None
    assert results.index_row("x.md", {"type": "resource_note"}, "", 0.0) is None  # no item id
    nb = results.index_row(
        "l.md", {"type": "learning_note", "pkpipe_item": "i", "notebook_url": "u"}, "# L", 0.0
    )
    assert nb and nb["note"] == "podcast + notebook"


def test_the_index_is_a_newest_first_table_with_safe_cells():
    rows = [
        dict(
            date="2026-11-08",
            stem="a-1",
            title="Old | one",
            category="tool",
            source="https://x.example/a",
            note="adopt",
            notebook="",
            item="1",
        ),
        dict(
            date="2026-11-10",
            stem="b-2",
            title="New",
            category="tool",
            source="",
            note="from the image only",
            notebook="",
            item="2",
        ),
    ]
    text = results.render_index("4-Resources/Tools", rows)
    assert text.index("b-2") < text.index("a-1") and "# Index of Tools" in text
    assert "Old \\| one" in text and "x.example/a" in text and "| image |" in text
