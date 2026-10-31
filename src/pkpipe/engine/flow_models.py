"""Flow JSON schema: nodes, edges, conditions (spec 005 T001, data-model ``FlowNode``).

A flow file ``flows/<id>.json`` is data only; the compiler (:mod:`pkpipe.engine.compiler`) turns it into a
LangGraph ``StateGraph``. Node ``config`` is validated by the step type's own model at compile time, so a new
step type brings its own config schema.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

START = "START"
END = "END"
_NODE_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_FLOW_ID = re.compile(r"^[a-z][a-z0-9_-]*$")
#: Node ids that would shadow a reference root (``item.x``, ``run.x``, ...).
RESERVED_IDS = frozenset({"item", "run", "input", "error", "config", "result", "start", "end"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Condition(_Strict):
    """A deterministic predicate over the flow state (constitution V: code decides, models propose).

    Exactly one form: a comparison on ``path`` (``eq``, ``ne``, ``in``, ``not_in``, ``exists``, ``truthy``,
    ``gt``, ``gte``, ``lt``, ``lte``) or a combinator (``all``, ``any``, ``not``).
    Paths are references like ``item.category`` or ``categorize.confidence``.
    """

    path: str | None = None
    eq: Any = None
    ne: Any = None
    in_: list[Any] | None = Field(default=None, alias="in")
    not_in: list[Any] | None = None
    exists: bool | None = None
    truthy: bool | None = None
    gt: float | None = None
    gte: float | None = None
    lt: float | None = None
    lte: float | None = None
    all: list[Condition] | None = None
    any: list[Condition] | None = None
    not_: Condition | None = Field(default=None, alias="not")

    @model_validator(mode="after")
    def _one_form(self) -> Condition:
        ops = [
            k
            for k in ("eq", "ne", "in_", "not_in", "exists", "truthy", "gt", "gte", "lt", "lte")
            if k in self.model_fields_set
        ]
        combos = [k for k in ("all", "any", "not_") if k in self.model_fields_set]
        if combos:
            if len(combos) > 1 or self.path is not None or ops:
                raise ValueError("a condition is either one combinator (all/any/not) or one comparison")
        elif self.path is None or len(ops) != 1:
            raise ValueError("a comparison needs 'path' and exactly one operator")
        return self


class FlowNode(_Strict):
    """One step. ``inputs`` are the only data the step sees (constitution VIII)."""

    id: str
    type: str
    bpmn_element: str | None = Field(default=None, description="BPMN element name, recorded on spans")
    config: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list, description="References: item.x, <node>.x, input.x, *")
    for_each: str | None = Field(
        default=None, description="Reference to a list; the step runs once per element (Send fan-out)"
    )
    on_item_error: Literal["fail", "continue"] = Field(
        default="fail", description="for_each only: 'continue' records a failed item and goes on"
    )

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _NODE_ID.match(v) or v in RESERVED_IDS:
            raise ValueError(f"node id '{v}' must match [a-z][a-z0-9_]* and not be a reserved name")
        return v

    @model_validator(mode="after")
    def _continue_needs_fan_out(self) -> FlowNode:
        if self.on_item_error == "continue" and not self.for_each:
            raise ValueError(f"node '{self.id}': on_item_error applies to for_each nodes only")
        return self


class FlowEdge(_Strict):
    """``from`` -> ``to``. Edges without ``when`` are always taken; among ``when`` edges of one source the
    first match wins (exclusive gateway), else its ``default`` edge. ``on: error`` edges are taken only when
    the source step failed."""

    source: str = Field(alias="from")
    to: str
    when: Condition | None = None
    default: bool = False
    on: Literal["success", "error"] = "success"

    @model_validator(mode="after")
    def _shape(self) -> FlowEdge:
        if self.default and self.when is not None:
            raise ValueError("a default edge has no 'when'")
        if self.on == "error" and (self.when is not None or self.default):
            raise ValueError("error edges take no condition")
        if self.to == START or self.source == END:
            raise ValueError("edges cannot lead into START or out of END")
        return self


class Flow(_Strict):
    id: str
    description: str = ""
    bpmn: str | None = Field(
        default=None, description="BPMN process (docs/process page) this flow implements"
    )
    data_classes: list[str] = Field(
        default_factory=list,
        description="Private data handled (journal, context): forces content capture off",
    )
    start: str | None = Field(default=None, description="Entry node; or use edges from START")
    nodes: list[FlowNode]
    edges: list[FlowEdge] = Field(default_factory=list)
    max_parallel: int = Field(default=2, ge=1, le=64, description="Parallel item limit for fan-out (FR-003)")
    max_steps: int = Field(default=1000, ge=10, description="Upper bound of executed steps (loops, waits)")

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _FLOW_ID.match(v):
            raise ValueError(f"flow id '{v}' must match [a-z][a-z0-9_-]*")
        return v

    @model_validator(mode="after")
    def _graph_shape(self) -> Flow:
        ids = [n.id for n in self.nodes]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"flow '{self.id}': duplicate node id(s) {dup}")
        if not self.nodes:
            raise ValueError(f"flow '{self.id}': no nodes")
        known = set(ids)
        for e in self.edges:
            if e.source != START and e.source not in known:
                raise ValueError(f"flow '{self.id}': edge from unknown node '{e.source}'")
            if e.to != END and e.to not in known:
                raise ValueError(f"flow '{self.id}': edge from '{e.source}' to unknown node '{e.to}'")
        starts = [e for e in self.edges if e.source == START]
        if self.start is None and not starts:
            raise ValueError(f"flow '{self.id}': set 'start' or add edges from START")
        if self.start is not None and starts:
            raise ValueError(f"flow '{self.id}': use either 'start' or edges from START, not both")
        if self.start is not None and self.start not in known:
            raise ValueError(f"flow '{self.id}': start node '{self.start}' does not exist")
        by_source: dict[str, list[FlowEdge]] = {}
        for e in self.edges:
            by_source.setdefault(e.source, []).append(e)
        for source, edges in by_source.items():
            defaults = [e for e in edges if e.default]
            if len(defaults) > 1:
                raise ValueError(f"flow '{self.id}': node '{source}' has more than one default edge")
            if defaults and not any(e.when is not None for e in edges):
                raise ValueError(f"flow '{self.id}': default edge of '{source}' without any 'when' edge")
        return self

    def node(self, node_id: str) -> FlowNode:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def edges_from(self, source: str) -> list[FlowEdge]:
        return [e for e in self.edges if e.source == source]


class FlowsConfig(RootModel[dict[str, Flow]]):
    """``flows/*.json`` of the effective config, keyed by file stem (= flow id)."""

    @model_validator(mode="after")
    def _ids_match_keys(self) -> FlowsConfig:
        for key, flow in self.root.items():
            if key != flow.id:
                raise ValueError(f"flows/{key}.json declares id '{flow.id}' (must equal the file name)")
        return self
