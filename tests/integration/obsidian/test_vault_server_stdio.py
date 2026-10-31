"""Spec 004 FR-001, FR-002, FR-003, SC-002: the server as a real MCP stdio subprocess on a synthetic vault."""

from __future__ import annotations

import asyncio
import os

import pytest

from tests.support.vault_mcp import call, stdio_client, write_policy

TOOLS = {
    "list_folder",
    "read_note",
    "write_note",
    "append_note",
    "move_note",
    "read_triage",
    "update_triage",
    "upsert_processing_list",
}

TRAVERSAL = [
    "../escape.md",
    "0-INBOX/../../escape.md",
    "/tmp/escape.md",
    "~/escape.md",
    "0-INBOX/..\\..\\escape.md",
    ".obsidian/workspace.md",
    "0-INBOX/./x.md",
]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def config(tmp_path, synthetic_vault):
    return write_policy(tmp_path, synthetic_vault, allow={"write_note": ["0-INBOX", "3-Resources/Tools"]})


def test_server_exposes_exactly_the_specified_tools(config):
    """FR-001."""

    async def go():
        async with stdio_client(config) as c:
            return {t.name for t in await c.list_tools()}

    assert run(go()) == TOOLS


def test_read_write_append_move_cycle_over_mcp(config, synthetic_vault):
    """US1: INBOX item is read, a curated note written, appended and the source moved."""

    async def go():
        async with stdio_client(config) as c:
            err, listing = await call(c, "list_folder", path="0-INBOX")
            assert not err and "example-tool.md" in listing["notes"]
            err, note = await call(c, "read_note", path="0-INBOX/example-tool.md")
            assert not err and note["front_matter"]["source"] == "https://example.invalid/tool"
            err, _ = await call(
                c,
                "write_note",
                path="3-Resources/Tools/example-tool.md",
                body="# Example tool\n",
                front_matter={"source": note["front_matter"]["source"], "category": "tool"},
            )
            assert not err
            err, _ = await call(
                c, "append_note", path="3-Resources/Tools/example-tool.md", text="\n- reviewed\n"
            )
            assert not err
            err, _ = await call(
                c, "move_note", src="0-INBOX/example-tool.md", dst="3-Resources/Tools/original-capture.md"
            )
            # write_note allows Tools, move_note default allows PARA -> ok
            assert not err
            return await call(c, "read_note", path="3-Resources/Tools/example-tool.md")

    err, note = run(go())
    assert not err and note["front_matter"] == {"source": "https://example.invalid/tool", "category": "tool"}
    assert note["body"] == "# Example tool\n\n- reviewed\n"
    assert not (synthetic_vault / "0-INBOX/example-tool.md").exists()


@pytest.mark.parametrize(
    "tool,args",
    [
        ("read_note", lambda p: {"path": p}),
        ("write_note", lambda p: {"path": p, "body": "x"}),
        ("append_note", lambda p: {"path": p, "text": "x"}),
        ("list_folder", lambda p: {"path": p}),
        ("move_note", lambda p: {"src": "0-INBOX/example-tool.md", "dst": p}),
        ("move_note", lambda p: {"src": p, "dst": "0-INBOX/x.md"}),
    ],
)
def test_traversal_attacks_rejected_over_mcp_and_nothing_escapes(
    config, synthetic_vault, tmp_path, tool, args
):
    """SC-002 / FR-002: every traversal path is a tool error and no file appears outside the vault."""
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())

    async def go():
        async with stdio_client(config) as c:
            return [await call(c, tool, **args(p)) for p in TRAVERSAL]

    for err, msg in run(go()):
        assert err, msg
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert after == before
    assert (synthetic_vault / "0-INBOX/example-tool.md").exists()


def test_per_tool_allowlist_enforced_over_mcp(config, synthetic_vault):
    """FR-002: write_note is limited to the configured folders even though read_note may read PARA."""

    async def go():
        async with stdio_client(config) as c:
            return (
                await call(c, "write_note", path="3-Resources/Learning/x.md", body="x"),
                await call(
                    c, "read_note", path="3-Resources/Learning/../Tools/../../0-INBOX/example-tool.md"
                ),
                await call(c, "write_note", path="3-Resources/Tools/ok.md", body="x"),
            )

    denied, dotdot, ok = run(go())
    assert denied[0] and "allowlist" in denied[1]
    assert dotdot[0]
    assert not ok[0]
    assert not (synthetic_vault / "3-Resources/Learning/x.md").exists()


def test_symlink_pointing_outside_vault_is_rejected_over_mcp(config, synthetic_vault, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("TOP SECRET")
    os.symlink(outside, synthetic_vault / "0-INBOX" / "innocent")

    async def go():
        async with stdio_client(config) as c:
            return (
                await call(c, "read_note", path="0-INBOX/innocent/secret.md"),
                await call(c, "write_note", path="0-INBOX/innocent/new.md", body="x"),
            )

    for err, msg in run(go()):
        assert err and "TOP SECRET" not in str(msg)
    assert not (outside / "new.md").exists()


def test_triage_and_processing_list_over_mcp(config, synthetic_vault):
    """US2: structured rows through the MCP boundary; owner edits survive."""
    triage = synthetic_vault / "0-INBOX/Inbox-Triage.md"

    async def go():
        async with stdio_client(config) as c:
            err, res = await call(
                c,
                "update_triage",
                rows=[
                    {"resource": "https://example.invalid/tool", "ai_suggested_category": "tool"},
                    {"resource": "https://example.invalid/watch", "ai_suggested_category": "learning"},
                ],
            )
            assert not err and len(res["inserted"]) == 2
            # the owner ticks one box and overrides the other in Obsidian
            text = triage.read_text()
            lines = text.splitlines()
            i = next(n for n, ln in enumerate(lines) if "example.invalid/tool" in ln)
            lines[i] = lines[i].replace("| [ ] |", "| [x] |", 1)
            j = next(n for n, ln in enumerate(lines) if "example.invalid/watch" in ln)
            lines[j] = lines[j].replace("|  | suggested |", "| misc | suggested |")
            triage.write_text("\n".join(lines) + "\n")
            err, res = await call(
                c, "update_triage", rows=[{"resource": "https://example.invalid/tool", "state": "released"}]
            )
            assert not err
            err, tri = await call(c, "read_triage")
            assert not err
            err, bad = await call(c, "update_triage", rows=[{"item_id": "nope", "state": "bogus"}])
            assert err
            err, pl = await call(
                c,
                "upsert_processing_list",
                rows=[
                    {
                        "item_id": tri["rows"][0]["item_id"],
                        "category": "tool",
                        "obsidian_link": "[[example-tool]]",
                    }
                ],
            )
            assert not err and len(pl["inserted"]) == 1
            return tri

    tri = run(go())
    by_res = {r["resource"]: r for r in tri["rows"]}
    tool, watch = by_res["https://example.invalid/tool"], by_res["https://example.invalid/watch"]
    assert tool["confirm"] is True and tool["state"] == "released"
    assert watch["override"] == "misc" and watch["state"] == "suggested"
    assert "Manual-Resource-Processing-List.md" in os.listdir(synthetic_vault / "0-INBOX")


def test_agent_cannot_confirm_override_or_release_over_mcp(config, synthetic_vault):
    """Constitution VI over MCP: owner fields and unconfirmed releases are refused, file untouched."""
    triage = synthetic_vault / "0-INBOX/Inbox-Triage.md"

    async def go():
        async with stdio_client(config) as c:
            err, res = await call(
                c,
                "update_triage",
                rows=[{"resource": "https://example.invalid/x", "ai_suggested_category": "tool"}],
            )
            assert not err
            iid = res["inserted"][0]
            before = triage.read_text()
            results = [
                await call(c, "update_triage", rows=[{"item_id": iid, "confirm": True}]),
                await call(c, "update_triage", rows=[{"item_id": iid, "override": "learning"}]),
                await call(c, "update_triage", rows=[{"item_id": iid, "state": "released"}]),
            ]
            return before, results

    before, results = run(go())
    assert all(err for err, _ in results)
    assert triage.read_text() == before


def test_generic_tools_cannot_touch_managed_files_over_mcp(config, synthetic_vault):
    async def go():
        async with stdio_client(config) as c:
            await call(c, "update_triage", rows=[{"resource": "r", "ai_suggested_category": "misc"}])
            return (
                await call(c, "write_note", path="0-INBOX/Inbox-Triage.md", body="wiped", overwrite=True),
                await call(c, "append_note", path="0-INBOX/Inbox-Triage.md", text="junk"),
            )

    for err, _ in run(go()):
        assert err
    assert "wiped" not in (synthetic_vault / "0-INBOX/Inbox-Triage.md").read_text()


def test_conflict_files_reported_over_mcp(config, synthetic_vault):
    """Edge case: Sync conflict files are ignored in listings and reported."""
    (synthetic_vault / "0-INBOX/example-tool (conflict).md").write_text("c")
    (synthetic_vault / "0-INBOX/Inbox-Triage (conflict).md").write_text("c")

    async def go():
        async with stdio_client(config) as c:
            _, listing = await call(c, "list_folder", path="0-INBOX")
            _, tri = await call(c, "read_triage")
            return listing, tri

    listing, tri = run(go())
    assert "example-tool (conflict).md" not in listing["notes"]
    assert set(listing["conflicts"]) == {"example-tool (conflict).md", "Inbox-Triage (conflict).md"}
    assert tri["conflicts"] == ["Inbox-Triage (conflict).md"]


def test_concurrent_client_calls_do_not_corrupt_triage_file(config, synthetic_vault):
    """FR-003: parallel update_triage calls from several clients all land."""

    async def one(i):
        async with stdio_client(config) as c:
            return await call(
                c,
                "update_triage",
                rows=[{"resource": f"https://e.invalid/{i}", "ai_suggested_category": "tool"}],
            )

    async def go():
        return await asyncio.gather(*[one(i) for i in range(6)])

    assert all(not err for err, _ in run(go()))

    async def read():
        async with stdio_client(config) as c:
            return (await call(c, "read_triage"))[1]

    rows = run(read())["rows"]
    assert len(rows) == 6 and len({r["item_id"] for r in rows}) == 6
