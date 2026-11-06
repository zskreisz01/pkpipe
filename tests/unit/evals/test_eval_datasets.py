"""Spec 013 data model (EvalCase) and the shipped synthetic datasets (docs/evaluation.md "Datasets")."""

from __future__ import annotations

import json
from collections import Counter
from urllib.parse import urlparse

import pytest

from pkpipe.engine.registry import load_effective
from pkpipe.evals.datasets import DatasetError, EvalPaths, load_json_case, load_jsonl
from pkpipe.evals.models import Source
from tests.conftest import REPO

EVALS = REPO / "evals"
# the sessions gate holds per session at checks >= 0.90: 12 cases tolerate one model slip, 5 or 6 do not
MIN_SESSION_CASES = 12


def test_load_jsonl_fills_suite_and_validates(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text(
        '{"id": "a", "input": {"x": 1}}\n\n// comment\n{"id": "b", "input": {}, "source": "override"}\n'
    )
    cases = load_jsonl(p, "triage")
    assert [c.id for c in cases] == ["a", "b"] and cases[0].suite == "triage"
    assert cases[0].source is Source.SYNTHETIC and cases[1].source is Source.OVERRIDE


@pytest.mark.parametrize(
    "line, message",
    [
        ("{not json", "d.jsonl:1: invalid JSON"),
        ('{"id": "a"}', "d.jsonl:1: input"),
        ('{"id": "a", "input": {}, "source": "scraped"}', "d.jsonl:1: source"),
        ('{"id": "a", "input": {}, "extra": 1}', "d.jsonl:1: extra"),
        ("[1]", "expected a JSON object"),
    ],
)
def test_bad_lines_name_file_and_line(tmp_path, line, message):
    p = tmp_path / "d.jsonl"
    p.write_text(line + "\n")
    with pytest.raises(DatasetError, match=message):
        load_jsonl(p, "s")


def test_duplicate_ids_are_rejected(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text('{"id": "a", "input": {}}\n{"id": "a", "input": {}}\n')
    with pytest.raises(DatasetError, match="duplicate case id 'a'"):
        load_jsonl(p, "s")


def test_json_case_defaults_id_to_file_stem(tmp_path):
    p = tmp_path / "my-case.json"
    p.write_text('{"input": {"steps": []}}')
    assert load_json_case(p, "flows").id == "my-case"
    p.write_text("[]")
    with pytest.raises(DatasetError):
        load_json_case(p, "flows")


def test_overlay_datasets_win_per_path(tmp_path):
    """Constitution VII: the private overlay replaces or adds datasets without touching the public repo."""
    public, overlay = tmp_path / "public", tmp_path / "overlay"
    (public / "datasets").mkdir(parents=True)
    (overlay / "evals" / "datasets").mkdir(parents=True)
    (public / "datasets" / "triage.jsonl").write_text("public")
    (public / "datasets" / "security.jsonl").write_text("public")
    (overlay / "evals" / "datasets" / "triage.jsonl").write_text("private")
    paths = EvalPaths.create(public, overlay)
    assert paths.find("datasets/triage.jsonl").read_text() == "private"
    assert paths.find("datasets/security.jsonl").read_text() == "public"
    assert paths.find("datasets/none.jsonl") is None
    assert paths.cassette_dir == overlay / "evals" / "cassettes"
    assert EvalPaths.create(public, None).cassette_dir == public / "cassettes"


# -- shipped datasets ----------------------------------------------------------------------------------------
def _all_jsonl():
    return sorted(EVALS.glob("datasets/**/*.jsonl"))


@pytest.mark.parametrize("path", _all_jsonl(), ids=lambda p: str(p.relative_to(EVALS)))
def test_every_shipped_dataset_is_valid_and_synthetic(path):
    """Constitution VII: public datasets are synthetic; links only to reserved example domains."""
    cases = load_jsonl(path, "x")
    assert cases and all(c.source is Source.SYNTHETIC for c in cases)
    for c in cases:
        url = c.input.get("url")
        if url:
            host = urlparse(url).hostname or ""
            reserved = ("example.com", "example.org", "example.net")  # RFC 2606, plus *.invalid
            assert host.endswith(".invalid") or any(host == d or host.endswith("." + d) for d in reserved), (
                url
            )


def test_triage_dataset_size_and_categories_match_the_pack(pack_dir):
    """docs/evaluation.md: 60-100 entries, >= 10 per category; every label is a configured category."""
    cases = load_jsonl(EVALS / "datasets/triage.jsonl", "triage")
    counts = Counter(c.expected["category"] for c in cases)
    categories = {c["id"] for c in load_effective(pack_dir, None).data["categories"]}
    assert 60 <= len(cases) <= 100
    assert set(counts) == categories and min(counts.values()) >= 10


def test_security_and_other_datasets_have_their_documented_shape():
    sec = load_jsonl(EVALS / "datasets/security.jsonl", "security")
    labels = Counter(c.expected["label"] for c in sec)
    assert labels["malicious"] >= 40 and labels["benign"] >= 40
    assert len({c.expected.get("type") for c in sec if c.expected["label"] == "malicious"}) >= 5
    inj = load_jsonl(EVALS / "datasets/injection.jsonl", "injection")
    assert len(inj) >= 10 and all("category" in c.expected for c in inj)
    calib = load_jsonl(EVALS / "datasets/calibration/resource_note.jsonl", "c")
    assert len(calib) == 20 and {c.expected["owner_score"] for c in calib} == {1, 2, 3, 4, 5}
    flows = sorted((EVALS / "datasets/flows").glob("*.json"))
    assert len(flows) >= 6 and all(json.loads(p.read_text())["expected"] for p in flows)


def test_every_category_session_has_a_synthetic_dataset():
    """Spec 010 T006 / constitution IX: each session of an other_resource category plan of the public
    pack has a
    dataset ``datasets/sessions/<category>/<session>.jsonl`` with its inputs and code checks."""
    from pkpipe.evals.category_sessions import session_names
    from pkpipe.evals.suites import find_node

    snap = load_effective(None, None).snapshot()
    names = session_names(snap)
    assert {"tool.research", "tool.synthesize", "marketing.extract", "misc.evaluate"} <= set(names)
    for name in names:
        category, session = name.split(".")
        cases = load_jsonl(EVALS / f"datasets/sessions/{category}/{session}.jsonl", name)
        assert len(cases) >= MIN_SESSION_CASES, name
        node = find_node(snap, category, session)
        for case in cases:
            assert case.source == "synthetic" and case.expected, (name, case.id)
            required = set(node.inputs) - {"item.owner_note"}  # the owner's note is optional (spec 023)
            assert "*" in node.inputs or required <= set(case.input), (name, case.id)
            assert ("context" in case.input) == bool(node.config.get("context")), (name, case.id)
        injected = [c for c in cases if "ignore all previous instructions" in json.dumps(c.input).lower()]
        assert injected, f"{name} has no case with an injected instruction"


def test_every_session_dataset_is_large_enough_for_its_own_gate():
    """The sessions gate holds per session (checks >= 0.90): with 5 or 6 cases a single model slip fails it,
    with 12 one slip still passes, so no session dataset is smaller."""
    small = {
        str(p.relative_to(EVALS)): n
        for p in sorted((EVALS / "datasets/sessions").glob("*/*.jsonl"))
        if (n := len(load_jsonl(p, p.stem))) < MIN_SESSION_CASES
    }
    assert not small, small


def test_every_session_dataset_has_a_rubric():
    paths = EvalPaths.create(EVALS)
    snap = load_effective(None, None).snapshot()
    from pkpipe.evals.suites import session_rubric, session_targets

    targets = session_targets(paths)
    assert targets
    assert all(session_rubric(paths, snap, t) for t in targets)
