"""Spec 026: schema-driven config forms (server side, no JavaScript framework).

The config files are validated by Pydantic models; their JSON Schema (``pkpipe schema``) is the single
source of the forms. :func:`build_form` turns a schema plus the current JSON value into a tree of *field
nodes* that the templates render; :func:`parse_form` turns the posted flat form back into JSON and
the per-field errors.

Form field names are paths: ``f.<property>.<property>``; array items and map rows carry their row number
(``f.categories.0.sessions.1.id``), a map row has its key in ``<row>.__k``. Because the names are built from
schema property names and row numbers only, nothing the owner types ever becomes a name.

A new config model in ``CONFIG_MODELS`` (or a new nested field of an existing one) gets its form without code:
objects, maps, arrays of objects, arrays of scalars, enums, booleans, numbers, strings and nullable variants
are rendered from the schema; anything else falls back to a JSON text field of the same file.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pkpipe.engine.config_models import CONFIG_MODELS, config_json_schemas

PREFIX = "f"
KEY = "__k"
#: A value that must be a reference to a secret, never the secret itself (constitution V, spec 026 FR-006).
ENV_REF = re.compile(r"^env:[A-Za-z_][A-Za-z0-9_]*$")
#: Property names of ``channels.json`` whose value is a secret (or a list of secrets).
SECRET_FIELDS = frozenset({"url", "urls", "token", "password", "secret", "api_key"})
#: Lists whose entries are merged by this key between the pack and the overlay layer.
MERGE_KEYS = {"categories": "id", "schedules": "flow"}
#: ``instruction`` / ``system`` style properties that point to an editable text asset.
ASSET_FIELDS = frozenset({"instruction", "system", "context"})
#: Property name -> picker list (flows, categories) for the fields that name one of them.
DEFAULT_HINTS = {"flow": "flows", "subflow": "flows", "tool_category": "categories"}
#: Events the pack sends notifications for (the picker also offers every event already routed in the config).
KNOWN_EVENTS = ("triage_ready", "daily_summary", "security_flag", "weekly_review", "context_review")
INSTRUCTION_TOKEN_BUDGET = 800  # design rules 4.2: the instruction of a session is about 800 tokens at most


class FormError(ValueError):
    """The path or the posted form cannot be handled."""


@dataclass
class Node:
    """One field of a form. ``kind`` selects the template macro."""

    kind: str  # string text number integer boolean enum lines json object array map
    name: str
    label: str
    path: tuple[Any, ...]
    help: str = ""
    value: Any = None
    required: bool = False
    nullable: bool = False
    default: Any = None
    has_default: bool = False
    enum: list[Any] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    children: list[Node] = field(default_factory=list)  # object properties, or the rows of an array / map
    template: Node | None = None  # an empty row, for the "add" button
    key: str | None = None  # map rows: the current key
    error: str | None = None
    hint: str | None = None  # name of a picker list (flows, categories, events, channels, env)
    secret: bool = False
    present: bool = True
    widget: str | None = None  # cron, asset, ...
    row: int | None = None  # index inside the parent array / map
    last: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


# -- schema helpers --------------------------------------------------------------------------------
def schema_for(stem: str) -> dict[str, Any]:
    schemas = config_json_schemas()
    if stem not in CONFIG_MODELS or stem not in schemas:
        raise FormError(f"'{stem}' has no form")
    return schemas[stem]


def stem_of(path: str) -> str | None:
    """``categories`` for ``categories.json``; ``None`` for anything that is not a config section."""
    if "/" in path or not path.endswith(".json"):
        return None
    stem = path[: -len(".json")]
    return stem if stem in CONFIG_MODELS else None


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    seen = 0
    while "$ref" in schema and seen < 20:
        name = schema["$ref"].rsplit("/", 1)[-1]
        extra = {k: v for k, v in schema.items() if k != "$ref"}
        schema = {**root.get("$defs", {}).get(name, {}), **extra}
        seen += 1
    return schema


def _unwrap(schema: dict[str, Any], root: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """``(inner schema, nullable)``: ``anyOf [X, null]`` is X that may be absent."""
    schema = _resolve(schema, root)
    options = schema.get("anyOf")
    if not options:
        return schema, False
    inner = [o for o in options if _resolve(o, root).get("type") != "null"]
    nullable = len(inner) != len(options)
    if len(inner) == 1:
        merged = {**{k: v for k, v in schema.items() if k != "anyOf"}, **_resolve(inner[0], root)}
        return merged, nullable
    return {"_union": True, **{k: v for k, v in schema.items() if k != "anyOf"}}, nullable


def _label(name: str) -> str:
    return name.replace("_", " ")


def _fname(path: tuple[Any, ...]) -> str:
    return ".".join([PREFIX, *map(str, path)])


# -- building --------------------------------------------------------------------------------------
def build_form(stem: str, data: Any, hints: Mapping[str, str] | None = None) -> Node:
    """The field tree of config section ``stem`` filled with ``data`` (the parsed JSON of its file)."""
    root = schema_for(stem)
    node = _build(
        root, root, "", (), data, required=True, in_stem=stem, hints=DEFAULT_HINTS if hints is None else hints
    )
    node.label = stem
    if stem == "channels":
        _hide_literals(node)
    if stem == "channels":  # routes: event name -> list of channel names
        for child in node.children:
            if child.name.endswith(".routes") and child.kind == "map":
                child.extra["key_hint"] = "events"
                for row in [*child.children, child.template]:
                    if row is not None:
                        row.hint = "channels"
    return node


def _hide_literals(node: Node) -> None:
    """A secret field never shows a literal value (only ``env:NAME`` references are displayed)."""
    if (
        node.secret
        and node.kind == "string"
        and isinstance(node.value, str)
        and not ENV_REF.match(node.value)
    ):
        if node.value:
            node.extra["literal"] = True
        node.value = ""
    if node.secret and node.kind == "lines" and isinstance(node.value, str):
        keep = [ln for ln in node.value.splitlines() if ENV_REF.match(ln.strip())]
        if len(keep) != len([ln for ln in node.value.splitlines() if ln.strip()]):
            node.extra["literal"] = True
        node.value = "\n".join(keep)
    for c in node.children:
        _hide_literals(c)


def _scalar_kind(schema: dict[str, Any]) -> str:
    if "enum" in schema:
        return "enum"
    t = schema.get("type")
    return {"string": "string", "integer": "integer", "number": "number", "boolean": "boolean"}.get(
        str(t), "json"
    )


def _build(
    schema: dict[str, Any],
    root: dict[str, Any],
    name: str,
    path: tuple[Any, ...],
    value: Any,
    *,
    required: bool,
    in_stem: str,
    hints: Mapping[str, str],
) -> Node:
    schema, nullable = _unwrap(schema, root)
    base: dict[str, Any] = {
        "name": _fname(path),
        "label": _label(name),
        "path": path,
        "help": str(schema.get("description") or ""),
        "required": required,
        "nullable": nullable,
        "has_default": "default" in schema,
        "default": schema.get("default"),
        "minimum": schema.get("minimum", schema.get("exclusiveMinimum")),
        "maximum": schema.get("maximum", schema.get("exclusiveMaximum")),
        "present": value is not None,
        "hint": hints.get(name) if name in hints else None,
    }
    if schema.get("_union"):
        return Node(kind="json", value=_dump(value), **base)
    t = schema.get("type")
    if t == "object" and schema.get("properties"):
        node = Node(kind="object", value=value, **base)
        data = value if isinstance(value, dict) else {}
        req = set(schema.get("required") or [])
        for prop, sub in schema["properties"].items():
            child = _build(
                sub,
                root,
                prop,
                (*path, prop),
                data.get(prop),
                required=prop in req,
                in_stem=in_stem,
                hints=hints,
            )
            _mark(child, in_stem, prop)
            node.children.append(child)
        return node
    if t == "object" and isinstance(schema.get("additionalProperties"), dict):
        node = Node(kind="map", value=value, **base)
        sub = schema["additionalProperties"]
        data = value if isinstance(value, dict) else {}
        for i, (k, v) in enumerate(data.items()):
            row = _build(sub, root, str(k), (*path, i), v, required=True, in_stem=in_stem, hints=hints)
            row.key, row.row = str(k), i
            row.extra["key_name"] = _fname((*path, i, KEY))
            node.children.append(row)
        node.template = _build(
            sub, root, "new", (*path, "{i}"), None, required=True, in_stem=in_stem, hints=hints
        )
        node.template.row = None
        node.template.extra["key_name"] = _fname((*path, "{i}", KEY))
        _flag_last(node)
        return node
    if t == "array":
        items = schema.get("items") or {}
        inner, _ = _unwrap(items, root)
        rows = value if isinstance(value, list) else []
        if inner.get("type") in ("string", "integer", "number") and "enum" not in inner:
            node = Node(kind="lines", value="\n".join(str(v) for v in rows), **base)
            node.extra["item_type"] = inner.get("type")
            return node
        if inner.get("type") == "object" and inner.get("properties"):
            node = Node(kind="array", value=value, **base)
            for i, v in enumerate(rows):
                row = _build(
                    items, root, f"#{i + 1}", (*path, i), v, required=True, in_stem=in_stem, hints=hints
                )
                row.row = i
                node.children.append(row)
            node.template = _build(
                items, root, "new", (*path, "{i}"), None, required=True, in_stem=in_stem, hints=hints
            )
            _flag_last(node)
            return node
        return Node(kind="json", value=_dump(value), **base)
    kind = _scalar_kind(schema)
    node = Node(kind=kind, value=value, **base)
    if kind == "enum":
        node.enum = list(schema["enum"])
    if kind == "string" and (schema.get("maxLength", 0) > 300 or name in ("description", "triage_hint")):
        node.kind = "text"
    if kind == "json":
        node.value = _dump(value)
    _mark(node, in_stem, name)
    return node


def _dump(value: Any) -> str:
    return "" if value is None else json.dumps(value, indent=2, ensure_ascii=False)


def _flag_last(node: Node) -> None:
    for i, c in enumerate(node.children):
        c.last = i == len(node.children) - 1


def _mark(node: Node, stem: str, prop: str) -> None:
    """Widgets and secret handling by (section, property)."""
    if stem == "channels" and prop in SECRET_FIELDS:
        node.secret = True
    if stem == "schedules" and prop == "cron":
        node.widget = "cron"
    if prop in ASSET_FIELDS and node.kind == "string":
        node.widget = "asset"


# -- parsing ---------------------------------------------------------------------------------------
@dataclass
class Parsed:
    data: Any
    errors: dict[str, str]  # form name -> message
    pathmap: dict[tuple[Any, ...], str]  # data path (keys, indexes) -> form name


def flat_of(items: list[tuple[str, str]]) -> dict[str, str]:
    """The posted fields below ``f.``; a later value wins (the hidden ``false`` before a checkbox)."""
    out: dict[str, str] = {}
    for k, v in items:
        if k == PREFIX or k.startswith(PREFIX + "."):
            out[k] = v
    return out


def parse_form(stem: str, flat: Mapping[str, str]) -> Parsed:
    """The JSON value described by the posted form, with every field error found on the way."""
    root = schema_for(stem)
    errors: dict[str, str] = {}
    pathmap: dict[tuple[Any, ...], str] = {}
    value = _parse(root, root, (), (), flat, errors, pathmap, required=True, stem=stem)
    return Parsed(value if value is not _ABSENT else None, errors, pathmap)


class _Absent:
    def __repr__(self) -> str:  # pragma: no cover
        return "<absent>"


_ABSENT = _Absent()


def _rows(prefix: str, flat: Mapping[str, str]) -> list[int]:
    found: set[int] = set()
    start = prefix + "."
    for k in flat:
        if k.startswith(start):
            head = k[len(start) :].split(".", 1)[0]
            if head.isdigit():
                found.add(int(head))
    return sorted(found)


def _parse(
    schema: dict[str, Any],
    root: dict[str, Any],
    path: tuple[Any, ...],
    dpath: tuple[Any, ...],
    flat: Mapping[str, str],
    errors: dict[str, str],
    pathmap: dict[tuple[Any, ...], str],
    *,
    required: bool,
    stem: str,
) -> Any:
    schema, nullable = _unwrap(schema, root)
    name = _fname(path)
    pathmap[dpath] = name
    t = schema.get("type")
    if schema.get("_union"):
        return _parse_json(name, flat.get(name, ""), errors, required)
    if t == "object" and schema.get("properties"):
        out: dict[str, Any] = {}
        req = set(schema.get("required") or [])
        for prop, sub in schema["properties"].items():
            v = _parse(
                sub,
                root,
                (*path, prop),
                (*dpath, prop),
                flat,
                errors,
                pathmap,
                required=prop in req,
                stem=stem,
            )
            if v is not _ABSENT:
                out[prop] = v
        return out if out or required else _ABSENT
    if t == "object" and isinstance(schema.get("additionalProperties"), dict):
        out = {}
        for i in _rows(name, flat):
            key = (flat.get(_fname((*path, i, KEY))) or "").strip()
            row_name = _fname((*path, i))
            if not key:
                if any(k.startswith(row_name + ".") and flat[k].strip() for k in flat if not k.endswith(KEY)):
                    errors[_fname((*path, i, KEY))] = "a name is required"
                continue
            if key in out:
                errors[_fname((*path, i, KEY))] = f"'{key}' is used twice"
                continue
            v = _parse(
                schema["additionalProperties"],
                root,
                (*path, i),
                (*dpath, key),
                flat,
                errors,
                pathmap,
                required=True,
                stem=stem,
            )
            out[key] = {} if v is _ABSENT else v
        return out if out or required else _ABSENT
    if t == "array":
        inner, _ = _unwrap(schema.get("items") or {}, root)
        if inner.get("type") in ("string", "integer", "number") and "enum" not in inner:
            lines = [ln.strip() for ln in (flat.get(name) or "").splitlines() if ln.strip()]
            if not lines and not required:
                return _ABSENT
            items: list[Any] = []
            for ln in lines:
                if inner["type"] == "string":
                    items.append(ln)
                    continue
                try:
                    items.append(int(ln) if inner["type"] == "integer" else float(ln))
                except ValueError:
                    errors[name] = f"'{ln}' is not a number"
            if stem == "channels" and _fname(path).rsplit(".", 1)[-1] in SECRET_FIELDS:
                for ln in lines:
                    if not ENV_REF.match(ln):
                        errors[name] = "only env:NAME references are accepted here, never the secret itself"
            return items
        if inner.get("type") == "object" and inner.get("properties"):
            out_list: list[Any] = []
            for n, i in enumerate(_rows(name, flat)):
                v = _parse(
                    schema["items"],
                    root,
                    (*path, i),
                    (*dpath, n),
                    flat,
                    errors,
                    pathmap,
                    required=True,
                    stem=stem,
                )
                out_list.append({} if v is _ABSENT else v)
            return out_list if out_list or required else _ABSENT
        return _parse_json(name, flat.get(name, ""), errors, required)
    raw = flat.get(name)
    kind = _scalar_kind(schema)
    if (
        raw is None
        or (raw.strip() == "" and kind != "string")
        or (kind == "string" and raw == "" and not required)
    ):
        if required and "default" not in schema and name in flat:
            errors[name] = "required"
        return _ABSENT
    if kind == "boolean":
        if raw in ("", "default"):
            return _ABSENT
        if raw not in ("true", "false"):
            errors[name] = "true or false"
            return _ABSENT
        return raw == "true"
    if kind in ("integer", "number"):
        try:
            num = int(raw) if kind == "integer" else float(raw)
        except ValueError:
            errors[name] = "a whole number is required" if kind == "integer" else "a number is required"
            return _ABSENT
        return num
    if kind == "enum":
        if raw == "":
            return _ABSENT
        for option in schema["enum"]:
            if str(option) == raw:
                return option
        errors[name] = f"one of: {', '.join(map(str, schema['enum']))}"
        return _ABSENT
    if kind == "json":
        return _parse_json(name, raw, errors, required)
    if kind == "string" and raw.strip() == "" and required and "default" not in schema:
        errors[name] = "required"
        return _ABSENT
    if stem == "channels" and path and str(path[-1]) in SECRET_FIELDS and raw and not ENV_REF.match(raw):
        errors[name] = "only an env:NAME reference is accepted here, never the secret itself"
    return raw


def _parse_json(name: str, raw: str, errors: dict[str, str], required: bool) -> Any:
    if not raw.strip():
        return _ABSENT
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        errors[name] = f"not valid JSON ({exc.msg}, line {exc.lineno})"
        return _ABSENT


# -- validation with per-field messages ------------------------------------------------------------
def validate_parsed(stem: str, parsed: Parsed, base: Any = None) -> dict[str, str]:
    """Model validation of the parsed value; the messages are attached to the form fields.

    An overlay file is a *layer*: it may name only what it changes. It is therefore validated merged onto
    ``base`` (the pack's file of the same section) with the merge the registry uses, so a route that points to
    a channel of the pack is fine. A problem in an entry that lives only in the pack is reported at the form.
    """
    from pydantic import ValidationError

    from pkpipe.engine.registry import merge_layers

    out = dict(parsed.errors)
    if out:
        return out
    value = parsed.data
    if base is not None:
        value = merge_layers({stem: base}, {stem: parsed.data}).get(stem, parsed.data)
    key = MERGE_KEYS.get(stem)
    try:
        CONFIG_MODELS[stem].model_validate(value)
    except ValidationError as exc:
        for err in exc.errors():
            loc = tuple(err["loc"])
            name = None
            if (
                key
                and loc
                and isinstance(loc[0], int)
                and isinstance(value, list)
                and isinstance(parsed.data, list)
            ):
                entry = value[loc[0]] if loc[0] < len(value) else {}
                ident = entry.get(key) if isinstance(entry, dict) else None
                rows = [i for i, e in enumerate(parsed.data) if isinstance(e, dict) and e.get(key) == ident]
                loc = (rows[0], *loc[1:]) if rows else ()
                if not rows:
                    out.setdefault(_fname(()), f"{stem} '{ident}': {err['msg']}")
                    continue
            name = _field_for(loc, parsed.pathmap) or _fname(())
            out.setdefault(name, err["msg"])
    return out


def _field_for(loc: tuple[Any, ...], pathmap: Mapping[tuple[Any, ...], str]) -> str | None:
    while True:
        if loc in pathmap:
            return pathmap[loc]
        if not loc:
            return None
        loc = loc[:-1]


def apply_errors(node: Node, errors: Mapping[str, str]) -> None:
    node.error = errors.get(node.name)
    if node.kind == "map":
        for c in node.children:
            c.extra["key_error"] = errors.get(c.extra.get("key_name", ""))
    for c in node.children:
        apply_errors(c, errors)


# -- row actions (add / delete / move) without JavaScript ------------------------------------------
def apply_action(stem: str, flat: Mapping[str, str], act: str) -> dict[str, str]:
    """The posted form after ``add:<path>``, ``del:<path>:<row>``,
    ``up:<path>:<row>`` or ``down:<path>:<row>``.

    Works on the *flat* form: rows are renumbered, so a half-filled form survives the action. Nothing is
    validated here; the owner may add a row and fill it in.
    """
    kind, _, rest = act.partition(":")
    if kind not in ("add", "del", "up", "down"):
        raise FormError("unknown action")
    path, _, row_s = rest.partition(":")
    if not re.fullmatch(r"f(\.[A-Za-z_][A-Za-z0-9_]*|\.\d+)*", path):
        raise FormError("invalid field path")
    rows = _rows(path, flat)
    order = list(rows)
    if kind == "add":
        order.append((rows[-1] + 1) if rows else 0)
    else:
        if not row_s.isdigit() or int(row_s) not in rows:
            raise FormError("unknown row")
        i = order.index(int(row_s))
        if kind == "del":
            order.pop(i)
        elif kind == "up" and i > 0:
            order[i - 1], order[i] = order[i], order[i - 1]
        elif kind == "down" and i < len(order) - 1:
            order[i + 1], order[i] = order[i], order[i + 1]
    start = path + "."
    rest_items = {k: v for k, v in flat.items() if not k.startswith(start)}
    out = dict(rest_items)
    for new, old in enumerate(order):
        for k, v in flat.items():
            if k.startswith(f"{start}{old}.") or k == f"{start}{old}":
                out[f"{start}{new}" + k[len(f"{start}{old}") :]] = v
    # a fresh row has no posted field: give an array row one marker so it exists after parsing
    if kind == "add":
        out[f"{start}{len(order) - 1}.{KEY}"] = out.get(f"{start}{len(order) - 1}.{KEY}", "")
    return out


# -- minimal overlay files -------------------------------------------------------------------------
def minimize(stem: str, pack_value: Any, value: Any) -> Any:
    """What an overlay file needs to hold so that pack + overlay equals ``value``.

    Used when a section has no overlay file yet: the form shows the effective (pack) values, and saving it
    must not copy the whole pack file into the overlay (it would shadow later pack updates). Lists merged by
    key keep only the entries that differ; dicts keep only the keys that differ; everything else is kept
    whole.
    A pack entry cannot be removed this way: set ``enabled: false`` instead.
    """
    key = MERGE_KEYS.get(stem)
    if key and isinstance(value, list) and isinstance(pack_value, list):
        base = {e.get(key): e for e in pack_value if isinstance(e, dict)}
        return [e for e in value if not (isinstance(e, dict) and base.get(e.get(key)) == e)]
    if isinstance(value, dict) and isinstance(pack_value, dict):
        return _dict_diff(pack_value, value)
    return value


def _dict_diff(base: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in value.items():
        if k not in base:
            out[k] = v
        elif isinstance(v, dict) and isinstance(base[k], dict):
            sub = _dict_diff(base[k], v)
            if sub:
                out[k] = sub
        elif v != base[k]:
            out[k] = v
    return out


def dumps(value: Any) -> str:
    """The text written to the draft: 2-space JSON, UTF-8 characters kept, one trailing newline."""
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


# -- literal secrets (also guards the raw editor) --------------------------------------------------
def literal_secrets(stem: str, data: Any) -> list[str]:
    """Paths of ``channels.json`` fields that hold a literal secret instead of an ``env:NAME`` reference."""
    if stem != "channels" or not isinstance(data, dict):
        return []
    bad: list[str] = []
    for cname, ch in (data.get("channels") or {}).items():
        if not isinstance(ch, dict):
            continue
        for prop in SECRET_FIELDS:
            v = ch.get(prop)
            values = v if isinstance(v, list) else [v]
            for item in values:
                if isinstance(item, str) and item and not ENV_REF.match(item):
                    bad.append(f"channels.{cname}.{prop}")
    return bad


def asset_path(prop: str, value: str) -> str:
    """The editable text file a session property points to."""
    if prop == "instruction":
        return f"instructions/{value}"
    if prop == "system":
        return f"prompts/system/{value}.md" if not value.endswith(".md") else f"prompts/system/{value}"
    return f"contexts/{value}.md" if not value.endswith(".md") else f"contexts/{value}"


def token_estimate(text: str) -> int:
    """A rough token count (four characters per token) for the instruction budget hint."""
    return (len(text) + 3) // 4


def instruction_hint(text: str, budget: int = INSTRUCTION_TOKEN_BUDGET) -> dict[str, Any]:
    n = token_estimate(text)
    return {"tokens": n, "budget": budget, "over": n > budget}


def cron_preview(expr: str, tz: str | None = None, count: int = 3) -> dict[str, Any]:
    """The next fire times of a crontab expression, or the reason it is invalid."""
    from datetime import UTC, datetime

    from apscheduler.triggers.cron import CronTrigger

    expr = (expr or "").strip()
    if not expr:
        return {"ok": False, "error": "empty"}
    try:
        trigger = (
            CronTrigger.from_crontab(expr, timezone=tz or None) if tz else CronTrigger.from_crontab(expr)
        )
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": str(exc)[:160]}
    times, prev, now = [], None, datetime.now(UTC)
    for _ in range(count):
        nxt = trigger.get_next_fire_time(prev, prev or now)
        if nxt is None:
            break
        times.append(nxt.strftime("%a %Y-%m-%d %H:%M %Z"))
        prev = nxt
    return {"ok": True, "times": times}
