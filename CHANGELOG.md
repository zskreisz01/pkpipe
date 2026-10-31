# Changelog

## 0.0.1 – unreleased
- Spec 001: core-defaults config layer, layer-confined session references, Pydantic config models + JSON Schema export, `pkpipe validate`, effective-config sha256 hash, test layout (unit/integration/e2e, fakes, `live` marker), CI + Pages workflows, Docusaurus site.
- Spec 002: `ModelPort.complete(..., profile)` with `SchemaError`/`ModelUnavailable`/`ContextOverflow`, OpenAI-compatible adapter (json_schema, local validation, retry, fallback, OpenTelemetry spans), `env:` profile resolution, recorded LM Studio fixtures + `ReplayModel`.
- Spec 002 review: context pre-check against the profile `context` (`ContextOverflow` before sending), reasoning output never mined for draft JSON, lone `</think>` handling, truncated text replies are errors, `model.fallback` event also on the fallback span.
- Repository skeleton, ports, triage domain rules, canary helper, layered config loader.
