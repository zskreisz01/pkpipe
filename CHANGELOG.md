# Changelog

## 0.0.1 – unreleased
- Spec 001: core-defaults config layer, layer-confined session references, Pydantic config models + JSON Schema export, `pkpipe validate`, effective-config sha256 hash, test layout (unit/integration/e2e, fakes, `live` marker), CI + Pages workflows, Docusaurus site.
- Spec 003: `pkpipe.telemetry` (OTLP-only bootstrap from `OTEL_*`, flow/step/LLM/tool spans with pkpipe attributes, per-flow content capture with scrubbing exporter, security-flag events, metrics, OpenInference/httpx/FastAPI instrumentation), Phoenix compose with auth off, Langfuse docs.
- Repository skeleton, ports, triage domain rules, canary helper, layered config loader.
