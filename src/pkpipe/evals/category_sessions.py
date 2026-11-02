"""Category sessions as eval targets (spec 010 FR-001, constitution IX: every LLM step has an eval dataset).

A session of a category's plan (``categories.json``) is an LLM step like a flow node, so it is evaluated like
one: ``datasets/sessions/<category id>/<session id>.jsonl`` names it (``tool/research.jsonl``), and the eval
builds the prompt exactly as the ``session_plan`` step does (:func:`pkpipe.steps.llm.build_prompt`: system
prompt, instruction, context pack, declared inputs, datamarking, output schema, the run canary).

A case gives the declared inputs by reference (``item.extract``, ``research``, ...; a session that reads ``*``
gets every key) and, for a session with a context pack, the pack's text under ``context`` (synthetic: the
packs themselves are private overlay files). A session with tools is evaluated on its inputs alone (the single
final answer with the output schema), the tool loop itself is a code path covered by the step's own tests.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from pkpipe.engine.config_models import Session
from pkpipe.engine.flow_models import FlowNode
from pkpipe.engine.snapshot import ConfigSnapshot
from pkpipe.steps.llm import LlmConfig

#: The case-input key that carries a synthetic context pack.
CONTEXT_KEY = "context"


def _category_sessions(snapshot: ConfigSnapshot):
    for cat in snapshot.data.get("categories") or []:
        if cat.get("subflow") != "other_resource":
            continue  # the sessions of other subflows are descriptive (the learning notebook agent)
        for raw in cat.get("sessions") or []:
            yield cat["id"], Session.model_validate(raw)


def session_node(session: Session) -> FlowNode:
    config: dict[str, Any] = {
        "system": session.system,
        "instruction": session.instruction,
        "canary": True,
    }
    if session.context:
        config["context"] = session.context
    if session.output_schema:
        config["output_schema"] = session.output_schema
    if session.tools:
        config["tools"] = list(session.tools)
    return FlowNode(
        id=session.id,
        type="llm_tools" if session.tools else "llm",
        bpmn_element="Run category sessions",
        config=config,
        inputs=list(session.inputs),
    )


def find_session(snapshot: ConfigSnapshot, category_id: str, session_id: str) -> FlowNode | None:
    for cid, session in _category_sessions(snapshot):
        if cid == category_id and session.id == session_id:
            return session_node(session)
    return None


def session_names(snapshot: ConfigSnapshot) -> list[str]:
    """``<category>.<session>`` of every session of a category plan that runs through ``session_plan``."""
    return sorted(f"{cid}.{s.id}" for cid, s in _category_sessions(snapshot))


def llm_config(node: FlowNode) -> LlmConfig:
    """The session part of a node's config (``tools`` of a session with tools is not used by an eval)."""
    return LlmConfig.model_validate({k: v for k, v in node.config.items() if k in LlmConfig.model_fields})


def case_inputs(node: FlowNode, case_input: dict[str, Any]) -> dict[str, Any]:
    """The declared inputs of ``node`` from a case's input; a session that reads ``*`` gets every key."""
    everything = "*" in node.inputs
    return {k: v for k, v in case_input.items() if k != CONTEXT_KEY and (everything or k in node.inputs)}


def with_context(snapshot: ConfigSnapshot, node: FlowNode, case_input: dict[str, Any]) -> ConfigSnapshot:
    """The snapshot with the case's synthetic context pack in place of the (private) one of the config."""
    text = case_input.get(CONTEXT_KEY)
    pack = node.config.get("context")
    if not (isinstance(text, str) and pack):
        return snapshot
    return dataclasses.replace(snapshot, assets={**snapshot.assets, f"contexts/{pack}.md": text})
