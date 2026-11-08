---
spec_id: 028-cache-any-version-and-protected-domains
status: Completed
description: Plan for the any-version description lookup and the protected deny-list hosts
updated: 2026-11-08
---

# Implementation Plan: Cached image descriptions at release, protected hosts in the deny-list

**Branch**: `spec/028-cache-any-version` | **Spec**: [spec.md](spec.md) | **Depends on**: 009, 021, 025

## Summary

Port method `cache_get_latest` (SQLite, fake); argument `any_version` of `image.prepare`, set in `load_image_description.json`; config key `security.url_filter.protected_domains` (`UrlFilter` model, pack `security.json`); `is_protected` in `security/quarantine.py`; the check in `security.record_flag`.

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| II. Hexagonal core | Yes | A new port method; the step knows only the port |
| V. Security | Yes | No weaker check: the described image still passes `image.finish`; a flag still quarantines the item and is logged; only the automatic host-wide block is skipped for listed hosts |
| XI. Documentation | Yes | CHANGELOG, configuration, flows, security docs, spec 025 / 021 / 009 notes |
