---
spec_id: 028-cache-any-version-and-protected-domains
status: Completed
description: Tasks of the any-version description lookup and the protected deny-list hosts
updated: 2026-11-08
---

# Tasks: Cached image descriptions at release, protected hosts in the deny-list

- [x] T001 `cache_get_latest` in the port, SQLite adapter and fake; contract test
- [x] T002 `any_version` in `image.prepare`; `load_image_description.json`; flow test
- [x] T003 `protected_domains` in the config model and the pack; `is_protected`; `security.record_flag`; tests
- [x] T004 Docs, CHANGELOG, spec notes, version 0.1.7
