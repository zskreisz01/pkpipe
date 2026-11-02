"""Spec 011 US1 and US2 across specs: the refresh writes real files into an overlay on disk; the next config
load carries the packs into the spec 010 fit sessions and the terms into the query guard, which then blocks
web queries that name a client. Model, notifier and clock are fakes; everything else is real files."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pkpipe.adapters.context_store_files import FileContextStore
from pkpipe.engine.registry import load_effective
from pkpipe.evals.category_sessions import find_session, llm_config
from pkpipe.steps import RunInfo, StepContext
from pkpipe.steps.llm import build_prompt, query_blocked
from tests.fakes import FakeClock, FakeModel, FakeNotify, RecordingTracer
from tests.support.context_packs import CLIENT, PROJECT, ROUTES, ContextModel, seed_vault
from tests.support.flows import make_ports, make_service
from tests.support.pack_runtime import vault_tools


@pytest.fixture
def refreshed(tmp_path, pack_dir):
    vault, overlay = tmp_path / "vault", tmp_path / "overlay"
    seed_vault(vault)
    overlay.mkdir()
    snap = load_effective(pack_dir, overlay).snapshot()
    ports = make_ports(
        tools=vault_tools(vault),
        model=FakeModel(ContextModel(leaky=True), schema_check=True),
        notify=FakeNotify(routes=ROUTES),
        tracer=RecordingTracer(),
        clock=FakeClock(),
        context_store=FileContextStore(overlay),
    )
    run = make_service(snap, ports).start("context_refresh", trigger="schedule")
    assert run.ok, run.error
    return overlay, load_effective(pack_dir, overlay)


def test_the_files_land_in_the_overlay_and_the_public_pack_stays_untouched(refreshed, pack_dir):
    overlay, _ = refreshed
    assert sorted(p.name for p in (overlay / "contexts").iterdir()) == ["enterprise.md", "personal.md"]
    assert (overlay / "security/query_guard_terms.txt").is_file()
    assert (
        not (pack_dir / "contexts/personal.md").exists()
        and not (pack_dir / "contexts/enterprise.md").exists()
    )
    assert (pack_dir / "contexts/system.md").is_file()  # FR-003: the public system pack ships in the pack


def test_the_next_config_load_has_no_missing_context_warnings(refreshed):
    _, cfg = refreshed
    from pkpipe.engine.registry import check_references

    _, warnings = check_references(cfg)
    assert not [w for w in warnings if "context pack" in w]


def test_the_fit_sessions_receive_the_packs_without_the_bookkeeping_comments(refreshed):
    _, cfg = refreshed
    snap = cfg.snapshot()
    for session, expect, forbidden in (
        ("fit_personal", "Homelab", "Brightwater"),
        ("fit_enterprise", "PostgreSQL", "Brightwater"),
    ):
        node = find_session(snap, "tool", session)
        system, user, _ = build_prompt(
            llm_config(node),
            SimpleNamespace(snapshot=snap, inputs={"research": {"n": 1}, "compare": {"n": 2}}),
        )
        assert "## Context" in user and expect in user and "pkpipe:" not in user
        if session == "fit_enterprise":
            assert forbidden not in user and CLIENT not in user and PROJECT not in user


def test_the_query_guard_blocks_the_names_of_the_enterprise_notes(refreshed):
    """US2.1: after the refresh the guard blocks a web query that names the client or the project, in any
    spelling, and lets a clean query pass; the model is never told which term matched."""
    _, cfg = refreshed
    snap = cfg.snapshot()
    ctx = StepContext(
        flow=None,
        node=None,
        run=RunInfo("r", "f", "manual", snap.version),
        snapshot=snap,
        ports=make_ports(),
        item=None,
        inputs={},
        resolver=lambda ref: None,
    )
    for query in (
        f"{CLIENT} migration case study",
        f"alternatives for {PROJECT.lower()}",
        "brightwater-bank oracle",
        "FALCON ledger tooling",
        "marlow retail kafka",
    ):
        assert query_blocked(ctx, "websearch:search", {"query": query}), query
    assert not query_blocked(ctx, "websearch:search", {"query": "oracle to postgresql migration tools"})
    assert not query_blocked(ctx, "obsidian:read_note", {"path": f"{CLIENT}.md"})  # local tools do not leave


def test_a_second_refresh_keeps_the_terms_and_does_not_duplicate_them(refreshed, pack_dir):
    overlay, cfg = refreshed
    before = (overlay / "security/query_guard_terms.txt").read_text(encoding="utf-8")
    vault = overlay.parent / "vault"
    ports = make_ports(
        tools=vault_tools(vault),
        model=FakeModel(ContextModel(leaky=True), schema_check=True),
        notify=FakeNotify(routes=ROUTES),
        clock=FakeClock(),
        context_store=FileContextStore(overlay),
    )
    assert make_service(cfg.snapshot(), ports).start("context_refresh").ok
    assert (overlay / "security/query_guard_terms.txt").read_text(encoding="utf-8") == before
