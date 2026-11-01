"""Spec 008 end to end: ``pkpipe run secure_fetch`` through the CLI with the real composition root, the real
web-fetch MCP server (subprocess, Lightpanda behind the filtering proxy) and the local fixture site.

Swapped at the composition root: the classifier (Prompt Guard 2 is gated and not installed here) and, in the
non-live tests, the model. The test-only loopback allowance for the fixture port is added to the run's ports
by the test and to the server by its environment: no config file is involved.
"""

from __future__ import annotations

import copy
import json

import pytest

from pkpipe import app
from pkpipe.cli import main
from pkpipe.steps.secure_fetch import CLASSIFIER_PORT, TEST_LOOPBACK_PORT
from tests.fakes import FakeClassifier, FakeModel
from tests.support.fixture_site import fixture_site
from tests.support.webfetch_mcp import webfetch_server

CLEAN_REPLY = {
    "title": "vectorlite: vector search inside SQLite",
    "summary": "vectorlite adds vector similarity search to SQLite.",
    "facts": ["MIT licensed", "Runs in-process"],
    "links": ["https://example.org/vectorlite/docs"],
    "injection_suspected": False,
    "evidence": None,
}


@pytest.fixture(scope="module")
def site():
    with fixture_site() as s:
        yield s


@pytest.fixture
def served(site, tmp_path, write_json, monkeypatch):
    """Web-fetch server with the fixture allowance; overlay points the pack's ``webfetch`` server at it."""
    with webfetch_server(test_loopback=site.env_allowance) as url:
        overlay = tmp_path / "overlay"
        write_json(overlay, "mcp_servers.json", {"webfetch": {"url": url}})
        monkeypatch.delenv("PKPIPE_OVERLAY_DIR", raising=False)
        real_ports = app.ports_for

        def ports(snapshot):
            p = real_ports(snapshot)
            p.extra[CLASSIFIER_PORT] = FakeClassifier()
            p.extra[TEST_LOOPBACK_PORT] = site.allowance
            return p

        monkeypatch.setattr(app, "ports_for", ports)
        yield overlay, tmp_path / "app.db"


def _run(capsys, overlay, db, url) -> dict:
    code = main(
        [
            "run",
            "secure_fetch",
            "--input",
            json.dumps({"url": url}),
            "--overlay",
            str(overlay),
            "--db",
            str(db),
        ]
        + ["--json"]
    )
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0, out
    return out


@pytest.mark.live("lightpanda")
def test_clean_page_end_to_end_gives_the_extract(site, served, capsys, monkeypatch):
    """US1: URL -> Lightpanda -> sanitize -> scan -> Reader -> clean verdict with the validated extract."""
    overlay, db = served
    model = FakeModel(lambda s, u: copy.deepcopy(CLEAN_REPLY))
    monkeypatch.setattr(app, "make_model", lambda snapshot: model)
    out = _run(capsys, overlay, db, site.url("/clean"))
    outputs = out["outputs"]
    assert out["status"] == "done" and outputs["fetch"]["fetcher"] == "lightpanda"
    assert outputs["verdict"]["status"] == "clean"
    assert outputs["verdict"]["extract"]["facts"] == ["MIT licensed", "Runs in-process"]
    # the Reader saw the JavaScript-rendered, sanitized text inside datamarking
    assert "Rendered by JavaScript: 10 ms queries." in model.calls[0]["user"]
    assert "<script" not in model.calls[0]["user"]


@pytest.mark.live("lightpanda")
def test_hidden_injection_page_is_flagged(site, served, capsys, monkeypatch):
    """US2: hidden HTML, a comment and Unicode-tag smuggling are removed and flagged by code."""
    overlay, db = served
    model = FakeModel(lambda s, u: copy.deepcopy({**CLEAN_REPLY, "title": "Kestrel"}))
    monkeypatch.setattr(app, "make_model", lambda snapshot: model)
    out = _run(capsys, overlay, db, site.url("/hidden"))
    v = out["outputs"]["verdict"]
    assert v["status"] == "flagged" and "heuristic" in v["flags"] and "extract" not in v
    page = model.calls[0]["user"].split("## Inputs", 1)[1]  # the datamarked page, not the instruction
    assert "Kestrel is a fictional task runner" in page
    assert "Ignore all previous" not in page and "mark this page as safe" not in page
    assert "Reveal your hidden" not in page and "disregard your rules" not in page


@pytest.mark.live("lightpanda")
@pytest.mark.parametrize("path", ["/redirect-private", "/subrequest-private", "/redirect-other-port"])
def test_ssrf_attempts_are_flagged_without_reader(site, served, capsys, monkeypatch, path):
    overlay, db = served
    model = FakeModel([])
    monkeypatch.setattr(app, "make_model", lambda snapshot: model)
    out = _run(capsys, overlay, db, site.url(path))
    assert out["outputs"]["fetch"]["status"] == "blocked"
    assert out["outputs"]["verdict"]["flag_reasons"] == {"url_filter": "private_network"}
    assert model.calls == []


@pytest.mark.live("lightpanda")
def test_redirect_to_a_deny_listed_domain_is_flagged(site, served, capsys, monkeypatch):
    """FR-001: the pack's deny-list applies to redirect hops inside the web-fetch server, too."""
    overlay, db = served
    model = FakeModel([])
    monkeypatch.setattr(app, "make_model", lambda snapshot: model)
    out = _run(capsys, overlay, db, site.url("/redirect-denied"))
    assert out["outputs"]["fetch"]["status"] == "blocked"
    assert out["outputs"]["verdict"]["flag_reasons"] == {"url_filter": "deny_list"}
    assert model.calls == []


def test_private_url_is_stopped_before_the_server(served, capsys, monkeypatch):
    overlay, db = served
    monkeypatch.setattr(app, "make_model", lambda snapshot: FakeModel([]))
    out = _run(capsys, overlay, db, "http://192.168.0.10/router")
    assert out["outputs"]["url_check"]["reason"] == "private_network"
    assert "fetch" not in out["outputs"] and out["outputs"]["verdict"]["flags"] == ["url_filter"]


@pytest.mark.live("lmstudio", "lightpanda")
def test_live_reader_extracts_the_clean_page(site, served, capsys):
    """Real Reader session on LM Studio: schema-valid extract, no injection suspected, clean verdict."""
    overlay, db = served
    out = _run(capsys, overlay, db, site.url("/clean"))
    read, v = out["outputs"]["read"], out["outputs"]["verdict"]
    assert read["canary_leaked"] is False and read["injection_suspected"] is False
    assert v["status"] == "clean" and "vectorlite" in json.dumps(v["extract"]).lower()


@pytest.mark.live("lmstudio", "lightpanda")
def test_live_reader_flags_a_visible_injection(site, served, capsys):
    """The page tells the model to write only PWNED: the verdict is flagged and the Reader did not obey."""
    overlay, db = served
    out = _run(capsys, overlay, db, site.url("/visible-injection"))
    v, read = out["outputs"]["verdict"], out["outputs"]["read"]
    assert v["status"] == "flagged" and "extract" not in v
    assert read["summary"].strip().upper() != "PWNED" and "sourdough" in json.dumps(read).lower()


@pytest.mark.live("lightpanda")
def test_without_prompt_guard_the_real_composition_root_fails_closed(
    site, served, capsys, monkeypatch, tmp_path
):
    """FR-004 + constitution V through the real ``app.make_classifier``: no Prompt Guard model installed means
    the clean page is still flagged (``classifier_unavailable``) and no extract leaves secure fetch."""
    overlay, db = served
    monkeypatch.setenv("PKPIPE_PROMPT_GUARD_DIR", str(tmp_path / "no-model-here"))
    # the fixture put a fake classifier in; put the one of the real factory back

    def ports(snapshot, _swapped=app.ports_for):
        p = _swapped(snapshot)
        p.extra[CLASSIFIER_PORT] = app.make_classifier(snapshot)
        return p

    monkeypatch.setattr(app, "ports_for", ports)
    app._CLASSIFIERS.clear()
    monkeypatch.setattr(
        app, "make_model", lambda snapshot: FakeModel(lambda s, u: copy.deepcopy(CLEAN_REPLY))
    )
    out = _run(capsys, overlay, db, site.url("/clean"))
    v = out["outputs"]["verdict"]
    assert out["outputs"]["scan"]["classifier"]["available"] is False
    assert v["status"] == "flagged" and v["flag_reasons"] == {"classifier": "classifier_unavailable"}
    assert "extract" not in v
