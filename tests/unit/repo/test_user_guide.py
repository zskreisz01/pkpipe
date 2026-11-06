"""Spec 023 US5: the English user guide is part of the site, covers every triage column and the surfaces, and
contains no private data."""

from __future__ import annotations

import re

from pkpipe.domain.triage import ItemState
from pkpipe.obsidian.lists import TRIAGE_COLUMNS, TRIAGE_OPTIONAL
from tests.conftest import REPO

GUIDE = (REPO / "docs/user-guide.md").read_text(encoding="utf-8")


def test_the_guide_has_front_matter_and_is_linked_from_the_docs_index():
    assert GUIDE.startswith("---\ntitle: User guide\n---\n")
    assert "(user-guide.md)" in (REPO / "docs/index.md").read_text(encoding="utf-8")


def test_every_triage_column_and_state_is_explained():
    for column in (*TRIAGE_COLUMNS, *TRIAGE_OPTIONAL):
        assert re.search(rf"^\| `{column}` \|", GUIDE, re.MULTILINE), column
    for state in ItemState:
        assert re.search(rf"^\| `{state.value}` \|", GUIDE, re.MULTILINE), state.value


def test_the_guide_covers_the_topics_of_the_brief():
    for needle in (
        "20:00",
        "06:00",
        "needs_owner",
        "two-hour budget",
        "New categories",
        "owner_note",
        "triage.surface",
        "pkpipe restore",
        "tailscale serve",
        "never use Tailscale Funnel".lower(),
    ):
        assert needle.lower() in GUIDE.lower(), needle


def test_the_guide_and_the_new_examples_hold_no_private_data():
    texts = [GUIDE, (REPO / "deploy/launchd/io.pkpipe.serve-tailscale-ip.plist.example").read_text()]
    for text in texts:
        assert not re.search(r"100\.(11[0-9]|1[2-9]\d)\.\d+\.\d+", text) or "100.64.0." in text
        assert "100.113." not in text and "/Users/" not in text and "zsolt" not in text.lower()
