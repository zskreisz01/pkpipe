---
spec_id: 006-notifications
title: Notification port and channels
status: Draft
description: NotifyPort with Discord webhook, Teams workflow webhook and Apprise adapters, event-to-channel routing and message templates per channel type.
relevant_when:
  - notifications
  - Discord
  - Teams
  - Apprise
  - channels.json
related: [007-triage-and-release]
updated: 2026-10-31
---

# Feature Specification: Notification port and channels

**Feature Branch**: `spec/006-notifications`
**Created**: 2026-10-31
**Status**: Draft
**Input**: Design docs (`docs/`), process model (`docs/design/pkpipe-process-model.drawio`), constitution

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Owner is told when to act (Priority: P1)

Triage-ready, daily summary, weekly review and security alerts reach the configured channels.

**Independent Test**: Adapter tests with a fake HTTP server.

**Acceptance Scenarios**:

1. **Given** event `triage_ready`, **When** routed, **Then** each configured channel receives the rendered template
2. **Given** a channel failure, **When** sending, **Then** other channels still receive it and the failure is traced

### Edge Cases

- Message longer than the channel limit → truncated with a link to the UI.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: NotifyPort MUST define `send(event, payload)`; routing by event type from `channels.json`.
- **FR-002**: Templates MUST be Jinja2 files per channel type (`templates/<event>.<type>.md|json`).
- **FR-003**: Secrets MUST be resolved from `env:` references.

### Key Entities

- **Channel**
- **Route**
- **Template**

## Success Criteria *(mandatory)*

- **SC-001**: Adding a new Apprise channel is configuration only.

## Clarifications

### Session 2026-10-31

- Q: Is this spec part of the public engine? → A: Yes; private categories, contexts and datasets stay in the overlay.

## Out of Scope

- Interactive buttons (later; confirmations go through UI or app MCP).
