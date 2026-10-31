"""Twenty synthetic Inbox-Triage.md variants as the owner might leave them after editing in Obsidian."""

from __future__ import annotations

HEADER = "| confirm | item | resource | suggested | override | state |"
SEP = "| --- | --- | --- | --- | --- | --- |"


def row(mark: str, item: str, resource: str, cat: str, override: str = "", state: str = "suggested") -> str:
    return f"| {mark} | {item} | {resource} | {cat} | {override} | {state} |"


def triage_fixtures() -> dict[str, str]:
    base_rows = [
        row("[ ]", "a1", "https://example.invalid/a", "tool"),
        row("[x]", "b2", "https://example.invalid/b", "learning"),
        row("[ ]", "c3", "plain text idea", "misc", "marketing", "pending"),
    ]
    table = "\n".join([HEADER, SEP, *base_rows]) + "\n"
    f: dict[str, str] = {}
    f["01-basic"] = f"# Inbox triage\n\n{table}"
    f["02-no-trailing-newline"] = f"# T\n\n{table}".rstrip("\n")
    f["03-crlf"] = f"# T\r\n\r\n{table}".replace("\n", "\r\n").replace("\r\r\n", "\r\n")
    f["04-front-matter"] = f"---\npkpipe: triage\ntags: [a, b]\n---\n# T\n{table}"
    f["05-prose-after"] = f"# T\n{table}\nNotes the owner typed below the table.\n- bullet\n"
    f["06-escaped-pipes"] = f"{HEADER}\n{SEP}\n{row('[ ]', 'p1', 'https://e.invalid/?a=1\\|b', 'tool')}\n"
    f["07-uppercase-x"] = f"{HEADER}\n{SEP}\n{row('[X]', 'u1', 'r', 'tool', '', 'pending')}\n"
    f["08-ragged-spacing"] = (
        "|confirm|item|resource|suggested|override|state|\n|---|---|---|---|---|---|\n"
        "|[ ]|r1|x|tool||suggested|\n"
    )
    f["09-unicode"] = (
        f"# Rézumé 📚\n\n{HEADER}\n{SEP}\n{row('[ ]', 'ü1', 'https://e.invalid/日本語', 'learning')}\n"
    )
    f["10-other-table-first"] = f"| a | b |\n| - | - |\n| 1 | 2 |\n\n{table}"
    f["11-other-table-after"] = f"{table}\n| x | y |\n| - | - |\n| 1 | 2 |\n"
    f["12-malformed-row"] = (
        f"{HEADER}\n{SEP}\n{row('[ ]', 'm1', 'r', 'tool')}\n| only | two |\n"
        f"{row('[ ]', 'm2', 'r2', 'tool')}\n"
    )
    f["13-bad-state-word"] = f"{HEADER}\n{SEP}\n{row('[ ]', 'b1', 'r', 'tool', '', 'weird')}\n"
    f["14-empty-table"] = f"# T\n\n{HEADER}\n{SEP}\n"
    f["15-no-table"] = "# Inbox triage\n\nNothing captured yet.\n"
    f["16-empty-file"] = ""
    f["17-blank-lines-inside-doc"] = f"\n\n# T\n\n\n{table}\n\n\n"
    f["18-html-comment"] = f"<!-- managed by pkpipe -->\n{table}"
    f["19-trailing-spaces"] = f"{HEADER}  \n{SEP}  \n{row('[ ]', 't1', 'r', 'tool')}   \n"
    f["20-many-rows"] = (
        "\n".join([HEADER, SEP, *[row("[ ]", f"i{n}", f"https://e.invalid/{n}", "tool") for n in range(60)]])
        + "\n"
    )
    assert len(f) == 20
    return f
