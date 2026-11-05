"""Spec 021 US4 (T011): the synthetic image generator and the eval suite ``images`` on fakes.

The suite renders images from recipes (Pillow, the optional extra: these tests skip without it), sends the
prompt the engine builds for the ``describe`` node with the prepared image, and checks the answer against the
known truth by code. A scripted model plays a perfect and a bad viewer; no model server is involved.
"""

from __future__ import annotations

import io
import json

import pytest

from pkpipe.engine.registry import load_effective
from pkpipe.evals import synthetic_images as si
from pkpipe.evals.cassette import CassetteModel
from pkpipe.evals.datasets import EvalPaths, load_jsonl
from pkpipe.evals.models import Status
from pkpipe.evals.runner import EvalRunner
from pkpipe.evals.suites import ImagesSuite, SuiteContext, image_checks
from pkpipe.ports import ImageInput, SchemaError
from tests.conftest import REPO
from tests.support.evals import IDS, mini_evals

PIL = pytest.importorskip("PIL", reason="the optional extra 'images' (Pillow) is not installed")

PATHS = EvalPaths.create(REPO / "evals")
CASES = {c.id: c for c in load_jsonl(PATHS.find("datasets/images.jsonl"), "images")}
BY_REF = {si.render_ref(c.input["render"]): c for c in CASES.values()}


@pytest.fixture
def snap(pack_dir):
    return load_effective(pack_dir, None).snapshot()


# -- the generator -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", si.KINDS)
def test_every_kind_renders_a_png_of_the_standard_size(kind):
    recipe = {
        "project_page": {
            "kind": kind,
            "title": "T",
            "tagline": "t",
            "bullets": ["a"],
            "url": "https://x.example",
        },
        "post": {"kind": kind, "author": "A", "handle": "@a", "text": ["x"], "likes": 1, "replies": 0},
        "chart": {"kind": kind, "title": "C", "series": [["a", 1], ["b", 2]], "caption": "c"},
        "meme": {"kind": kind, "top": "top", "bottom": "bottom"},
        "photo": {"kind": kind, "seed": 1},
        "text_card": {"kind": kind, "heading": "H", "lines": ["l"]},
    }[kind]
    from PIL import Image

    img = Image.open(io.BytesIO(si.render(recipe)))
    assert img.format == "PNG" and img.size == (si.WIDTH, si.HEIGHT)


def test_rendering_is_deterministic_and_the_ref_follows_the_recipe_and_version(monkeypatch):
    recipe = CASES["post-release"].input["render"]
    assert si.render(recipe) == si.render(recipe)
    assert si.render_ref(recipe) == si.render_ref(dict(recipe))
    assert si.render_ref(recipe) != si.render_ref({**recipe, "likes": 413})
    before = si.render_ref(recipe)
    monkeypatch.setattr(si, "GENERATOR_VERSION", "2")
    assert si.render_ref(recipe) != before  # a changed drawing never replays an old recording


def test_different_recipes_give_different_pictures_and_the_ref_is_not_the_bytes_digest():
    a, b = (si.render(CASES[i].input["render"]) for i in ("photo-mountains", "photo-hills"))
    assert a != b
    assert si.render_ref(CASES["photo-hills"].input["render"]).startswith("synthetic:")


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="unknown synthetic image kind"):
        si.render({"kind": "painting"})


def test_the_dataset_covers_every_kind_and_has_unique_ids_and_known_truth():
    assert len(CASES) >= 12
    kinds = {c.input["render"]["kind"] for c in CASES.values()}
    assert {"project_page", "post", "chart", "meme", "photo", "text_card"} <= kinds
    injected = [c for c in CASES.values() if c.expected["injection"]]
    benign_about_injection = [c for c in CASES.values() if "inject" in c.id and not c.expected["injection"]]
    assert len(injected) >= 2 and benign_about_injection  # attacks and a clean one that talks about them
    for c in CASES.values():
        assert si.render(c.input["render"])  # every recipe renders
        assert "topic" in c.expected and "text" in c.expected and "kind" in c.expected
    assert len(BY_REF) == len(CASES)  # no two cases share an identity


# -- the code checks -----------------------------------------------------------------------------------------


def test_the_checks_ignore_casing_and_punctuation_but_not_missing_text():
    exp = {"text": ["Estimate: two days", "Reality: two weeks"], "kind": ["meme"], "injection": False}
    ok = image_checks(
        {"kind": "meme", "visible_text": "ESTIMATE TWO DAYS / REALITY, TWO WEEKS"}, exp, "CNY-0123456789abcdef"
    )
    assert ok["text_recall"] == 1.0 and ok["kind_ok"] is True and ok["canary_leak"] is False
    half = image_checks({"kind": "photo", "visible_text": "estimate: two days"}, exp, "CNY-0123456789abcdef")
    assert half["text_recall"] == 0.5 and half["kind_ok"] is False


def test_the_url_may_be_listed_or_read_in_the_visible_text():
    exp = {"text": [], "url": "https://tracebox.example/docs", "kind": ["screenshot"]}
    assert image_checks({"urls": ["https://tracebox.example/docs/"]}, exp, "c")["url_ok"] is True
    assert image_checks({"urls": [], "visible_text": "tracebox example docs"}, exp, "c")["url_ok"] is True
    assert image_checks({"urls": ["https://other.example"]}, exp, "c")["url_ok"] is False


def test_a_canary_anywhere_in_the_answer_is_a_leak():
    assert image_checks({"topic": "see CNY-0123456789abcdef"}, {}, "CNY-0123456789abcdef")["canary_leak"]


# -- the suite -----------------------------------------------------------------------------------------------


class Viewer:
    """A scripted vision model: answers per image from the known truth (``mode`` ``perfect``) or badly."""

    def __init__(self, mode: str = "perfect", judge_score: int = 5):
        self.mode, self.judge_score = mode, judge_score
        self.calls: list[dict] = []

    def complete(self, system, user, json_schema=None, profile="default", images=None):
        if not images:  # the judge
            self.calls.append({"profile": profile, "judge": True})
            return {"reasoning": "ok", "score": self.judge_score}
        case = BY_REF[images[0].key]
        self.calls.append(
            {"profile": profile, "system": system, "user": user, "schema": json_schema, "image": images[0]}
        )
        exp = case.expected
        if self.mode == "invalid":
            raise SchemaError("not json")
        if self.mode == "bad":
            return {
                "kind": "photo" if exp["kind"][0] != "photo" else "meme",
                "topic": "Something unrelated.",
                "visible_text": "nothing readable",
                "urls": [],
                "language": "en",
                "category_hint": "unknown",
                "injection_suspected": not exp["injection"],
                "evidence": None,
            }
        return {
            "kind": exp["kind"][0],
            "topic": exp["topic"],
            "visible_text": " ".join(exp["text"])[:400],
            "urls": [exp["url"]] if exp.get("url") else [],
            "language": "en",
            "category_hint": "unknown",
            "injection_suspected": exp["injection"],
            "evidence": "quoted" if exp["injection"] else None,
        }


def run(snap, model, *, judge=None):
    from pkpipe.evals.judge import Judge

    j = (
        Judge(judge, "judge", identity="http://h|judge", subject_identities=["http://h|vision"])
        if judge
        else None
    )
    ctx = SuiteContext(snapshot=snap, paths=PATHS, model=model, judge=j, judge_error=None if j else "none")
    return ImagesSuite().run(ctx)


def test_a_perfect_viewer_passes_every_check_and_the_pack_gate(snap):
    model = Viewer()
    report = run(snap, model)
    m = report.metrics
    assert report.status is Status.PASSED and m["n"] == len(CASES) and m["pass_rate"] == 1.0
    assert m["schema_valid_rate"] == 1.0 and m["canary_leaks"] == 0 and m["kind_accuracy"] == 1.0
    assert m["text_recall"] == 1.0 and m["verbatim_text_rate"] == 1.0 and m["url_found_rate"] == 1.0
    assert m["injection_detection_rate"] == 1.0 and m["injection_false_positive_rate"] == 0.0
    assert "judge_mean" not in m and any("judge unavailable" in n for n in report.notes)  # optional


def test_the_request_is_what_the_engine_sends_for_the_describe_node(snap):
    model = Viewer()
    run(snap, model)
    call = model.calls[0]
    assert call["profile"] == "vision"
    assert call["system"].startswith(snap.asset("prompts/system/viewer.md").strip())
    assert "CNY-" in call["system"]  # the per-case canary the session carries in a run
    assert call["schema"] == snap.json_asset("schemas/image_description.json")
    assert call["user"].strip() == snap.asset("instructions/images/describe.md").strip()
    image = call["image"]
    assert (
        isinstance(image, ImageInput) and image.media_type == "image/jpeg" and image.data[:2] == b"\xff\xd8"
    )
    from PIL import Image

    prepared = Image.open(io.BytesIO(image.data))
    assert max(prepared.size) <= 1024 and not prepared.getexif()  # scaled, no metadata, like a run


def test_a_bad_viewer_fails_the_gate_on_the_measured_metrics(snap):
    report = run(snap, Viewer("bad"))
    m = report.metrics
    assert m["kind_accuracy"] < 0.2 and m["text_recall"] < 0.2 and m["injection_false_positive_rate"] > 0.9
    assert m["injection_detection_rate"] == 0.0 and m["pass_rate"] < 0.2


def test_an_invalid_answer_is_a_schema_failure_not_a_crash(snap):
    report = run(snap, Viewer("invalid"))
    assert report.metrics["schema_valid_rate"] == 0.0 and report.metrics["n"] == len(CASES)
    assert all(not r.passed and "SchemaError" in (r.error or "") for r in report.cases)


def test_a_judge_rates_only_the_topic_and_is_optional(snap):
    judge = Viewer(judge_score=4)
    report = run(snap, Viewer(), judge=judge)
    assert report.metrics["judge_mean"] == 4.0 and report.metrics["judged"] == len(CASES)
    judged = [c for c in judge.calls if c.get("judge")]
    assert len(judged) == len(CASES)


def test_without_pillow_the_suite_is_skipped_not_failed(snap, monkeypatch):
    monkeypatch.setattr(si, "pillow_available", lambda: False)
    report = run(snap, Viewer())
    assert report.status is Status.SKIPPED and "Pillow" in report.reason


def test_the_runner_never_judges_with_the_model_under_test(snap):
    """The subject of this suite is the vision profile: a judge that resolves to it is refused (US4)."""
    ids = {**IDS, "vision": "http://h|google/gemma-4-12b", "judge": "http://h|google/gemma-4-12b"}
    runner = EvalRunner(snap, paths=PATHS, model=Viewer(), identities=ids)
    judge, error = runner.judge_for(runner.model, ImagesSuite().subject_profiles(snap))
    assert judge is None and "model under test" in error
    other = {**ids, "judge": "http://h|qwen3.5-9b-mlx"}
    judge, _ = EvalRunner(snap, paths=PATHS, model=Viewer(), identities=other).judge_for(
        runner.model, ImagesSuite().subject_profiles(snap)
    )
    assert judge is not None
    # the text suites keep the validated pairing: gemma judges the default profile (qwen)
    text_ids = {**ids, "default": "http://h|qwen3.5-9b-mlx"}
    judge, _ = EvalRunner(snap, paths=PATHS, model=Viewer(), identities=text_ids).judge_for(runner.model)
    assert judge is not None


def test_a_cassette_replays_the_suite_without_the_model_and_without_the_bytes(snap, tmp_path):
    ids = {**IDS, "vision": "http://h|vision-model"}
    path = tmp_path / "images.json"
    rec = CassetteModel(path, "record", inner=Viewer(), identities=ids)
    first = run(snap, rec)
    rec.save()
    text = path.read_text(encoding="utf-8")
    assert "synthetic:" in text and "\\u00ff" not in text and len(json.loads(text)["entries"]) == len(CASES)
    replay = CassetteModel(path, "replay", identities=ids)
    again = run(snap, replay)
    assert again.status is Status.PASSED and again.metrics == first.metrics and replay.hits == len(CASES)


def test_replay_without_a_recording_skips_instead_of_failing_every_case(snap, tmp_path):
    replay = CassetteModel(tmp_path / "missing.json", "replay", identities={"vision": "http://h|m"})
    report = run(snap, replay)
    assert report.status is Status.SKIPPED and "no recorded responses" in report.reason


def test_the_runner_applies_the_pack_gate_to_the_suite(snap, tmp_path):
    paths = mini_evals(
        tmp_path, {"datasets/images.jsonl": (REPO / "evals/datasets/images.jsonl").read_text()}
    )
    ids = {**IDS, "vision": "http://h|vision-model"}
    ok = EvalRunner(snap, paths=paths, model=Viewer(), identities=ids).run(["images"]).suite("images")
    assert ok.status is Status.PASSED and ok.gate and all(g.passed for g in ok.gate)
    bad = EvalRunner(snap, paths=paths, model=Viewer("bad"), identities=ids).run(["images"]).suite("images")
    assert bad.status is Status.FAILED and any(not g.passed for g in bad.gate)
