---
spec_id: 030-item-event-log
status: Completed
description: Plan for the item event log and the process start buttons
updated: 2026-11-08
---

# Implementation Plan: Item event log

**Branch**: `spec/030-item-event-log` | **Spec**: [spec.md](spec.md) | **Depends on**: 014, 027, 029

## Summary

New port `ItemEventPort` implemented by `AppStore` (table `item_events`); pure `domain/item_events.py`; the compiler wraps every item step with a `step` event and `StepContext.event` lets steps add `verdict`, `source`, `quarantined`, `held`; `AppServices.item_history` merges events with the audit rows of the item; UI, HTTP, app MCP (owner tool) and CLI read it; the overview gets start buttons.

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| II. Hexagonal core | Yes | Steps and the compiler know only the port (through `ports.extra["item_events"]`) |
| V. Security / private data | Yes | No text in events; private data classes log content-free events; the MCP tool is owner only |
| XI. Documentation | Yes, with a stated BPMN decision | See below |

## BPMN decision

No BPMN page changes: the log adds no node, edge, state, gateway or outcome to any process; it records what the existing elements already do. The owner's view of it (a page, an API, a tool) is a channel of the existing owner-decision activities, not a new activity. Docs, C4 (App store label, App DB label), specs and the glossary carry the change.

## Risks

Writes per step on SQLite (WAL): one small insert per item step, in the process that runs the flow; a failing write is logged and ignored. The table grows without bound (a retention rule is out of scope; rows are small).
