"""Flow compiler: flow JSON -> LangGraph ``StateGraph`` (spec 005 US1, T003).

* Every node becomes a LangGraph node that runs its step executor inside a ``step`` span and returns a
  ``Command`` carrying the state update and the next node(s).
* Edges: plain edges are always taken; ``when`` edges of one source form an exclusive gateway (first match,
  else ``default``); ``on: error`` edges catch a failed step (otherwise the run fails, FR edge case).
* ``for_each`` turns the way into a node into a ``Send`` fan-out, one task per element; LangGraph runs at most
  ``max_parallel`` tasks at a time (FR-003). Results are collected as a list under the node id.
* ``subflow`` nodes run another compiled flow for the current item; its checkpoints nest under the run.
* ``wait`` nodes re-enter themselves until done; each attempt is a checkpoint (resumable, SC-002).
"""

from __future__ import annotations

import contextlib
import copy
import os
import threading
from collections.abc import Callable, Mapping
from typing import Annotated, Any, TypedDict

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END as LG_END
from langgraph.graph import START as LG_START
from langgraph.graph import StateGraph
from langgraph.types import Command, Send
from pydantic import ValidationError

from pkpipe.domain.data_classes import PRIVATE_DATA_CLASSES, is_private
from pkpipe.engine.expr import MISSING, condition_paths, evaluate, lookup, references_in
from pkpipe.engine.flow_models import END, START, Flow, FlowNode
from pkpipe.engine.snapshot import ConfigSnapshot
from pkpipe.steps import STEPS, RunInfo, StepContext, StepPorts, StepRegistry, load_plugins

#: Inputs a tool-using session must never receive: raw external text (constitution IV).
RAW_UNTRUSTED_INPUTS = frozenset({"*", "item", "item.body", "item.content", "item.raw", "item.text"})
REFERENCE_ROOTS = frozenset({"item", "input", "run", "error", "config", "result"})


class FlowError(Exception):
    """A flow does not compile: ``errors`` name the flow and node."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class StepFailed(Exception):
    """A step raised and its node has no error edge: the run fails."""

    def __init__(self, flow_id: str, node_id: str, cause: BaseException, *, content_free: bool = False):
        """``content_free``: a flow of a private data class (journal, context, image): the message names the
        type of the cause only, since its text can quote the private content (spec 021 safety review)."""
        self.flow_id, self.node_id, self.cause = flow_id, node_id, cause
        detail = type(cause).__name__ if content_free else f"{type(cause).__name__}: {cause}"
        super().__init__(f"flow '{flow_id}' step '{node_id}' failed: {detail}")


# -- state --------------------------------------------------------------------------------------------------
def merge_dict(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    return {**(a or {}), **(b or {})}


def extend_lists(a: dict[str, list] | None, b: dict[str, list] | None) -> dict[str, list]:
    out = dict(a or {})
    for k, v in (b or {}).items():
        out[k] = list(out.get(k, [])) + list(v)
    return out


def last_value(a: Any, b: Any) -> Any:
    return b


class FlowState(TypedDict, total=False):
    input: dict[str, Any]
    item: Annotated[dict[str, Any], merge_dict]
    outputs: Annotated[dict[str, Any], merge_dict]
    item_outputs: Annotated[dict[str, list[dict[str, Any]]], extend_lists]
    waits: Annotated[dict[str, Any], merge_dict]
    failures: Annotated[dict[str, Any], merge_dict]
    error: Annotated[dict[str, Any] | None, last_value]


def make_resolver(state: dict[str, Any], snapshot: ConfigSnapshot, run: RunInfo) -> Callable[[str], Any]:
    outputs = state.get("outputs") or {}
    item_outputs = state.get("item_outputs") or {}

    def resolver(ref: str) -> Any:
        root, _, rest = ref.partition(".")
        if root == "item":
            base = state.get("item")
        elif root == "input":
            base = state.get("input") or {}
        elif root == "run":
            base = run.as_dict()
        elif root == "error":
            base = state.get("error")
        elif root == "config":
            base = snapshot.data
        elif root in outputs:
            base = outputs[root]
        elif root in item_outputs:
            base = item_outputs[root]
        else:
            return MISSING
        if base is None:
            return MISSING
        return base if not rest else lookup(base if isinstance(base, dict | list) else {}, rest)

    return resolver


def collect_inputs(node: FlowNode, state: dict[str, Any], resolver: Callable[[str], Any]) -> dict[str, Any]:
    """Only the declared inputs (FR-002). ``*`` = every step output so far; unavailable refs are None."""
    out: dict[str, Any] = {}
    for ref in node.inputs:
        if ref == "*":
            out.update(copy.deepcopy(state.get("item_outputs") or {}))
            out.update(copy.deepcopy(state.get("outputs") or {}))
            continue
        value = resolver(ref)
        out[ref] = None if value is MISSING else copy.deepcopy(value)
    return out


def _element_item(element: Any, index: int) -> dict[str, Any]:
    if isinstance(element, dict) and element.get("id") not in (None, ""):
        return copy.deepcopy(element)
    return {"id": str(index), "value": copy.deepcopy(element)}


# -- validation ---------------------------------------------------------------------------------------------
def validate_flow(
    flow: Flow, snapshot: ConfigSnapshot, registry: StepRegistry | None = None
) -> tuple[list[str], list[str]]:
    """Step types, step configs, references and structural rules. Returns (errors, warnings)."""
    registry = registry or _registry()
    errors: list[str] = []
    warnings: list[str] = []
    where = f"flow '{flow.id}'"
    node_ids = {n.id for n in flow.nodes}
    for node in flow.nodes:
        if node.type not in registry:
            errors.append(
                f"{where}: node '{node.id}' has unknown step type '{node.type}' "
                f"(known: {', '.join(registry.types())})"
            )
            continue
        executor = registry.get(node.type)
        try:
            cfg = executor.Config.model_validate(node.config)
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"])
                errors.append(f"{where}: node '{node.id}' config {loc}: {err['msg']}")
            continue
        e, w = executor.check(node, cfg, snapshot)
        e += executor.check_flow(flow, node, cfg)
        errors += [f"{where}: {m}" for m in e]
        warnings += [f"{where}: {m}" for m in w]
        refs = list(node.inputs) + references_in(node.config) + ([node.for_each] if node.for_each else [])
        for ref in refs:
            root = ref.split(".", 1)[0]
            if ref != "*" and root not in REFERENCE_ROOTS and root not in node_ids:
                errors.append(f"{where}: node '{node.id}' references unknown '{ref}'")
        if executor.uses_tools:
            raw = sorted(set(node.inputs) & RAW_UNTRUSTED_INPUTS)
            raw += sorted(
                ref for ref in node.inputs if ref not in raw and _unvalidated_source(flow, ref, registry)
            )
            if raw:
                errors.append(
                    f"{where}: node '{node.id}' uses tools and must not receive raw untrusted input {raw} "
                    "(constitution IV: pass a validated extract instead)"
                )
        if node.for_each and executor.loops:
            errors.append(
                f"{where}: node '{node.id}' ({node.type}) cannot be a for_each target; use a subflow"
            )
        if node.for_each:
            outgoing = flow.edges_from(node.id)
            if any(e.when is not None or e.default or e.on == "error" for e in outgoing):
                errors.append(
                    f"{where}: for_each node '{node.id}' takes only plain edges; route items in a subflow "
                    "and use on_item_error: continue for failures"
                )
    errors += private_data_errors(flow, snapshot)
    for edge in flow.edges:
        for path in condition_paths(edge.when) if edge.when else []:
            root = path.split(".", 1)[0]
            if root not in REFERENCE_ROOTS and root not in node_ids:
                errors.append(
                    f"{where}: condition on edge {edge.source}->{edge.to} references unknown '{path}'"
                )
    return errors, warnings


#: Step types whose output is external data that no schema has validated (tool results, wait poll results,
#: tool-using sessions that saw such results, the output of a category session plan (spec 010: its sessions
#: read web search results and the page extract)), plus the raw stages of secure fetch (spec 008): the fetched
#: page, the sanitized text and the Reader output (which quotes page text as evidence). Sessions with tools
#: take the secure-fetch verdict's ``extract`` (or ``item.extract``) instead.
UNVALIDATED_OUTPUT_TYPES = frozenset(
    {"tool", "wait", "llm_tools", "fetch", "sanitize", "reader", "subflow", "session_plan", "vision"}
)


def _unvalidated_source(flow: Flow, ref: str, registry: StepRegistry) -> bool:
    """Whether ``ref`` reads the raw output of another node (constitution IV: sessions with tools see only
    validated JSON). Validated: ``code`` results and ``llm`` sessions with an ``output_schema``."""
    root = ref.split(".", 1)[0]
    source = next((n for n in flow.nodes if n.id == root), None)
    if source is None or source.type not in registry:
        return False
    if source.type in UNVALIDATED_OUTPUT_TYPES:
        return True
    return source.type == "llm" and not source.config.get("output_schema")


#: Data classes whose flows stay on this machine: local model, no tool that sends data away (spec 015 FR-002
#: for journals, spec 011 for the context packs: the enterprise notes are summarized by the local model only,
#: spec 021 for images: screenshots of messages and profiles).
LOCAL_ONLY_CLASSES = PRIVATE_DATA_CLASSES


#: Step types that are one model session (in a local-only flow their profile and fallbacks must be loopback).
SESSION_TYPES = ("llm", "llm_tools", "reader", "vision")


def _is_loopback(url: str) -> bool:
    import ipaddress
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    try:
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _profile_url(value: str) -> str | None:
    """The literal URL of ``base_url`` (``env:NAME|default`` chains: every alternative must be loopback, so
    the last literal one is taken); ``None`` when it is only an environment lookup without a default."""
    parts = [p for p in value.split("|") if not p.startswith("env:")]
    return parts[-1] if parts else None


def _resolved_url(value: str, environ: Mapping[str, str]) -> str | None:
    """The URL ``base_url`` resolves to in ``environ`` right now (same rules as the model adapter: the first
    set ``env:`` variable, else the literal); ``None`` when nothing resolves."""
    for part in value.split("|"):
        if not part.startswith("env:"):
            return part
        if environ.get(part[4:]):
            return environ[part[4:]]
    return None


def private_data_errors(
    flow: Flow, snapshot: ConfigSnapshot, environ: Mapping[str, str] | None = None
) -> list[str]:
    """Spec 015 FR-002 / constitution VII: a flow handling journal data (or, spec 011, context pack data) is
    local only. Its sessions have no tools and use a model profile (and fallbacks) on a loopback address, its
    tool nodes call no MCP server marked ``egress``, and it only starts subflows of the same data classes (so
    capture stays off).

    Without ``environ`` (config validation) the configured default of each ``env:`` chain is checked. With
    ``environ`` (every run, when it compiles a flow) only the value the chain resolves to right now is
    checked, so an environment variable pointing the profile at another host stops that run before any
    journal text is sent, while the rest of the config still loads."""
    local = LOCAL_ONLY_CLASSES & set(flow.data_classes)
    if not local:
        return []
    label = " and ".join(sorted(local))
    where = f"flow '{flow.id}' ({label} data)"
    errors: list[str] = []
    models = snapshot.data.get("models") or {}
    servers = snapshot.data.get("mcp_servers") or {}
    flows = snapshot.data.get("flows") or {}

    def profile_errors(node_id: str, name: str) -> None:
        seen: set[str] = set()
        while name and name not in seen:
            seen.add(name)
            profile = models.get(name)
            if profile is None:
                return  # reported by the session's own checks
            base_url = str(profile.get("base_url", ""))
            if environ is not None:
                current = _resolved_url(base_url, environ)
                if current is not None and not _is_loopback(current):
                    errors.append(
                        f"{where}: node '{node_id}' uses model profile '{name}' whose base_url resolves "
                        f"to a non-loopback address in this environment; {label} data is processed "
                        "locally only"
                    )
            else:
                url = _profile_url(base_url)
                if url is None or not _is_loopback(url):
                    errors.append(
                        f"{where}: node '{node_id}' uses model profile '{name}' that is not on a loopback "
                        f"address; {label} data is processed locally only"
                    )
            name = profile.get("fallback") or ""

    def nested_sessions(target: str, seen: set[str]) -> list[tuple[str, dict[str, Any]]]:
        """The sessions of a (static) subflow and of the subflows below it: ``(flow.node, config)``."""
        if target in seen or target not in flows:
            return []
        seen.add(target)
        found: list[tuple[str, dict[str, Any]]] = []
        for inner in flows[target].get("nodes") or []:
            inner_cfg = inner.get("config") or {}
            if inner.get("type") in SESSION_TYPES:
                found.append((f"{target}.{inner.get('id')}", inner_cfg))
            sub = inner_cfg.get("flow")
            if inner.get("type") == "subflow" and isinstance(sub, str) and "${" not in sub:
                found += nested_sessions(sub, seen)
        return found

    for node in flow.nodes:
        cfg = node.config
        if node.type in SESSION_TYPES:
            profile_errors(node.id, str(cfg.get("profile", "default")))
        sub_target = cfg.get("flow")
        if (
            environ is not None
            and node.type == "subflow"
            and isinstance(sub_target, str)
            and "${" not in sub_target
        ):
            # a run stops before it starts a subflow whose sessions would reach a remote host
            for where_node, inner_cfg in nested_sessions(sub_target, {flow.id}):
                profile_errors(where_node, str(inner_cfg.get("profile", "default")))
        if environ is not None:
            continue
        if node.type == "llm_tools":
            errors.append(f"{where}: node '{node.id}' is a session with tools; {label} sessions have none")
        tools = [cfg["tool"]] if node.type in ("tool", "wait") and isinstance(cfg.get("tool"), str) else []
        if node.type == "fetch":  # secure fetch (spec 008) always leaves the machine
            tools.append(str(cfg.get("tool") or "webfetch:fetch_page"))
        tools += [
            c.get("tool") for c in cfg.get("checks", []) if node.type == "guard" and isinstance(c, dict)
        ]
        for tool in filter(None, tools):
            server = tool.partition(":")[0]
            if (servers.get(server) or {}).get("egress"):
                errors.append(
                    f"{where}: node '{node.id}' calls '{tool}' on a server that sends data off the machine "
                    "(egress)"
                )
        target = cfg.get("flow")
        if node.type == "subflow" and isinstance(target, str) and "${" not in target:
            if local - set((flows.get(target) or {}).get("data_classes", [])):
                errors.append(
                    f"{where}: node '{node.id}' starts subflow '{target}' that is not a {label} flow "
                    "(it would not be local only and its traces could capture content)"
                )
    return errors


def _subflow_targets(flow: Flow) -> list[str]:
    return [
        n.config["flow"]
        for n in flow.nodes
        if n.type == "subflow" and isinstance(n.config.get("flow"), str) and "${" not in n.config["flow"]
    ]


def validate_config_flows(
    snapshot: ConfigSnapshot, registry: StepRegistry | None = None
) -> tuple[list[str], list[str]]:
    """All flows of a config plus cross-file checks (subflow cycles, schedules -> flows)."""
    try:
        flows = snapshot.flows()
    except ValidationError:
        return [], []  # reported by validate_effective
    errors: list[str] = []
    warnings: list[str] = []
    for flow in flows.values():
        e, w = validate_flow(flow, snapshot, registry)
        errors += e
        warnings += w
    graph = {fid: _subflow_targets(f) for fid, f in flows.items()}

    def cyclic(fid: str, stack: tuple[str, ...]) -> str | None:
        for target in graph.get(fid, []):
            if target in stack:
                return " -> ".join((*stack, target))
            found = cyclic(target, (*stack, target))
            if found:
                return found
        return None

    for fid in flows:
        path = cyclic(fid, (fid,))
        if path:
            errors.append(f"flow '{fid}': subflow cycle {path}")
            break
    for sched in snapshot.data.get("schedules", []):
        if sched.get("flow") not in flows:
            warnings.append(
                f"schedule for flow '{sched.get('flow')}' has no flows/{sched.get('flow')}.json yet"
            )
    return errors, warnings


def _registry() -> StepRegistry:
    load_plugins()
    return STEPS


# -- compilation --------------------------------------------------------------------------------------------
class FlowCompiler:
    """Compiles the flows of one pinned config for one run."""

    def __init__(
        self,
        snapshot: ConfigSnapshot,
        ports: StepPorts,
        run: RunInfo,
        *,
        registry: StepRegistry | None = None,
        on_status: Callable[[str], None] | None = None,
    ):
        self.snapshot = snapshot
        self.ports = ports
        self.run = run
        self.registry = registry or _registry()
        self.on_status = on_status or (lambda status: None)
        self._subgraphs: dict[str, Any] = {}
        self._lock = threading.Lock()

    def compile(self, flow_id: str, checkpointer: Any = None):
        """Compiled graph of ``flow_id``; subflows are compiled lazily and inherit the checkpointer."""
        flow = self.snapshot.flow(flow_id)
        errors, _ = validate_flow(flow, self.snapshot, self.registry)
        errors += private_data_errors(flow, self.snapshot, os.environ)
        if errors:
            raise FlowError(errors)
        return self._build(flow).compile(checkpointer=checkpointer, name=flow.id)

    def subgraph(self, flow_id: str):
        """Compiled subflow, shared by all items; parallel fan-out tasks may ask for it at the same time.

        Static subflow cycles are rejected by validation; subflows compile lazily, so nothing nests here.
        """
        with self._lock:
            if flow_id not in self._subgraphs:
                self._subgraphs[flow_id] = self.compile(flow_id)
            return self._subgraphs[flow_id]

    # -- graph ----------------------------------------------------------------------------------------------
    def _build(self, flow: Flow) -> StateGraph:
        builder = StateGraph(FlowState)
        for node in flow.nodes:
            targets = {e.to for e in flow.edges_from(node.id)}
            dest = tuple(
                sorted(
                    LG_END if t == END else t for t in targets | ({node.id} if self._loops(node) else set())
                )
            )
            builder.add_node(node.id, self._node_fn(flow, node), destinations=dest or (LG_END,))
        if flow.start is not None:
            start = flow.start

            def start_router(state: dict[str, Any]) -> Any:
                return self._goto(flow, [start], state, {}) or LG_END

        else:

            def start_router(state: dict[str, Any]) -> Any:
                targets = self._targets(flow, START, state)
                return self._goto(flow, targets, state, {}) or LG_END

        builder.add_conditional_edges(LG_START, start_router)
        return builder

    def _loops(self, node: FlowNode) -> bool:
        return node.type in self.registry and self.registry.get(node.type).loops

    def _targets(self, flow: Flow, source: str, view: dict[str, Any]) -> list[str]:
        """After ``source`` succeeded: plain edges + the first matching ``when`` edge (or default)."""
        resolver = make_resolver(view, self.snapshot, self.run)
        edges = [e for e in flow.edges_from(source) if e.on == "success"]
        targets = [e.to for e in edges if e.when is None and not e.default]
        chosen = next((e.to for e in edges if e.when is not None and evaluate(e.when, resolver)), None)
        if chosen is None:
            chosen = next((e.to for e in edges if e.default), None)
        if chosen is not None:
            targets.append(chosen)
        return targets

    def _goto(
        self, flow: Flow, targets: list[str], view: dict[str, Any], skipped: dict[str, list]
    ) -> list[Any]:
        """Turn target node ids into LangGraph gotos; ``for_each`` targets become one ``Send`` per element.

        An empty fan-out is skipped: its result is an empty list and its successors run instead.
        """
        out: list[Any] = []
        for target in dict.fromkeys(targets):
            if target == END:
                continue
            node = flow.node(target)
            if not node.for_each:
                out.append(target)
                continue
            elements = make_resolver(view, self.snapshot, self.run)(node.for_each)
            if elements is MISSING or elements is None:
                elements = []
            if not isinstance(elements, list):
                raise FlowError(
                    [f"flow '{flow.id}': for_each '{node.for_each}' of '{node.id}' is not a list"]
                )
            if elements:
                base = {k: v for k, v in view.items() if k != "item"}
                out.extend(
                    Send(target, {**base, "item": _element_item(e, i), "waits": {}})
                    for i, e in enumerate(elements)
                )
            else:
                skipped[target] = []
                nested = {**view, "item_outputs": extend_lists(view.get("item_outputs"), {target: []})}
                out.extend(self._goto(flow, self._targets(flow, target, nested), nested, skipped))
        return out

    def _node_fn(self, flow: Flow, node: FlowNode) -> Callable[[dict[str, Any]], Command]:
        executor = self.registry.get(node.type)
        config = executor.Config.model_validate(node.config)
        error_targets = [e.to for e in flow.edges_from(node.id) if e.on == "error"]
        tracer = self.ports.tracer

        def fn(state: dict[str, Any]) -> Command:
            item = state.get("item") or None
            resolver = make_resolver(state, self.snapshot, self.run)
            ctx = StepContext(
                flow=flow,
                node=node,
                run=self.run,
                snapshot=self.snapshot,
                ports=self.ports,
                item=copy.deepcopy(item),
                inputs=collect_inputs(node, state, resolver),
                resolver=resolver,
                wait=(state.get("waits") or {}).get(node.id),
                run_subflow=self._run_subflow,
                set_status=self.on_status,
            )
            item_id = str(item["id"]) if item and "id" in item else None
            category = item.get("category") if item else None
            item_cm = tracer.item(item_id, category) if item_id else contextlib.nullcontext()
            with (
                item_cm,
                tracer.step(
                    node.id, node.bpmn_element or node.id, item_id=item_id, item_category=category
                ) as span,
            ):
                span.set("pkpipe.step.type", node.type)
                try:
                    result = executor.run(node, config, ctx)
                except GraphBubbleUp:
                    raise  # LangGraph control flow (interrupts, parent commands), not a step failure
                except Exception as exc:  # noqa: BLE001 - routed or re-raised below
                    private = is_private(flow.data_classes)
                    failure = {
                        "node": node.id,
                        "type": type(exc).__name__,
                        # a nested StepFailed is content-free already (its flow was private)
                        "message": "" if private and not isinstance(exc, StepFailed) else str(exc)[:500],
                    }
                    if node.for_each and node.on_item_error == "continue":
                        span.fail(type(exc).__name__)
                        entry = {"item": item, "status": "failed", "error": failure}
                        update = {"item_outputs": {node.id: [entry]}}
                        return Command(
                            update=update,
                            goto=self._goto(flow, self._targets(flow, node.id, state), state, {}),
                        )
                    if error_targets:
                        span.fail(type(exc).__name__)
                        update = {"error": failure, "failures": {node.id: failure}}
                        view = {**state, **update}
                        return Command(
                            update=update, goto=self._goto(flow, error_targets, view, {}) or LG_END
                        )
                    if isinstance(exc, StepFailed):
                        raise
                    raise StepFailed(flow.id, node.id, exc, content_free=private) from exc
                if result.pending is not None:
                    return Command(update={"waits": {node.id: result.pending}}, goto=node.id)
                new_item = {**(item or {}), **(result.item or {})} if (item or result.item) else None
                if node.for_each:
                    entry = {"item": new_item, "output": result.output, "status": "done"}
                    update: dict[str, Any] = {"item_outputs": {node.id: [entry]}}
                    view = {
                        **state,
                        "item_outputs": extend_lists(state.get("item_outputs"), update["item_outputs"]),
                    }
                    view.pop("item", None)
                else:
                    update = {"outputs": {node.id: result.output}}
                    if result.item:
                        update["item"] = result.item
                    if ctx.wait is not None:
                        update["waits"] = {node.id: None}
                    view = {
                        **state,
                        "outputs": merge_dict(state.get("outputs"), update["outputs"]),
                        "item": new_item,
                    }
                skipped: dict[str, list] = {}
                goto = self._goto(flow, self._targets(flow, node.id, view), view, skipped)
                if skipped:
                    update["item_outputs"] = extend_lists(update.get("item_outputs"), skipped)
                return Command(update=update, goto=goto or LG_END)

        fn.__name__ = f"step_{node.id}"
        return fn

    def _run_subflow(self, flow_id: str, item: dict[str, Any] | None) -> dict[str, Any]:
        """Runs inside the calling node: LangGraph nests the subflow's checkpoints under that task, so a
        resumed run continues the subflow where it stopped instead of starting it again."""
        graph = self.subgraph(flow_id)
        return graph.invoke({"item": copy.deepcopy(item) or {}, "outputs": {}, "item_outputs": {}})


__all__ = [
    "FlowCompiler",
    "FlowError",
    "FlowState",
    "StepFailed",
    "make_resolver",
    "validate_config_flows",
    "validate_flow",
]
