"""Spec 020 SC-002: no vault folder of the default PARA layout is spelled outside the layout defaults."""

from __future__ import annotations

import ast
import json
import re

from tests.conftest import PACK, REPO

SRC = REPO / "src" / "pkpipe"
#: the PARA folder names and every other place of the default layout (spec 020 SC-002)
OLD = re.compile(
    r"(0-INBOX|1-Projects|2-Areas|3-Resources|4-Archive|Agent-Tasks|(?<![A-Za-z0-9])_quarantine|Inbox-Triage"
    r"|Self-Improvement|System-Tool-Candidates|System-Changes|Improvement-Backlog|Manual-Resource"
    r"|Context-Review|Inbox-Archive|\bJournal\b)"
)
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


def _values(node, key=""):
    """String values of a flow, without the prose keys (descriptions and BPMN element labels)."""
    if isinstance(node, str):
        if key not in ("description", "bpmn_element"):
            yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _values(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _values(v, key)


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
        for text in _values(json.loads(flow.read_text(encoding="utf-8"))):
            for m in OLD.finditer(text):
                offenders.append(f"{flow.name}: {m.group(0)}")
    assert not offenders, offenders
    layout = json.loads((PACK / "layout.json").read_text(encoding="utf-8"))
    assert layout["inbox_dir"] == "0-INBOX"  # the pack's defaults live in layout.json


def test_templates_and_pack_config_take_vault_places_from_the_layout():
    """HTML templates and the pack's config files (but ``layout.json``, the defaults) spell no vault folder:
    a category folder is ``{resources_dir}/<name>``."""
    offenders = []
    files = [*sorted((SRC / "api" / "templates").rglob("*")), *sorted(PACK.glob("*.json"))]
    for path in files:
        if not path.is_file() or path.name == "layout.json":
            continue
        for m in OLD.finditer(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(REPO)}: {m.group(0)}")
    assert not offenders, offenders
    categories = json.loads((PACK / "categories.json").read_text(encoding="utf-8"))
    assert all(c["para_path"].startswith("{resources_dir}/") for c in categories)
