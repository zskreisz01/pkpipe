"""Spec 023 US1: the web UI as the primary decision surface (all fields, thumbnails, filters, bulk confirm,
override dropdown and new-category input, comment, image endpoint) on a real vault, behind a real login."""

from __future__ import annotations

import json
import re

import pytest

from pkpipe.domain.triage import item_id_for
from pkpipe.obsidian import lists
from tests.support.app_stack import OWNER_TOKEN, api_client, make_stack, seed_triage
from tests.support.images import jpeg_with_exif, solid

TRIAGE_FILE = "0-INBOX/Inbox-Triage.md"
IMAGE_NAME = "photo-1.png"


@pytest.fixture
def ui(stack):
    return login(stack)


def login(stack):
    client = api_client(stack, None)
    assert client.post("/login", data={"token": OWNER_TOKEN}).status_code == 303
    client.headers["X-CSRF-Token"] = stack.web.state.sessions.csrf(client.cookies["pkpipe_session"])
    client.headers["HX-Request"] = "true"
    return client


def rows(stack):
    return {r["item_id"]: r for r in lists.read_triage(stack.vault / TRIAGE_FILE)["rows"]}


def seed_image(stack, name=IMAGE_NAME, **row):
    (stack.vault / "0-INBOX/attachments").mkdir(parents=True, exist_ok=True)
    (stack.vault / "0-INBOX/attachments" / name).write_bytes(solid())
    stack.services.vault.update_triage(
        [
            {
                "resource": name,
                "ai_suggested_category": "none",
                "state": "suggested",
                "kind": "image",
                "needs_owner": True,
                "topic": "a red rectangle",
                **row,
            }
        ]
    )
    return item_id_for(name)


def test_the_page_shows_every_field_the_image_the_highlight_and_the_comment(stack, ui):
    ids = seed_triage(stack, a="tool")
    img = seed_image(stack)
    stack.services.comment_item(ids["a"], "keep | this [[wiki]] 😀", actor="ui")
    page = ui.get("/triage").text
    assert f'<img src="/ui/triage/{img}/image"' in page and 'data-testid="thumb"' in page
    assert re.search(rf'<tr id="row-{img}"[^>]*class="needs-owner"', page)
    assert 'data-testid="needs-owner"' in page and "a red rectangle" in page and ">image<" not in page
    assert "image" in page and "suggested" in page and "tool" in page
    assert "keep | this [[wiki]] 😀" in page  # the comment, escaped by the template
    assert f'hx-post="/ui/triage/{ids["a"]}/override"' in page and 'name="new_category"' in page
    assert 'name="category"' in page and "Confirm all suggested" in page
    # the highlight and the thumbnail belong to the image row only
    note_row = re.search(rf'<tr id="row-{ids["a"]}".*?</tr>', page, re.S).group(0)
    assert "needs-owner" not in note_row and "<img" not in note_row


def test_filters_by_state_category_and_needs_owner(stack, ui):
    ids = seed_triage(stack, a="tool", b="marketing", q="tool")
    img = seed_image(stack)
    stack.services.vault.update_triage([{"item_id": ids["q"], "state": "quarantined"}])

    def shown(query):
        page = ui.get(f"/triage?{query}").text
        return {i for i in (*ids.values(), img) if f'id="row-{i}"' in page}

    assert shown("") == {*ids.values(), img}
    assert shown("state=quarantined") == {ids["q"]}
    assert shown("category=marketing") == {ids["b"]}
    assert shown("category=none") == {img}
    assert shown("needs_owner=yes") == {img}
    assert shown("needs_owner=no") == {*ids.values()}
    assert shown("state=suggested&category=tool&needs_owner=no") == {ids["a"]}
    assert 'data-testid="row-count"' in ui.get("/triage?state=processed").text
    api = api_client(stack).get("/api/triage?needs_owner=true").json()["rows"]
    assert [r["item_id"] for r in api] == [img]


def test_bulk_confirm_acts_on_the_filtered_set_only_and_skips_what_needs_the_owner(stack, ui):
    ids = seed_triage(stack, a="tool", b="tool", c="marketing", d="tool")
    img = seed_image(stack)
    ui.post(f"/ui/triage/{ids['d']}/override", data={"category": "marketing"})  # the owner decided already
    resp = ui.post("/ui/triage/bulk-confirm", data={"category": "tool"})
    assert resp.status_code == 200 and resp.headers["HX-Redirect"].startswith("/triage?")
    assert "2+confirmed" in resp.headers["HX-Redirect"]
    state = rows(stack)
    assert state[ids["a"]]["confirm"] and state[ids["b"]]["confirm"]
    assert not state[ids["c"]]["confirm"]  # not in the filtered set
    assert not state[ids["d"]]["confirm"] and state[ids["d"]]["override"] == "marketing"
    assert not state[img]["confirm"]  # an image that needs the owner is never bulk confirmed
    audit = [a for a in stack.services.store.audit_log() if a["action"] == "bulk_confirm"]
    assert audit[0]["actor"] == "ui" and audit[0]["detail"]["confirmed"] == 2
    assert api_client(stack).post("/api/triage/bulk-confirm", json={}).json()["confirmed"] == 1  # c, now
    assert "Bulk confirm" in ui.get("/triage?bulk=2+confirmed").text


def test_override_by_dropdown_and_by_a_new_category_name_are_checked_server_side(stack, ui):
    ids = seed_triage(stack, a="tool", b="tool", c="tool")
    assert "overridden" in ui.post(f"/ui/triage/{ids['a']}/override", data={"category": "marketing"}).text
    assert rows(stack)[ids["a"]]["override"] == "marketing"
    new = ui.post(f"/ui/triage/{ids['b']}/override", data={"new_category": " Children Books "}).text
    assert "proposes a new category" in new and rows(stack)[ids["b"]]["override"] == "children-books"
    for bad in ("<script>alert(1)</script>", "x", "a" * 80, "![](http://evil)"):
        out = ui.post(f"/ui/triage/{ids['c']}/override", data={"new_category": bad}).text
        assert "unknown category" in out, bad
    assert rows(stack)[ids["c"]]["override"] is None
    assert "an override needs a category" in ui.post(f"/ui/triage/{ids['c']}/override", data={}).text


def test_an_awaiting_category_row_links_its_proposal_note_and_can_be_decided_again(stack, ui):
    ids = seed_triage(stack, a="tool")
    ui.post(f"/ui/triage/{ids['a']}/override", data={"new_category": "children"})
    stack.services.vault.update_triage([{"item_id": ids["a"], "state": "awaiting_category"}])
    page = ui.get("/triage").text
    note = f"2-Areas/Self-Improvement/System-Changes/new-category-children-{ids['a']}.md"
    assert 'data-testid="proposal-note"' in page and note in page
    assert 'class="primary"' not in re.search(rf'<tr id="row-{ids["a"]}".*?</tr>', page, re.S).group(0)
    assert api_client(stack).get("/api/triage").json()["rows"][0]["proposal_note"] == note
    assert "overridden" in ui.post(f"/ui/triage/{ids['a']}/override", data={"category": "marketing"}).text
    assert rows(stack)[ids["a"]]["override"] == "marketing"


def test_the_comment_round_trips_is_capped_and_audited_by_length_only(stack, ui):
    ids = seed_triage(stack, a="tool")
    odd = "line one\nline two | pipe [[link]] <b>x</b> ünï 😀\ttab"
    out = ui.post(f"/ui/triage/{ids['a']}/comment", data={"comment": odd}).text
    assert "comment saved" in out and "&lt;b&gt;x&lt;/b&gt;" in out and "<b>x</b>" not in out
    assert rows(stack)[ids["a"]]["comment"] == "line one line two | pipe [[link]] <b>x</b> ünï 😀 tab"
    ui.post(f"/ui/triage/{ids['a']}/comment", data={"comment": "x" * 3000})
    assert len(rows(stack)[ids["a"]]["comment"]) == 1000
    api = api_client(stack)
    assert api.post(f"/api/triage/{ids['a']}/comment", json={"comment": "via the API"}).status_code == 200
    assert rows(stack)[ids["a"]]["comment"] == "via the API"
    assert api.get("/api/triage").json()["rows"][0]["comment"] == "via the API"
    log = json.dumps(stack.services.store.audit_log())
    assert "via the API" not in log and "line one" not in log and '"chars": 10' in log
    assert api.post("/api/triage/ffffffffffff/comment", json={"comment": "x"}).status_code == 409
    # the pipeline's own tools still refuse to write it
    from pkpipe.obsidian.errors import InvalidRows

    with pytest.raises(InvalidRows, match="comment is the owner's"):
        stack.services.vault.update_triage([{"item_id": ids["a"], "comment": "from the pipeline"}])


def test_the_thumbnail_endpoint_is_authenticated_scoped_and_serves_bytes_only(stack, ui):
    ids = seed_triage(stack, a="tool")
    img = seed_image(stack)
    resp = ui.get(f"/ui/triage/{img}/image")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/jpeg"  # the prepared bytes
    assert resp.content[:3] == b"\xff\xd8\xff"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in resp.headers["content-security-policy"]
    assert api_client(stack, None).get(f"/ui/triage/{img}/image").status_code == 303  # needs the login
    assert api_client(stack).get(f"/api/triage/{img}/image").status_code == 200  # owner token
    assert api_client(stack, "x" * 30).get(f"/api/triage/{img}/image").status_code == 401
    assert ui.get(f"/ui/triage/{ids['a']}/image").status_code == 404  # a note row has no image
    assert ui.get("/ui/triage/ffffffffffff/image").status_code == 404


def test_the_thumbnail_drops_metadata_and_never_fetches_a_url(stack, ui):
    pytest.importorskip("PIL")
    from io import BytesIO

    from PIL import Image

    (stack.vault / "0-INBOX/attachments").mkdir(parents=True, exist_ok=True)
    (stack.vault / "0-INBOX/attachments" / "geo.jpg").write_bytes(jpeg_with_exif())
    stack.services.vault.update_triage(
        [
            {"resource": "geo.jpg", "ai_suggested_category": "none", "kind": "image"},
            {"resource": "https://example.invalid/x.png", "ai_suggested_category": "none", "kind": "image"},
        ]
    )
    got = ui.get(f"/ui/triage/{item_id_for('geo.jpg')}/image")
    assert got.status_code == 200
    exif = Image.open(BytesIO(got.content)).getexif()
    assert not dict(exif)  # EXIF with the invented GPS and device is gone
    assert ui.get(f"/ui/triage/{item_id_for('https://example.invalid/x.png')}/image").status_code == 404
    assert "<img" not in ui.get("/triage").text.split('id="row-' + item_id_for("https://example.invalid/x.png"))[1].split("</tr>")[0]


def test_a_missing_or_oversized_or_fake_image_is_a_404_not_a_crash(stack, ui):
    (stack.vault / "0-INBOX/attachments").mkdir(parents=True, exist_ok=True)
    (stack.vault / "0-INBOX/attachments" / "fake.png").write_bytes(b"<html>not an image</html>")
    stack.services.vault.update_triage(
        [
            {"resource": "fake.png", "ai_suggested_category": "none", "kind": "image"},
            {"resource": "gone.png", "ai_suggested_category": "none", "kind": "image"},
        ]
    )
    for name in ("fake.png", "gone.png"):
        assert ui.get(f"/ui/triage/{item_id_for(name)}/image").status_code == 404
    stack.services.vault.policy.max_image_bytes = 10
    (stack.vault / "0-INBOX/attachments" / "fake.png").write_bytes(solid())
    assert ui.get(f"/ui/triage/{item_id_for('fake.png')}/image").status_code == 404  # too large


def test_overrides_become_private_eval_candidates_with_the_comment_as_the_reason(stack, ui):
    ids = seed_triage(stack, a="tool", b="tool")
    ui.post(f"/ui/triage/{ids['a']}/override", data={"category": "marketing"})
    ui.post(f"/ui/triage/{ids['b']}/override", data={"category": "tool"})  # equals the suggestion: no case
    ui.post(f"/ui/triage/{ids['a']}/comment", data={"comment": "this is an ad idea, not a tool"})
    path = stack.overlay / "evals/candidates/triage.jsonl"
    cases = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    assert len(cases) == 1
    case = cases[0]
    assert case["id"] == f"override-{ids['a']}" and case["source"] == "owner_override"
    assert case["expected"] == {"category": "marketing"} and case["ai_suggested"] == "tool"
    assert case["reason"] == "this is an ad idea, not a tool" and case["input"]["resource"] == "0-INBOX/a.md"
    before = path.read_text(encoding="utf-8")
    stack.services.collect_override_candidates()  # idempotent: one line per item, the first day kept
    assert path.read_text(encoding="utf-8") == before
    assert stack.overlay.joinpath(".git").exists() and "evals/candidates" not in str(stack.pack)  # private only


def test_the_surface_banner_and_the_web_surface_page(tmp_path, synthetic_vault, pack_dir):
    web = make_stack(
        tmp_path,
        synthetic_vault,
        pack_dir,
        {
            "triage.json": '{"surface": "web"}\n',
            "ui.json": '{"public_url": "https://mini.tail1234.ts.net"}\n',
        },
    )
    assert web.services.vault.policy.triage_surface == "web"
    ids = seed_triage(web, a="tool")
    page = (web.vault / TRIAGE_FILE).read_text(encoding="utf-8")
    assert "https://mini.tail1234.ts.net/triage" in page and "|" not in page.replace("·", "")
    assert (web.vault / "0-INBOX/_pkpipe/triage-data.md").is_file()
    client = login(web)
    html = client.get("/triage").text
    assert ids["a"] in html and "Surface <strong>web</strong>" in html
    client.post(f"/ui/triage/{ids['a']}/confirm")
    assert "Waiting for your decision: 0" in (web.vault / TRIAGE_FILE).read_text(encoding="utf-8")
    assert lists.read_triage(web.vault / "0-INBOX/_pkpipe/triage-data.md")["rows"][0]["confirm"] is True


def test_a_switched_surface_shows_the_pending_move_and_never_an_empty_table(tmp_path, synthetic_vault, pack_dir):
    web = make_stack(
        tmp_path,
        synthetic_vault,
        pack_dir,
        {"triage.json": '{"surface": "web"}\n', "ui.json": '{"public_url": "https://mini.tail1234.ts.net"}\n'},
    )
    # the table is still in the page: the owner switched the config but no triage run moved it yet
    lists.update_triage(
        web.vault / TRIAGE_FILE, [{"resource": "0-INBOX/a.md", "ai_suggested_category": "tool"}]
    )
    html = login(web).get("/triage").text
    assert 'data-testid="migration-banner"' in html and "0-INBOX/a.md" in html
    assert "moves to" in html
