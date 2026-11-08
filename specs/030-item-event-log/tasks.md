---
spec_id: 030-item-event-log
status: Completed
description: Tasks of the item event log and the process start buttons
updated: 2026-11-08
---

# Tasks: Item event log

- [x] T001 `ItemEventPort`, AppStore table and methods (`record`, `events_of`, `recent_item_ids`, `audit_of`), `domain/item_events.py`, `app.make_item_events`
- [x] T002 `StepContext.event`; compiler `step` events (done / failed, duration, content-free in private flows)
- [x] T003 Events from `secure_fetch.verdict`, `security.record_flag`, `security.hold_for_review`, `item.find_sources`
- [x] T004 `AppServices.item_history`, `recent_items`, `launchable_flows`; HTTP route; UI `/items`, `/items/{id}`, history link, overview card, CSS
- [x] T005 App MCP owner tool `item_history`; CLI `pkpipe item`
- [x] T006 Tests: unit (`test_item_events`, `test_compiler`), integration (`test_item_history`, MCP contract)
- [x] T007 Docs, C4 labels and PNGs, glossary, CHANGELOG 0.1.9, version
