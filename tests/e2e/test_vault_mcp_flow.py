"""Spec 004 end to end: the documented entry point (mcp_servers/obsidian_vault/server.py) drives the
INBOX -> triage -> release -> PARA flow on a synthetic vault; the domain release rule decides."""

from __future__ import annotations

import asyncio
import sys

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from pkpipe.domain.triage import ItemState, TriageItem, release
from tests.conftest import REPO
from tests.support.vault_mcp import call, write_policy

ENTRY = REPO / "mcp_servers" / "obsidian_vault" / "server.py"


def test_inbox_to_para_flow_through_the_entry_point(tmp_path, synthetic_vault):
    """US1 + US2: agent proposes triage, owner confirms in the file, code releases, notes land in PARA."""
    cfg = write_policy(tmp_path, synthetic_vault)
    triage_file = synthetic_vault / "0-INBOX" / "Inbox-Triage.md"
    transport = StdioTransport(sys.executable, [str(ENTRY), "--config", str(cfg), "--transport", "stdio"])

    async def flow():
        async with Client(transport) as c:
            # 1. agent reads the INBOX and proposes categories
            _, listing = await call(c, "list_folder", path="0-INBOX")
            proposals = {"example-tool.md": "tool", "example-video.md": "learning"}
            rows = []
            for name in listing["notes"]:
                _, note = await call(c, "read_note", path=f"0-INBOX/{name}")
                rows.append(
                    {"resource": note["front_matter"]["source"], "ai_suggested_category": proposals[name]}
                )
            err, _ = await call(c, "update_triage", rows=rows)
            assert not err

            # 2. owner confirms the tool row and overrides nothing else (edit in Obsidian = plain file edit)
            text = triage_file.read_text()
            triage_file.write_text(
                "\n".join(
                    ln.replace("| [ ] |", "| [x] |", 1) if "invalid/tool" in ln else ln
                    for ln in text.splitlines()
                )
                + "\n"
            )

            # 3. 06:00: code applies the release rule to what the tool returns
            _, tri = await call(c, "read_triage")
            items = [
                TriageItem(
                    r["item_id"],
                    r["resource"],
                    r["ai_suggested_category"],
                    r["confirm"],
                    r["override"],
                    ItemState(r["state"]),
                )
                for r in tri["rows"]
            ]
            released, pending = release(items)
            assert [i.ai_suggested_category for i in released] == ["tool"]
            err, _ = await call(
                c,
                "update_triage",
                rows=[{"item_id": i.id, "state": i.state.value} for i in released + pending],
            )
            assert not err

            # 4. the released item becomes a curated note in PARA; the capture is archived beside it
            item = released[0]
            err, _ = await call(
                c,
                "write_note",
                path="3-Resources/Tools/example-tool.md",
                body="# Example tool\n",
                front_matter={"source": item.resource, "category": item.effective_category},
            )
            assert not err
            err, _ = await call(
                c, "move_note", src="0-INBOX/example-tool.md", dst="3-Resources/Tools/capture-example-tool.md"
            )
            assert not err
            err, _ = await call(
                c,
                "upsert_processing_list",
                rows=[
                    {
                        "item_id": item.id,
                        "category": "tool",
                        "obsidian_link": "[[example-tool]]",
                        "status": "done",
                    }
                ],
            )
            assert not err
            err, _ = await call(c, "update_triage", rows=[{"item_id": item.id, "state": "processed"}])
            assert not err
            return item.id, await call(c, "read_triage")

    item_id, (_, final) = asyncio.run(flow())
    states = {r["item_id"]: r["state"] for r in final["rows"]}
    assert states[item_id] == "processed" and sorted(states.values()) == ["pending", "processed"]
    assert (synthetic_vault / "3-Resources/Tools/example-tool.md").read_text().startswith("---\nsource:")
    assert (synthetic_vault / "3-Resources/Tools/capture-example-tool.md").exists()
    assert not (synthetic_vault / "0-INBOX/example-tool.md").exists()
    assert (synthetic_vault / "0-INBOX/example-video.md").exists()  # unconfirmed item stays in the INBOX
    plist = (synthetic_vault / "0-INBOX/Manual-Resource-Processing-List.md").read_text()
    assert f"| {item_id} | tool | [[example-tool]] |" in plist and plist.rstrip().endswith("| done |")
