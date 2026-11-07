---
spec_id: 026-config-editor-forms
status: Completed
description: Plan for the config editor forms
updated: 2026-11-07
---

# Implementation Plan: Config editor forms in the UI

**Branch**: `spec/026-config-editor-forms` | **Spec**: [spec.md](spec.md) | **Depends on**: 001, 014, 022, 023

## Summary

A pure module `api/forms.py` (schema to field tree, posted form to JSON, row actions on the flat form, minimal overlay, secret rule, cron preview, token hint) and thin routes and templates in the existing UI: `/config/form`, `/ui/config/form/{save,action}`, `/ui/config/cron-preview`, the macro file `_fields.html`, `_form.html`, `config_form.html`, and a small `config-forms.js`.

## Constitution Check

| Principle | Complies? | Note |
| --- | --- | --- |
| II. Hexagonal core | Yes | UI adapter only; the form module imports the config models, no port |
| III. Configuration over code | Yes | Forms edit config; no behaviour moves into code; new sections need no code |
| IV. Untrusted content | Yes | Owner input only; names carry no owner text; autoescape; no inline script |
| V. Secrets | Yes | `env:NAME` only, literals refused and never rendered |
| VI. Human in the loop | Yes | Saving only changes the draft; publish keeps validate, eval gate, commit, rollback |
| VII. Public / private separation | Yes | Generic; no personal data in code, tests or docs |
| IX. Eval before publish | Yes | The publish gate is untouched |
| XI. Design as diagram and code | N/A | No process or component change: BPMN and C4 stay |
| XII. Tooling | Yes | `uv`, ruff, mypy (`api/forms.py` is on the typed list), pytest |

## Structure

```
src/pkpipe/api/forms.py              schema -> Node tree, parse, actions, minimize, secrets, cron, token hint
src/pkpipe/api/app.py                routes (form page, save, action, cron preview), hints
src/pkpipe/api/workspace.py          pack_json(), literal-secret check in check_content
src/pkpipe/api/templates/            _fields.html (macros), _form.html, config_form.html; config/edit/_editor changes
src/pkpipe/api/static/               config-forms.js, app.css additions
tests/unit/api/test_forms.py         engine
tests/integration/api/test_config_forms.py   pages, actions, errors, secrets, escaping, CSRF
```

## Decisions

- Validation happens on the merged layers (`merge_layers`) so partial overlays are judged as the engine sees them.
- Rows are numbered; actions renumber the flat posted form, so no server session state is needed.
- Flows stay JSON: a graph of nodes and edges is better served by the JSON view and the BPMN page.
