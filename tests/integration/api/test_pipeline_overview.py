"""Spec 033: the pipeline overview over HTTP, in the UI, in the app MCP and on the CLI, on a real vault.

One bucket per item, what needs the owner with its action, the landing page and the item header."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client

from tests.integration.api.test_retry_failed_items import PROCESSING, fail, rows
from tests.integration.api.test_web_triage_surface import login
from tests.support.app_stack import OWNER_TOKEN, api_client, running, seed_triage

EVIDENCE = {
    "threshold": 0.5,
    "max_score": 0.99,
    "reading": "injection_detector_only",
    "members": [],
}


@pytest.fixture
def world(stack):
    ids = seed_triage(stack, new="tool", failed="tool", done="tool", held="tool", quar="tool", rel="tool")
    vault = stack.services.vault
    fail(stack, ids["failed"])
    fail(stack, ids["done"], status="done")
    fail(stack, ids["held"])
    stack.services.store.hold(ids["held"], "https://example.org/repo", EVIDENCE)
    vault.update_triage([{"item_id": ids["quar"], "state": "quarantined"}])
    vault.owner_decide(ids["rel"], confirm=True)
    vault.update_triage([{"item_id": ids["rel"], "state": "released"}])
    return ids


def by_bucket(ov):
    return {code: n for code, n in ov["funnel"]["buckets"].items() if n}


def test_the_funnel_adds_up_to_the_triage_table_and_every_item_has_one_bucket(stack, world):
    client = api_client(stack)
    ov = client.get("/api/pipeline").json()
    table = client.get("/api/triage").json()["rows"]
    assert ov["funnel"]["total"] == len(table) == 6
    assert sum(ov["funnel"]["buckets"].values()) == 6
    assert by_bucket(ov) == {
        "waiting_confirmation": 1,
        "released": 1,
        "done": 1,
        "held": 1,
        "failed": 1,
        "quarantined": 1,
    }
    assert [s["stage"] for s in ov["funnel"]["stages"]] == ["triaged", "released", "processed", "stopped"]
    assert "items" not in ov  # the per-item records stay inside


def test_what_needs_the_owner_has_the_action_and_the_closed_item_does_not_appear(stack, world):
    ov = api_client(stack).get("/api/pipeline").json()
    needs = {n["item_id"]: n for n in ov["needs_you"]}
    assert set(needs) == {world["held"], world["quar"], world["failed"]}
    assert needs[world["held"]]["action"]["kind"] == "accept"
    assert needs[world["quar"]]["action"]["kind"] == "release"
    assert needs[world["failed"]]["action"]["kind"] == "retry"
    assert world["done"] not in needs and world["new"] not in needs


def test_the_pipeline_page_and_the_landing_page_show_the_counters_and_the_cards(stack, world):
    client = login(stack)
    page = client.get("/pipeline")
    assert page.status_code == 200
    for marker in ('data-testid="funnel-total"', 'data-testid="needs-card"', 'data-testid="act-accept"'):
        assert marker in page.text
    assert f'id="item-{world["held"]}"' in page.text and f"/items/{world['failed']}" in page.text
    home = client.get("/")
    assert 'data-testid="funnel-total"' in home.text and 'data-testid="needs-card"' in home.text
    assert 'data-testid="launch-triage"' in home.text  # the start buttons stay on the landing page
    nav = page.text.split("<header>")[1].split("</header>")[0]
    assert nav.index("Pipeline") < nav.index("Overview") < nav.index("Triage")


def test_the_markup_works_on_a_phone():
    base = (Path(__file__).parents[3] / "src/pkpipe/api/templates/base.html").read_text()
    css = (Path(__file__).parents[3] / "src/pkpipe/api/static/app.css").read_text()
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in base
    phone = css.split("@media (max-width: 600px)")[1]
    assert "grid-template-columns:1fr" in phone and "min-height:44px" in phone
    assert "overflow-wrap:anywhere" in css and ".ptable { display:block; overflow-x:auto; }" in css


def test_an_owner_action_on_a_card_uses_the_audited_service_and_refreshes_the_page(stack, world):
    client = login(stack)
    out = client.post(f"/ui/pipeline/{world['failed']}/retry")
    assert out.status_code == 200 and "retry:" in out.text and 'data-testid="pipeline-message"' in out.text
    assert rows(stack)[world["failed"]]["state"] == "released"
    audit = stack.services.store.audit_log()
    assert audit[0]["action"] == "retry_item" and audit[0]["target"] == world["failed"]
    assert f'id="item-{world["failed"]}"' not in out.text  # it left "needs you"
    bad = client.post(f"/ui/pipeline/{world['failed']}/explode")
    assert "unknown action" in bad.text and 'class="err"' in bad.text
    again = client.post(f"/ui/pipeline/{world['done']}/retry")  # the rules refuse, the page says why
    assert 'class="err"' in again.text and "did not fail" in again.text


def test_the_item_page_says_where_the_item_is_now(stack, world):
    client = login(stack)
    page = client.get(f"/items/{world['held']}").text
    assert 'data-testid="item-where"' in page and "held for you" in page and "processed" in page
    done = client.get(f"/items/{world['done']}").text
    assert "Where it is now" in done and ">done<" in done


def test_the_overview_is_an_owner_tool_and_the_cli_prints_it(stack, world, capsys):
    from pkpipe.mcp.server import OWNER_TOOLS
    from pkpipe.pipeline_cli import pipeline_command

    assert "pipeline_overview" in OWNER_TOOLS
    args = SimpleNamespace(
        pack=str(stack.pack), overlay=str(stack.overlay), db=str(stack.db), vault=str(stack.vault), json=True
    )
    assert pipeline_command(args) == 0
    ov = json.loads(capsys.readouterr().out)
    assert ov["funnel"]["total"] == 6 and len(ov["needs_you"]) == 3
    args.json = False
    assert pipeline_command(args) == 0
    text = capsys.readouterr().out
    assert "Needs you (3)" in text and world["held"] in text


def test_the_app_mcp_tool_returns_the_overview_to_the_owner_only(stack, world):
    from tests.support.app_stack import AGENT_TOKEN

    async def call(url, token):
        async with Client(f"{url}/mcp", auth=token) as c:
            try:
                res = await c.call_tool("pipeline_overview", {})
            except Exception as exc:  # noqa: BLE001
                return str(exc), None
            return None, res.data

    with running(stack) as srv:
        err, data = asyncio.run(call(srv.url, OWNER_TOKEN))
        assert not err and data["funnel"]["total"] == 6
        err, data = asyncio.run(call(srv.url, AGENT_TOKEN))
        assert err and data is None


def test_a_vault_that_cannot_be_read_is_an_error_message_not_a_crash(stack):
    stack.services.vault = None
    page = login(stack).get("/pipeline")
    assert page.status_code == 200 and 'data-testid="pipeline-error"' in page.text


def test_the_processing_list_name_is_the_one_the_overview_reads(stack, world):
    assert PROCESSING.endswith("Manual-Resource-Processing-List.md")
    ov = api_client(stack).get("/api/pipeline").json()
    assert ov["where"]["processing_list"] == PROCESSING
