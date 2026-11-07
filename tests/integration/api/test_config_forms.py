"""Spec 026: the config forms in the UI: schema-driven fields, row actions, per-field errors, secrets, the
markdown hint and the shared draft with the JSON view. Real login session, real overlay git repository."""

from __future__ import annotations

import json
import re

import pytest

from tests.support.app_stack import OWNER_TOKEN, api_client

INSTRUCTION = "instructions/triage/categorize.md"


@pytest.fixture
def ui(stack):
    client = api_client(stack, None)
    assert client.post("/login", data={"token": OWNER_TOKEN}).status_code == 303
    client.headers["X-CSRF-Token"] = stack.web.state.sessions.csrf(client.cookies["pkpipe_session"])
    client.headers["HX-Request"] = "true"
    return client


def form_fields(html: str) -> dict[str, str]:
    """Every named input of the rendered form with its current value (what a browser would post)."""
    out: dict[str, str] = {}
    for m in re.finditer(r'<input[^>]*name="([^"]+)"[^>]*>', html):
        tag = m.group(0)
        if 'type="hidden"' in tag or 'name="f.' in tag:
            value = re.search(r'value="([^"]*)"', tag)
            out[m.group(1)] = value.group(1) if value else ""
    for m in re.finditer(r'<textarea[^>]*name="([^"]+)"[^>]*>(.*?)</textarea>', html, re.S):
        out[m.group(1)] = m.group(2)
    for m in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        sel = re.search(r'<option value="([^"]*)"\s+selected', m.group(2))
        out[m.group(1)] = sel.group(1) if sel else ""
    return {
        k: v.replace("&#34;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        for k, v in out.items()
    }


def open_form(ui, path):
    page = ui.get("/config/form", params={"path": path})
    assert page.status_code == 200, page.text[:300]
    return page.text, form_fields(page.text)


def test_the_config_page_links_every_config_section_to_its_form_and_json_view(ui):
    page = ui.get("/config").text
    assert page.count('data-testid="form-link"') >= 10
    assert "/config/form?path=categories.json" in page and "/config/form?path=schedules.json" in page
    assert "/config/form?path=flows" not in page  # flows stay JSON
    edit = ui.get("/config/edit", params={"path": "categories.json"}).text
    assert "/config/form?path=categories.json" in edit


def test_a_non_section_path_goes_to_the_text_editor(ui):
    resp = ui.get("/config/form", params={"path": INSTRUCTION}, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"].startswith("/config/edit")


def test_saving_a_form_edits_the_shared_draft_that_the_json_view_shows(stack, ui):
    html, fields = open_form(ui, "schedules.json")
    assert fields["f.0.cron"] and 'data-testid="pack-note"' in html
    fields["f.0.cron"] = "15 7 * * *"
    resp = ui.post("/ui/config/form/save", data=fields)
    assert resp.status_code == 200 and "saved to the draft" in resp.text
    raw = ui.get("/config/edit", params={"path": "schedules.json"}).text
    assert "15 7 * * *" in raw  # the JSON tab reads the same draft
    draft = stack.services.open_draft()
    saved = json.loads(draft["files"]["schedules.json"]["content"])
    assert [e["cron"] for e in saved] == ["15 7 * * *"]  # a pack file: only the changed entry is written


def test_a_pack_section_is_not_copied_into_the_overlay_when_nothing_changed(ui):
    _, fields = open_form(ui, "schedules.json")
    resp = ui.post("/ui/config/form/save", data=fields)
    assert "nothing changed" in resp.text


def test_a_field_error_is_shown_at_the_field_and_nothing_is_saved(stack, ui):
    _, fields = open_form(ui, "images.json")
    fields["f.max_side"] = "wide"
    resp = ui.post("/ui/config/form/save", data=fields)
    assert 'data-testid="field-error"' in resp.text and "a whole number is required" in resp.text
    assert "images.json" not in stack.services.open_draft()["files"]


def test_rows_are_added_removed_and_moved_with_the_values_typed_so_far(ui):
    _, fields = open_form(ui, "schedules.json")
    n = len([k for k in fields if re.fullmatch(r"f\.\d+\.flow", k)])
    fields["f.0.flow"] = "typed-but-not-saved"
    added = form_fields(ui.post("/ui/config/form/action", data={**fields, "act": "add:f"}).text)
    assert added["f.0.flow"] == "typed-but-not-saved" and f"f.{n}.cron" in added
    added[f"f.{n}.cron"], added[f"f.{n}.flow"] = "0 3 * * *", "triage"
    moved = form_fields(ui.post("/ui/config/form/action", data={**added, "act": f"up:f:{n}"}).text)
    assert moved[f"f.{n - 1}.flow"] == "triage" and moved[f"f.{n}.flow"] != "triage"
    gone = form_fields(ui.post("/ui/config/form/action", data={**moved, "act": f"del:f:{n - 1}"}).text)
    assert f"f.{n}.cron" not in gone and gone["f.0.flow"] == "typed-but-not-saved"


def test_a_bad_action_is_refused_not_executed(ui):
    _, fields = open_form(ui, "schedules.json")
    resp = ui.post("/ui/config/form/action", data={**fields, "act": "add:../../../etc"})
    assert resp.status_code == 409 or resp.status_code == 422


def test_a_session_with_its_instruction_link_is_part_of_the_categories_form(ui):
    html, fields = open_form(ui, "categories.json")
    assert any(re.fullmatch(r"f\.\d+\.sessions\.\d+\.instruction", k) for k in fields)
    assert "/config/edit?path=instructions/" in html  # the text of a session is one click away
    assert 'aria-label="move' in html and 'aria-label="remove' in html


def test_new_category_with_sessions_through_the_form_publishes_a_valid_draft(stack, ui):
    _, fields = open_form(ui, "categories.json")
    n = len([k for k in fields if re.fullmatch(r"f\.\d+\.id", k)])
    fields = form_fields(ui.post("/ui/config/form/action", data={**fields, "act": "add:f"}).text)
    fields.update(
        {
            f"f.{n}.id": "evaluations",
            f"f.{n}.label": "Evaluations",
            f"f.{n}.subflow": "process_other_resource",
            f"f.{n}.para_path": "{resources_dir}/Evaluations",
            f"f.{n}.triage_hint": "things to evaluate",
        }
    )
    resp = ui.post("/ui/config/form/save", data=fields)
    assert "saved to the draft" in resp.text, resp.text[-600:]
    saved = json.loads(stack.services.open_draft()["files"]["categories.json"]["content"])
    assert saved[-1]["id"] == "evaluations" and len(saved) == 1  # pack entries are not copied


def test_labels_and_values_are_escaped_everywhere(stack, ui):
    _, fields = open_form(ui, "categories.json")
    fields["f.0.label"] = '"><script>alert(1)</script><img src=x onerror=alert(2)>'
    fields["f.0.triage_hint"] = "<b>bold</b>"
    fields["f.0.flows_bogus"] = "x"
    ui.post("/ui/config/form/save", data=fields)
    page = ui.get("/config/form", params={"path": "categories.json"}).text
    assert "<script>alert(1)" not in page and "<img src=x" not in page and "&lt;script&gt;" in page
    assert "<b>bold</b>" not in page
    assert not re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", page)


def test_a_secret_is_only_accepted_as_an_env_reference_in_both_views(stack, ui):
    _, fields = open_form(ui, "channels.json")
    fields = form_fields(ui.post("/ui/config/form/action", data={**fields, "act": "add:f.channels"}).text)
    name = [k for k in fields if k.startswith("f.channels.") and k.endswith(".__k")][-1][: -len(".__k")]
    fields.update({f"{name}.__k": "discord_extra", f"{name}.type": "discord_webhook"})
    fields[f"{name}.url"] = "https://discord.com/api/webhooks/1/SECRETSECRET"
    resp = ui.post("/ui/config/form/save", data=fields)
    assert "never the secret itself" in resp.text
    assert "channels.json" not in stack.services.open_draft()["files"]
    fields[f"{name}.url"] = "env:DISCORD_EXTRA_URL"
    again = ui.post("/ui/config/form/save", data=fields).text
    assert "saved to the draft" in again, re.findall(
        r'data-testid="(?:field|form)-error"[^>]*>([^<]*)', again
    )
    # the JSON view refuses a literal secret too
    draft = stack.services.open_draft()
    bad = json.dumps({"channels": {"x": {"type": "discord_webhook", "url": "https://hooks.example/LITERAL"}}})
    resp = ui.post(
        "/ui/config/save",
        data={
            "draft_id": draft["draft_id"],
            "path": "channels.json",
            "content": bad,
            "rev": draft["rev"],
            "base_hash": draft["files"]["channels.json"]["base_hash"],
        },
    )
    assert "never the secret itself" in resp.text


def test_secret_fields_show_whether_the_variable_is_set_never_its_value(stack, ui, monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://hooks.example/REAL-VALUE")
    page = ui.get("/config/form", params={"path": "channels.json"}).text
    assert "REAL-VALUE" not in page


def test_the_cron_preview_names_the_next_runs_and_the_reason_for_a_bad_expression(ui):
    ok = ui.get("/ui/config/cron-preview", params={"f.0.cron": "0 6 * * *"}).text
    assert "next:" in ok and "06:00" in ok
    bad = ui.get("/ui/config/cron-preview", params={"f.0.cron": "not a cron"}).text
    assert 'class="err"' in bad


def test_the_instruction_editor_shows_the_token_budget(ui):
    page = ui.get("/config/edit", params={"path": INSTRUCTION}).text
    assert 'data-testid="token-hint"' in page and 'data-token-budget="800"' in page
    assert "/static/config-forms.js" in page


def test_form_endpoints_need_csrf_and_the_owner(stack, ui):
    _, fields = open_form(ui, "schedules.json")
    del ui.headers["X-CSRF-Token"]
    assert ui.post("/ui/config/form/save", data=fields).status_code == 403
    assert ui.post("/ui/config/form/action", data={**fields, "act": "add:f"}).status_code == 403
    anonymous = api_client(stack, None)
    assert anonymous.get(
        "/config/form", params={"path": "schedules.json"}, follow_redirects=False
    ).status_code in (
        303,
        401,
    )


def test_broken_json_in_the_draft_is_reported_with_the_way_out(stack, ui):
    draft = stack.services.open_draft()
    stack.services.edit_file(draft["draft_id"], "ui.json", "{not json", base_hash=None, rev=draft["rev"])
    resp = ui.get("/config/form", params={"path": "ui.json"})
    assert resp.status_code == 422 and "fix it in the JSON view" in resp.text


def test_new_config_sections_appear_in_the_listing_and_get_a_form(ui):
    for stem in (
        "triage",
        "ui",
        "report",
        "images",
        "layout",
        "security",
        "channels",
        "schedules",
        "categories",
    ):
        page = ui.get("/config/form", params={"path": f"{stem}.json"})
        assert page.status_code == 200 and 'id="cfg-form"' in page.text, stem


def test_selectors_are_safe_and_pickers_are_wired(ui):
    html, _ = open_form(ui, "schedules.json")
    targets = re.findall(r'hx-target="#([^"]+)"', html)
    assert targets and all("." not in t for t in targets)  # a dot would be read as a CSS class
    assert (
        'list="dl-flows"' in html and '<datalist id="dl-flows">' in html and '<option value="triage">' in html
    )
    assert 'aria-describedby=""' not in html
    _, fields = open_form(ui, "images.json")
    fields["f.max_side"] = "wide"
    page = ui.post("/ui/config/form/save", data=fields).text
    assert 'aria-invalid="true"' in page and 'aria-describedby="e-f.max_side"' in page
