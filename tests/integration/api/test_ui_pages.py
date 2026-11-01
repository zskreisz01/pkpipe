"""Spec 014 US1/US2: the HTMX UI pages and fragments (server side), behind a real login session."""

from __future__ import annotations

import re

import pytest

from tests.support.app_stack import OWNER_TOKEN, api_client, git, seed_triage


@pytest.fixture
def ui(stack):
    client = api_client(stack, None)
    assert client.post("/login", data={"token": OWNER_TOKEN}).status_code == 303
    client.headers["X-CSRF-Token"] = stack.web.state.sessions.csrf(client.cookies["pkpipe_session"])
    client.headers["HX-Request"] = "true"
    return client


def test_pages_render_for_the_owner(ui):
    for path in ("/", "/triage", "/runs", "/config", "/audit"):
        resp = ui.get(path)
        assert resp.status_code == 200 and "<title>" in resp.text, path
        assert "/static/htmx.min.js" in resp.text
    assert 'hx-headers=\'{"X-CSRF-Token": "' in ui.get("/").text


def test_page_has_no_inline_script_and_loads_only_local_assets(ui):
    """CSP script-src 'self': no inline scripts, no external hosts."""
    html = ui.get("/config").text
    assert not re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert "http://" not in html.replace("http://127.0.0.1", "") and "https://" not in html


def test_triage_page_confirm_override_and_release_fragments(stack, ui):
    ids = seed_triage(stack, a="tool", b="marketing", q="tool")
    stack.services.vault.update_triage([{"item_id": ids["q"], "state": "quarantined"}])
    page = ui.get("/triage").text
    assert all(i in page for i in ids.values()) and "Release from quarantine" in page
    row = ui.post(f"/ui/triage/{ids['a']}/confirm").text
    assert 'data-testid="confirmed"' in row and f'id="row-{ids["a"]}"' in row
    row = ui.post(f"/ui/triage/{ids['b']}/override", data={"category": "tool"}).text
    assert "overridden" in row
    bad = ui.post(f"/ui/triage/{ids['b']}/override", data={"category": "bogus"}).text
    assert "unknown category" in bad and "err" in bad
    q = ui.post(f"/ui/triage/{ids['q']}/release").text
    assert "released from quarantine" in q and ">pending<" in q
    quarantined_confirm = ui.post(f"/ui/triage/{ids['q']}/confirm").text  # now pending: allowed
    assert 'data-testid="confirmed"' in quarantined_confirm


def test_untrusted_triage_text_is_html_escaped(stack, ui):
    """A hostile INBOX note name cannot inject markup into the owner's page."""
    stack.services.vault.update_triage(
        [{"resource": "0-INBOX/<img src=x onerror=alert(1)>.md", "ai_suggested_category": "tool"}]
    )
    page = ui.get("/triage").text
    assert "<img src=x" not in page and "&lt;img src=x onerror=alert(1)&gt;" in page


def test_state_changing_ui_endpoints_need_csrf(stack, ui):
    ids = seed_triage(stack, a="tool")
    del ui.headers["X-CSRF-Token"]
    assert ui.post(f"/ui/triage/{ids['a']}/confirm").status_code == 403
    assert ui.post("/ui/runs", data={"flow": "triage"}).status_code == 403


def test_runs_page_starts_a_flow(stack, ui):
    resp = ui.post("/ui/runs", data={"flow": "triage"})
    assert 'data-testid="runs-message"' in resp.text and "started triage" in resp.text
    run = stack.services.list_runs()[0]
    stack.services.launcher.wait(run["run_id"])
    detail = ui.get(f"/runs/{run['run_id']}")
    assert detail.status_code == 200 and "Finished steps" in detail.text and run["run_id"] in detail.text
    assert ui.get("/runs/not-a-run").status_code == 404
    again = ui.post("/ui/runs", data={"flow": "no_such"}).text
    assert "unknown flow" in again


def test_config_editor_draft_validate_smoke_publish_flow_through_the_ui(stack, ui):
    path = "instructions/triage/categorize.md"
    page = ui.get("/config/edit", params={"path": path}).text
    draft_id = re.search(r'name="draft_id" value="(\w+)"', page).group(1)
    rev = re.search(r'name="rev" value="(\d+)"', page).group(1)
    base = re.search(r'name="base_hash" value="(\w+)"', page).group(1)
    saved = ui.post(
        "/ui/config/save",
        data={
            "draft_id": draft_id,
            "path": path,
            "content": "Choose the best id.\n",
            "rev": rev,
            "base_hash": base,
        },
    ).text
    assert "saved to the draft" in saved and 'name="rev" value="1"' in saved
    stale = ui.post(
        "/ui/config/save",
        data={"draft_id": draft_id, "path": path, "content": "x", "rev": rev, "base_hash": base},
    ).text
    assert "changed elsewhere" in stale
    panel = ui.get("/config").text
    assert "Choose the best id." in panel and 'data-testid="diff"' in panel
    assert 'data-testid="validate-status">Valid' in ui.post(f"/ui/drafts/{draft_id}/validate").text
    assert 'data-testid="gate-status">passed' in ui.post(f"/ui/drafts/{draft_id}/smoke").text
    done = ui.post(
        f"/ui/drafts/{draft_id}/publish", data={"summary": "UI publish", "override_reason": ""}
    ).text
    assert 'data-testid="publish-status">Published' in done
    assert git(stack.overlay, "log", "-1", "--format=%s") == "config: UI publish"
    publish_id = stack.services.store.list_publishes()[0]["publish_id"]
    detail = ui.get(f"/publishes/{publish_id}").text
    assert "Choose the best id." in detail and "Roll back" in detail
    rb = ui.post(f"/ui/publishes/{publish_id}/rollback")
    assert rb.headers["hx-redirect"] == "/config"
    assert not (stack.overlay / path).exists()


def test_ui_category_form_adds_to_the_draft_and_reports_errors(stack, ui):
    page = ui.get("/config").text
    draft_id = re.search(r'name="draft_id" value="(\w+)"', page).group(1)
    form = {
        "draft_id": draft_id,
        "rev": "0",
        "id": "gardening",
        "label": "Gardening",
        "subflow": "other_resource",
        "para_path": "3-Resources/Gardening",
        "triage_hint": "Plants.",
    }
    ok = ui.post("/ui/config/category", data=form).text
    assert "category &#39;gardening&#39; added" in ok and "categories.json" in ok
    dup = ui.post("/ui/config/category", data={**form, "rev": "1"}).text
    assert "already exists" in dup
    stale = ui.post("/ui/config/category", data={**form, "id": "other", "rev": "0"}).text
    assert "changed elsewhere" in stale


def test_blocked_publish_is_explained_in_the_ui(stack, ui):
    from tests.support.app_stack import gate_model

    stack.set_gate_model(gate_model(triage_right=False))
    path = "instructions/triage/categorize.md"
    page = ui.get("/config/edit", params={"path": path}).text
    draft_id = re.search(r'name="draft_id" value="(\w+)"', page).group(1)
    ui.post(
        "/ui/config/save",
        data={"draft_id": draft_id, "path": path, "content": "misc\n", "rev": "0", "base_hash": "absent"},
    )
    out = ui.post(f"/ui/drafts/{draft_id}/publish", data={"summary": "bad", "override_reason": ""}).text
    assert 'data-testid="publish-status">Not published' in out and "triage: accuracy" in out
    assert not (stack.overlay / path).exists()


def test_discard_draft_redirects(stack, ui):
    page = ui.get("/config").text
    draft_id = re.search(r'name="draft_id" value="(\w+)"', page).group(1)
    resp = ui.post(f"/ui/drafts/{draft_id}/discard")
    assert resp.headers["hx-redirect"] == "/config"
    assert stack.services.store.get_draft(draft_id)["status"] == "discarded"


def test_read_only_config_when_no_overlay(stack):
    stack.services.workspace.overlay_dir = None
    client = api_client(stack, None)
    client.post("/login", data={"token": OWNER_TOKEN})
    page = client.get("/config").text
    assert "read-only" in page and "New category" not in page
    edit = client.get("/config/edit", params={"path": "categories.json"}).text
    assert "Read-only" in edit
