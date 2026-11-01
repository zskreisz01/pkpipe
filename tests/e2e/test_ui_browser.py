"""Spec 014 SC-001 / US1 / US2: the config UI in a real browser (Playwright Chromium) against a real uvicorn
server on a synthetic vault and a temp overlay git repository.

Walks the owner's path: log in, confirm and override triage items, create a new category, validate, smoke
eval, publish (git commit), see the new category in the triage form, roll the publish back, start a run.
"""

from __future__ import annotations

import json

import pytest

from pkpipe.obsidian import lists
from tests.support.app_stack import OWNER_TOKEN, git, running, seed_triage

pytestmark = pytest.mark.live("browser")


@pytest.fixture
def browser_page(stack):
    from playwright.sync_api import sync_playwright

    with running(stack) as srv, sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(15000)
        problems: list[str] = []
        page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: problems.append(str(e)))
        yield page, srv.url, problems, context
        browser.close()
    assert problems == [], f"browser console problems (CSP violations show up here): {problems}"


def login(page, url, token=OWNER_TOKEN):
    page.goto(url + "/")
    assert page.url.endswith("/login")
    page.fill("#token", token)
    page.click("button[type=submit]")


def test_login_rejects_a_wrong_token_and_accepts_the_owner_token(browser_page):
    page, url, problems, _ = browser_page
    login(page, url, "not-the-token")
    assert page.locator("#login-error").inner_text() == "Invalid token."
    # a failed attempt logs a 401 resource error in the console; that is expected here
    problems.clear()
    page.fill("#token", OWNER_TOKEN)
    page.click("button[type=submit]")
    page.wait_for_selector("text=Overview")
    assert page.url == url + "/"


def test_owner_confirms_and_overrides_triage_items_in_the_browser(stack, browser_page):
    """US2 via the UI: the buttons write the Obsidian triage file."""
    ids = seed_triage(stack, a="tool", b="marketing")
    page, url, _, _ = browser_page
    login(page, url)
    page.goto(url + "/triage")
    assert page.locator('[data-testid="pending-count"]').count() == 0
    row_a = page.locator(f"#row-{ids['a']}")
    row_a.get_by_role("button", name="Confirm").click()
    page.wait_for_selector(f'#row-{ids["a"]} [data-testid="confirmed"]')
    row_b = page.locator(f"#row-{ids['b']}")
    row_b.get_by_label("override category").select_option("tool")
    row_b.get_by_role("button", name="Override").click()
    page.wait_for_selector(f'#row-{ids["b"]} [data-testid="row-message"]:has-text("overridden")')
    rows = {r["item_id"]: r for r in lists.read_triage(stack.vault / "0-INBOX/Inbox-Triage.md")["rows"]}
    assert rows[ids["a"]]["confirm"] is True and rows[ids["b"]]["override"] == "tool"


def test_new_category_end_to_end_from_the_ui_then_rollback(stack, browser_page):
    """SC-001: create a category in the UI, validate, smoke eval, publish; the category becomes selectable;
    rollback removes it again."""
    ids = seed_triage(stack, a="tool")
    page, url, _, _ = browser_page
    page.on("dialog", lambda d: d.accept())
    login(page, url)
    page.goto(url + "/config")
    page.fill("#cat-id", "gardening")
    page.fill("#cat-label", "Gardening")
    page.select_option("#cat-subflow", "other_resource")
    page.fill("#cat-para", "3-Resources/Gardening")
    page.fill("#cat-hint", "Plants, soil and garden tools.")
    page.get_by_role("button", name="Add to draft").click()
    page.wait_for_selector('[data-testid="draft-message"]:has-text("added to the draft")')
    assert "gardening" in page.locator('[data-testid="diff"]').inner_text()

    page.get_by_role("button", name="Validate").click()
    page.wait_for_selector('[data-testid="validate-status"]:has-text("Valid")')
    page.get_by_role("button", name="Smoke eval").click()
    page.wait_for_selector('[data-testid="gate-status"]:has-text("passed")')
    assert "accuracy" in page.locator('[data-testid="gate-metrics"]').inner_text()  # measured values shown
    assert git(stack.overlay, "rev-list", "--count", "HEAD") == "1"  # smoke never commits

    page.fill("#summary", "Add gardening category")
    page.get_by_role("button", name="Publish").click()
    page.wait_for_selector('[data-testid="publish-status"]:has-text("Published")')
    assert git(stack.overlay, "log", "-1", "--format=%s") == "config: Add gardening category"
    assert [c["id"] for c in json.loads((stack.overlay / "categories.json").read_text())] == ["gardening"]

    page.goto(url + "/triage")
    options = page.locator(f"#row-{ids['a']} select option").all_inner_texts()
    assert "gardening" in options

    page.goto(url + "/config")
    page.get_by_role("link", name="Add gardening category").first.click()
    page.wait_for_selector('[data-testid="publish-diff"]:has-text("gardening")')
    page.get_by_role("button", name="Roll back").click()
    page.wait_for_url(url + "/config")
    assert not (stack.overlay / "categories.json").exists()
    assert git(stack.overlay, "log", "-1", "--format=%s").startswith("config: roll back")
    assert "gardening" not in stack.services.categories()


def test_editor_blocks_a_stale_second_tab_and_a_failing_gate_blocks_publish(stack, browser_page):
    """Edge case (optimistic lock) and US1 (blocked publish) in the browser."""
    from tests.support.app_stack import gate_model

    page, url, _, context = browser_page
    login(page, url)
    path = "instructions/triage/categorize.md"
    page.goto(f"{url}/config/edit?path={path}")
    second = context.new_page()
    second.goto(f"{url}/config/edit?path={path}")
    page.fill("textarea", "First tab wins.\n")
    page.get_by_role("button", name="Save to draft").click()
    page.wait_for_selector('[data-testid="editor-message"]:has-text("saved to the draft")')
    second.fill("textarea", "Second tab loses.\n")
    second.get_by_role("button", name="Save to draft").click()
    second.wait_for_selector('[data-testid="editor-message"]:has-text("changed elsewhere")')

    stack.set_gate_model(gate_model(triage_right=False))
    page.goto(url + "/config")
    page.get_by_role("button", name="Publish").click()
    page.wait_for_selector('[data-testid="publish-status"]:has-text("Not published")')
    assert "triage: accuracy" in page.locator('[data-testid="result"]').inner_text()
    assert not (stack.overlay / path).exists()


def test_owner_starts_a_run_and_opens_it(stack, browser_page):
    page, url, _, _ = browser_page
    login(page, url)
    page.goto(url + "/runs")
    page.select_option("#flow", "triage")
    page.get_by_role("button", name="Run").click()
    page.wait_for_selector('[data-testid="runs-message"]:has-text("started triage")')
    run = stack.services.list_runs()[0]
    stack.services.launcher.wait(run["run_id"])
    page.goto(f"{url}/runs/{run['run_id']}")
    page.wait_for_selector("text=Finished steps")
    assert run["run_id"] in page.content()
    page.goto(url + "/audit")
    assert "run_flow" in page.content()
