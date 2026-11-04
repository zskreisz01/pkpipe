"""Spec 020 SC-002: no vault folder of the default PARA layout is spelled outside the layout defaults."""

from __future__ import annotations

import ast
import json
import re

from tests.conftest import PACK, REPO

SRC = REPO / "src" / "pkpipe"
OLD = re.compile(r"(0-INBOX|1-Projects|2-Areas|3-Resources|4-Archive)")
#: the one place that holds the defaults
ALLOWED = {SRC / "domain" / "layout.py"}


def _docstrings(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def test_no_string_literal_of_the_old_folder_names_in_the_package():
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path in ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstrings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                if OLD.search(node.value):
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}: {node.value[:60]!r}")
    assert not offenders, "vault folders belong in the layout:\n" + "\n".join(offenders)


def test_pack_flows_take_vault_places_from_the_layout():
    """The pack's flows name no vault folder: every place is ``${config.layout.<name>}``."""
    offenders = []
    for flow in sorted((PACK / "flows").glob("*.json")):
        for m in OLD.finditer(flow.read_text(encoding="utf-8")):
            offenders.append(f"{flow.name}: {m.group(0)}")
    assert not offenders, offenders
    layout = json.loads((PACK / "layout.json").read_text(encoding="utf-8"))
    assert layout["inbox_dir"] == "0-INBOX"  # the pack's defaults live in layout.json
