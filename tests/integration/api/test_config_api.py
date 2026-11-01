"""Spec 014 US1 / SC-001: drafts, validation, smoke eval, publish through the eval gate to git, diff and
rollback. The overlay is a temp git repository; this repository and its remotes are never touched."""

from __future__ import annotations

import json

import pytest

from tests.support.app_stack import api_client, gate_model, git

INSTRUCTION = "instructions/triage/categorize.md"


def _draft(client) -> dict:
    return client.post("/api/drafts").json()


def _edit(client, draft, path=INSTRUCTION, content="Pick the best category id.\n", **extra):
    body = {"path": path, "content": content, "rev": draft["rev"], **extra}
    return client.put(f"/api/drafts/{draft['draft_id']}/files", json=body)


def test_file_listing_shows_pack_and_overlay_files_and_only_config(stack):
    files = {f["path"]: f["source"] for f in api_client(stack).get("/api/config/files").json()["files"]}
    assert files["categories.json"] == "pack" and files["telemetry.json"] == "both"
    assert INSTRUCTION in files and not any(p.startswith(".") or ".git" in p for p in files)
    info = api_client(stack).get("/api/config/file", params={"path": INSTRUCTION}).json()
    assert info["source"] == "pack" and info["base_hash"] == "absent" and info["content"]


@pytest.mark.parametrize(
    "path",
    [
        "../outside.json",
        "/etc/passwd",
        "flows/../../x.json",
        ".git/config",
        "evil.json",
        "prompts/x.sh",
        "instructions/../models.json",
        "instructions\\x.md",
        "categories.json/../x",
        "flows/.hidden.json",
    ],
)
def test_paths_outside_the_config_surface_are_refused(stack, path):
    """Security: a draft can only touch known config files, never arbitrary paths."""
    client = api_client(stack)
    d = _draft(client)
    assert _edit(client, d, path=path, content="{}").status_code in (400, 422), path
    assert client.get("/api/config/file", params={"path": path}).status_code in (400, 422)
    assert client.get(f"/api/drafts/{d['draft_id']}").json()["files"] == {}


def test_oversized_and_binary_content_is_refused(stack):
    client = api_client(stack)
    d = _draft(client)
    assert _edit(client, d, content="x" * (257 * 1024)).status_code == 400
    assert _edit(client, d, content="a\x00b").status_code == 400


def test_validation_reports_json_and_schema_and_reference_errors(stack):
    client = api_client(stack)
    d = _draft(client)
    r = _edit(client, d, path="categories.json", content="[ not json")
    assert r.status_code == 200  # a work in progress may be saved invalid
    result = client.post(f"/api/drafts/{d['draft_id']}/validate").json()
    assert not result["ok"] and "invalid JSON" in result["errors"][0]
    d = r.json()
    bad_ref = json.dumps(
        [
            {
                "id": "x",
                "label": "X",
                "subflow": "other_resource",
                "para_path": "3-Resources/X",
                "triage_hint": "h",
                "sessions": [{"id": "s", "system": "analyst", "instruction": "missing/file.md"}],
            }
        ]
    )
    d = client.put(
        f"/api/drafts/{d['draft_id']}/files",
        json={"path": "categories.json", "content": bad_ref, "rev": d["rev"]},
    ).json()
    result = client.post(f"/api/drafts/{d['draft_id']}/validate").json()
    assert not result["ok"] and any("missing/file.md" in e for e in result["errors"])
    # the overlay on disk is untouched by drafts and validation
    assert not (stack.overlay / "categories.json").exists()


def test_valid_draft_validates_and_the_diff_shows_the_change(stack):
    client = api_client(stack)
    d = _draft(client)
    d = _edit(client, d).json()
    assert client.post(f"/api/drafts/{d['draft_id']}/validate").json()["ok"] is True
    diff = client.get(f"/api/drafts/{d['draft_id']}/diff").json()["files"]
    assert diff[0]["path"] == INSTRUCTION and "+Pick the best category id." in diff[0]["diff"]


def test_publish_runs_the_gate_then_commits_exactly_the_draft_files(stack):
    """US1 scenario 1: validation and smoke eval run before a git commit; scenario 2: diff + rollback."""
    (stack.overlay / "NOTES.private").write_text("uncommitted private notes", encoding="utf-8")
    before = git(stack.overlay, "rev-list", "--count", "HEAD")
    client = api_client(stack)
    d = _edit(client, _draft(client)).json()
    smoke = client.post(f"/api/drafts/{d['draft_id']}/smoke").json()
    assert smoke["status"] == "passed" and "triage" in smoke["affected"] and "security" in smoke["affected"]
    assert git(stack.overlay, "rev-list", "--count", "HEAD") == before  # a smoke run never commits
    resp = client.post(f"/api/drafts/{d['draft_id']}/publish", json={"summary": "Sharper triage instruction"})
    body = resp.json()
    assert resp.status_code == 200 and body["published"] and body["status"] == "passed"
    sha = body["publish"]["commit_sha"]
    assert git(stack.overlay, "rev-list", "--count", "HEAD") == str(int(before) + 1)
    assert git(stack.overlay, "show", "--name-only", "--format=%s", sha).splitlines()[0] == (
        "config: Sharper triage instruction"
    )
    assert git(stack.overlay, "show", "--name-only", "--format=", sha) == INSTRUCTION
    assert "NOTES.private" in git(stack.overlay, "status", "--porcelain")  # unrelated work not swept in
    assert (stack.overlay / INSTRUCTION).read_text() == "Pick the best category id.\n"
    diff = client.get(f"/api/publishes/{body['publish']['publish_id']}/diff").text
    assert "+Pick the best category id." in diff
    assert client.get(f"/api/drafts/{d['draft_id']}").json()["status"] == "published"
    assert client.post(f"/api/drafts/{d['draft_id']}/publish", json={}).status_code == 409  # not twice


def test_failing_gate_blocks_publish_and_writes_nothing_unless_overridden_with_a_reason(stack):
    stack.set_gate_model(gate_model(triage_right=False))
    before = git(stack.overlay, "rev-parse", "HEAD")
    client = api_client(stack)
    d = _edit(client, _draft(client), content="Always answer misc.\n").json()
    blocked = client.post(f"/api/drafts/{d['draft_id']}/publish", json={"summary": "bad"})
    assert blocked.status_code == 409 and blocked.json()["published"] is False
    assert any(f.startswith("triage: accuracy") for f in blocked.json()["failures"])
    assert git(stack.overlay, "rev-parse", "HEAD") == before and not (stack.overlay / INSTRUCTION).exists()
    assert client.get(f"/api/drafts/{d['draft_id']}").json()["status"] == "open"
    blank = client.post(f"/api/drafts/{d['draft_id']}/publish", json={"override_reason": "  "})
    assert blank.status_code == 409
    ok = client.post(
        f"/api/drafts/{d['draft_id']}/publish",
        json={"summary": "accepted", "override_reason": "owner accepts"},
    )
    assert ok.status_code == 200 and ok.json()["status"] == "overridden"
    stored = client.get("/api/publishes").json()["publishes"][0]
    assert stored["decision"]["override_reason"] == "owner accepts"
    audit = [a for a in client.get("/api/audit").json()["audit"] if a["action"] == "publish"]
    assert audit[0]["detail"]["override"] is True


def test_lowering_a_gate_threshold_is_refused_even_with_override(stack):
    """Constitution IX through the UI: thresholds only move upwards."""
    client = api_client(stack)
    d = _draft(client)
    evals = json.dumps({"suites": {"triage": {"gate": {"accuracy": {"min": 0.1}}}}})
    d = client.put(
        f"/api/drafts/{d['draft_id']}/files", json={"path": "evals.json", "content": evals, "rev": d["rev"]}
    ).json()
    resp = client.post(f"/api/drafts/{d['draft_id']}/publish", json={"override_reason": "please"})
    assert resp.status_code == 409 and resp.json()["threshold_regressions"]
    assert not (stack.overlay / "evals.json").exists()


def test_invalid_draft_is_never_gated_or_published(stack):
    client = api_client(stack)
    d = _edit(client, _draft(client), path="models.json", content='{"default": {"temperature": 99}}').json()
    resp = client.post(f"/api/drafts/{d['draft_id']}/publish", json={})
    assert resp.status_code == 409 and resp.json()["stage"] == "validate" and resp.json()["errors"]
    assert stack.gate_model.calls == []


def test_concurrent_edits_are_caught_by_the_file_hash_and_the_draft_revision(stack):
    """Edge case: optimistic lock on the file hash (disk) and on the draft revision (two tabs)."""
    client = api_client(stack)
    d = _draft(client)
    base = client.get("/api/config/file", params={"path": "telemetry.json"}).json()
    # the file changes on disk (Obsidian / git) after the editor loaded it
    (stack.overlay / "telemetry.json").write_text('{"capture_content": {"flows": ["triage"]}}\n')
    stale = _edit(client, d, path="telemetry.json", content="{}", base_hash=base["base_hash"])
    assert stale.status_code == 409 and "changed on disk" in stale.json()["error"]
    # two tabs on one draft: the second save with the old revision loses
    fresh = client.get("/api/config/file", params={"path": "telemetry.json"}).json()
    first = _edit(client, d, path="telemetry.json", content="{}", base_hash=fresh["base_hash"])
    assert first.status_code == 200
    second = _edit(client, d, path=INSTRUCTION, content="other\n")  # still carries the old rev
    assert second.status_code == 409 and "changed elsewhere" in second.json()["error"]


def test_publish_is_refused_when_a_file_changed_on_disk_after_the_draft_started(stack):
    client = api_client(stack)
    d = _draft(client)
    d = _edit(client, d, path="telemetry.json", content='{"capture_content": {"flows": ["triage"]}}\n').json()
    (stack.overlay / "telemetry.json").write_text('{"capture_content": {"flows": ["triage"]}}\n')
    git(stack.overlay, "commit", "-qam", "edited elsewhere")
    resp = client.post(f"/api/drafts/{d['draft_id']}/publish", json={})
    assert resp.status_code == 409 and "changed on disk" in json.dumps(resp.json())


def test_publish_refuses_files_with_uncommitted_edits_in_the_repository(stack):
    """Uncommitted owner work in a file the draft touches: publish refuses instead of committing it."""
    (stack.overlay / "telemetry.json").write_text(
        '{"capture_content": {"flows": ["a"]}}\n'
    )  # dirty, not committed
    client = api_client(stack)
    d = _edit(
        client, _draft(client), path="telemetry.json", content='{"capture_content": {"flows": ["b"]}}\n'
    ).json()
    before = git(stack.overlay, "rev-parse", "HEAD")
    resp = client.post(f"/api/drafts/{d['draft_id']}/publish", json={})
    assert resp.status_code == 409 and "uncommitted" in resp.json()["error"]
    assert git(stack.overlay, "rev-parse", "HEAD") == before
    assert (stack.overlay / "telemetry.json").read_text() == '{"capture_content": {"flows": ["a"]}}\n'


def test_rollback_reverts_the_commit_validates_and_cannot_repeat(stack):
    client = api_client(stack)
    d = _edit(client, _draft(client)).json()
    pub = client.post(f"/api/drafts/{d['draft_id']}/publish", json={"summary": "tune"}).json()["publish"]
    resp = client.post(f"/api/publishes/{pub['publish_id']}/rollback")
    assert resp.status_code == 200 and resp.json()["kind"] == "rollback"
    assert not (stack.overlay / INSTRUCTION).exists()
    assert git(stack.overlay, "log", "-1", "--format=%s").startswith("config: roll back")
    again = client.post(f"/api/publishes/{pub['publish_id']}/rollback")
    assert again.status_code == 409
    listed = {p["publish_id"]: p for p in client.get("/api/publishes").json()["publishes"]}
    assert listed[pub["publish_id"]]["reverted_by"] == resp.json()["publish_id"]
    assert client.post(f"/api/publishes/{resp.json()['publish_id']}/rollback").status_code == 409
    assert client.post("/api/publishes/nope/rollback").status_code == 404


def test_rollback_refused_when_later_edits_overlap(stack):
    client = api_client(stack)
    d = _edit(client, _draft(client)).json()
    pub = client.post(f"/api/drafts/{d['draft_id']}/publish", json={"summary": "first"}).json()["publish"]
    (stack.overlay / INSTRUCTION).write_text("Edited by hand in the repo.\n")
    git(stack.overlay, "commit", "-qam", "manual edit")
    resp = client.post(f"/api/publishes/{pub['publish_id']}/rollback")
    assert resp.status_code == 409
    assert (stack.overlay / INSTRUCTION).read_text() == "Edited by hand in the repo.\n"
    assert git(stack.overlay, "status", "--porcelain") == ""


def test_publish_without_overlay_or_git_repo_is_refused(stack, tmp_path):
    client = api_client(stack)
    d = _edit(client, _draft(client)).json()
    stack.services.workspace.overlay_dir = None
    resp = client.post(f"/api/drafts/{d['draft_id']}/publish", json={})
    assert resp.status_code in (400, 409, 422)
    assert git(stack.overlay, "status", "--porcelain") == ""


def test_publish_into_a_directory_that_is_not_a_git_repo_fails_cleanly(stack, tmp_path):
    plain = tmp_path / "plain-overlay"
    plain.mkdir()
    stack.services.workspace.overlay_dir = plain
    stack.services.publisher.workspace = stack.services.workspace
    client = api_client(stack)
    d = _draft(client)
    d = _edit(client, d).json()
    resp = client.post(f"/api/drafts/{d['draft_id']}/publish", json={})
    assert resp.status_code == 409 and "git" in resp.json().get("error", json.dumps(resp.json()))
    assert not (plain / INSTRUCTION).exists()


def test_new_category_end_to_end_via_api(stack):
    """SC-001: create a category, validate, gate, publish; then it is a real category the owner can pick."""
    client = api_client(stack)
    d = _draft(client)
    added = client.post(
        f"/api/drafts/{d['draft_id']}/categories",
        json={
            "id": "gardening",
            "label": "Gardening",
            "subflow": "other_resource",
            "para_path": "3-Resources/Gardening",
            "triage_hint": "Gardening resources.",
            "rev": d["rev"],
        },
    )
    assert added.status_code == 201
    dup = client.post(
        f"/api/drafts/{d['draft_id']}/categories",
        json={
            "id": "tool",
            "label": "T",
            "subflow": "other_resource",
            "para_path": "3-Resources/T",
            "triage_hint": "h",
            "rev": added.json()["rev"],
        },
    )
    assert dup.status_code == 409
    bad = client.post(
        f"/api/drafts/{d['draft_id']}/categories",
        json={
            "id": "Bad Id!",
            "label": "T",
            "subflow": "other_resource",
            "para_path": "x",
            "triage_hint": "h",
            "rev": added.json()["rev"],
        },
    )
    assert bad.status_code == 409
    assert client.post(f"/api/drafts/{d['draft_id']}/validate").json()["ok"] is True
    pub = client.post(f"/api/drafts/{d['draft_id']}/publish", json={"summary": "add gardening"})
    assert pub.status_code == 200 and pub.json()["published"], pub.json()["failures"]
    assert "gardening" in stack.services.categories()
    committed = json.loads(git(stack.overlay, "show", "HEAD:categories.json"))
    assert [c["id"] for c in committed] == ["gardening"]  # the overlay layer holds only the new entry
    from tests.support.app_stack import seed_triage

    ids = seed_triage(stack, a="tool")
    assert client.post(f"/api/triage/{ids['a']}/override", json={"category": "gardening"}).status_code == 200


def test_discarding_a_draft_leaves_the_overlay_alone(stack):
    client = api_client(stack)
    d = _edit(client, _draft(client)).json()
    assert client.delete(f"/api/drafts/{d['draft_id']}").json()["status"] == "discarded"
    assert not (stack.overlay / INSTRUCTION).exists()
    assert _edit(client, d).status_code == 409


def test_publish_and_rollback_resync_the_scheduler(stack):
    """A published schedule change takes effect without a restart, and a rollback restores it."""
    from pkpipe.scheduler import FlowScheduler

    sched = FlowScheduler(lambda flow: None, name="spec014")

    def sync():
        snap = stack.services.runs.config()
        return sched.sync(snap.data.get("schedules", []), snap.flows())

    try:
        sched.start(stack.services.runs.config().data["schedules"], stack.services.runs.config().flows())
        stack.services.on_config_change = sync
        before = str(sched.scheduler.get_job("flow:triage").trigger)
        client = api_client(stack)
        d = _draft(client)
        sched_json = json.dumps([{"flow": "triage", "cron": "5 4 * * *"}])
        d = client.put(
            f"/api/drafts/{d['draft_id']}/files",
            json={"path": "schedules.json", "content": sched_json, "rev": d["rev"]},
        ).json()
        pub = client.post(f"/api/drafts/{d['draft_id']}/publish", json={"summary": "earlier triage"})
        assert pub.status_code == 200, pub.json()
        changed = str(sched.scheduler.get_job("flow:triage").trigger)
        assert changed != before and "minute='5'" in changed
        client.post(f"/api/publishes/{pub.json()['publish']['publish_id']}/rollback")
        assert str(sched.scheduler.get_job("flow:triage").trigger) == before
    finally:
        sched.shutdown(wait=False)


def test_publish_report_and_record_carry_the_measured_gate_metrics(stack):
    """FR-004: the publish keeps its gate decision including every gated metric value (shown in the UI)."""
    client = api_client(stack)
    d = _edit(client, _draft(client)).json()
    body = client.post(f"/api/drafts/{d['draft_id']}/publish", json={"summary": "m"}).json()
    triage = body["metrics"]["triage"]
    assert triage["status"] == "passed" and triage["accuracy"]["value"] == 1.0
    assert triage["accuracy"]["op"] == ">=" and isinstance(triage["accuracy"]["threshold"], float)
    stored = client.get("/api/publishes").json()["publishes"][0]["decision"]
    assert stored["metrics"]["triage"]["accuracy"]["value"] == 1.0


def test_a_draft_edited_while_the_gate_runs_is_not_published(stack):
    """Edge case (two tabs): the commit must contain exactly what the gate evaluated."""
    client = api_client(stack)
    d = _edit(client, _draft(client)).json()
    services = stack.services
    real_gate = services._gate

    def gate_then_other_tab_saves(draft, override_reason):
        result = real_gate(draft, override_reason)
        services.edit_file(d["draft_id"], INSTRUCTION, "Edited in tab two.\n", base_hash=None, rev=None)
        return result

    services._gate = gate_then_other_tab_saves
    before = git(stack.overlay, "rev-parse", "HEAD")
    resp = client.post(f"/api/drafts/{d['draft_id']}/publish", json={"summary": "race"})
    assert resp.status_code == 409 and "changed while the eval gate ran" in resp.json()["error"]
    assert git(stack.overlay, "rev-parse", "HEAD") == before and not (stack.overlay / INSTRUCTION).exists()
    assert client.get(f"/api/drafts/{d['draft_id']}").json()["status"] == "open"


def test_publish_never_writes_through_a_symlink_out_of_the_overlay(stack, tmp_path):
    """Security: a symlinked config folder in the overlay cannot redirect a publish outside it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (stack.overlay / "instructions").symlink_to(outside, target_is_directory=True)
    client = api_client(stack)
    d = _edit(client, _draft(client)).json()
    resp = client.post(f"/api/drafts/{d['draft_id']}/publish", json={"summary": "x"})
    assert resp.status_code == 409 and "outside the overlay" in resp.json()["error"]
    assert list(outside.rglob("*")) == []
