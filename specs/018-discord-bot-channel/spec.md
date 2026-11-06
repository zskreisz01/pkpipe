---
spec_id: 018-discord-bot-channel
title: Discord bot notification channel
status: Completed
description: Channel type `discord_bot` sends notifications through the Discord REST API with a bot token, outgoing only.
relevant_when:
  - discord
  - bot
  - notifications
related: [006-notifications]
updated: 2026-11-03
---

# Feature Specification: Discord bot notification channel

**Feature Branch**: `spec/018-discord-bot-channel`
**Created**: 2026-11-03
**Status**: Completed
**Input**: Owner decision: a dedicated bot per project instead of webhooks; one channel for all events; outgoing only for now.

## Clarifications

- Outgoing only: `POST /channels/{id}/messages`, no gateway, no `discord.py`, no slash commands. A two-way bot (confirm and run from Discord through the app MCP) is a later spec and needs its own security decision (it would hold the owner token).
- One channel for every event is a routing choice in `channels.json`, not a feature.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Notifications from a bot (Priority: P1)

**Independent Test**: Adapter and notifier tests with a mock HTTP transport.

**Acceptance Scenarios**:

1. **Given** a `discord_bot` channel routed for `triage_ready`, **When** the event is sent, **Then** one `POST` to the channel's messages endpoint carries `Authorization: Bot <token>` and the webhook template text
2. **Given** a payload containing `@everyone`, **When** it is sent, **Then** `allowed_mentions.parse` is empty

### Edge Cases

- Token or channel id env variable missing → delivery fails with the variable name (never a value); other channels still deliver.
- 429 with a short `retry_after` → one retry; a long one or a second 429 → failed delivery.
- 401/403/404 → failed delivery `HTTP <status>` (token, permission or channel id wrong); the token appears in no error, span or log.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Channel type `discord_bot` with `token` (`env:NAME`) and `channel_id` (digits or `env:NAME`), validated by `pkpipe validate`; an inline token is rejected.
- **FR-002**: The API base is fixed in code; no config key changes it.
- **FR-003**: The type reuses the `discord_webhook` templates (and `<event>.discord_bot.*` overrides them), limit 2000 characters.
- **FR-004**: `allowed_mentions.parse = []` on every message.

## Success Criteria

- **SC-001**: Unit and notifier tests green without network.
