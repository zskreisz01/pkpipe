---
spec_id: 018-discord-bot-channel
status: Completed
description: Plan for the Discord bot channel
updated: 2026-11-03
---

# Implementation Plan: Discord bot notification channel

**Branch**: `spec/018-discord-bot-channel` | **Spec**: [spec.md](spec.md)

## Summary

`pkpipe.adapters.notify_discord_bot.DiscordBot` (httpx), a router factory entry, `Channel` model fields, template alias to the webhook templates.

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| II. Hexagonal core | Yes | An outbound adapter behind `NotifyPort`; the core is untouched |
| III. Configuration over code | Yes | `channels.json` only |
| IV. Untrusted content isolation | Yes | Mentions disabled; payload text is data |
| VII. Public / private separation | Yes | Token and channel id are `env:` references |
| X. Observable by default | Yes | The existing `notify.send` span; the client stays out of httpx instrumentation |
| Others | n/a | |
