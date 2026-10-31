# Changelog

## 0.0.1 – unreleased
- Spec 001: core-defaults config layer, layer-confined session references, Pydantic config models + JSON Schema export, `pkpipe validate`, effective-config sha256 hash, test layout (unit/integration/e2e, fakes, `live` marker), CI + Pages workflows, Docusaurus site.
- Spec 002: `ModelPort.complete(..., profile)` with `SchemaError`/`ModelUnavailable`/`ContextOverflow`, OpenAI-compatible adapter (json_schema, local validation, retry, fallback, OpenTelemetry spans), `env:` profile resolution, recorded LM Studio fixtures + `ReplayModel`.
- Spec 002 review: context pre-check against the profile `context` (`ContextOverflow` before sending), reasoning output never mined for draft JSON, lone `</think>` handling, truncated text replies are errors, `model.fallback` event also on the fallback span.
- Spec 003: `pkpipe.telemetry` (OTLP-only bootstrap from `OTEL_*`, flow/step/LLM/tool spans with pkpipe attributes, per-flow content capture with scrubbing exporter, security-flag events, metrics, OpenInference/httpx/FastAPI instrumentation), Phoenix compose with auth off, Langfuse docs.
- Spec 003 review: `telemetry.json` config file (validated, layered) feeds the content policy; URL-decoded OTLP headers; no invented metrics URL with a traces-only endpoint; step spans always carry all seven attributes; reranker payloads scrubbed; `tokens_per_second` / learning-time recorders; live LM Studio telemetry tests.
- Repository skeleton, ports, triage domain rules, canary helper, layered config loader.
