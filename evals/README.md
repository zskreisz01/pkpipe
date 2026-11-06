# Evals

Datasets, rubrics and recorded model responses for `pkpipe eval` (spec 013, [docs/evaluation.md](../docs/evaluation.md)).

| Path | Content |
| --- | --- |
| `datasets/triage.jsonl` | Triage cases: URL, title, owner's note -> expected category |
| `datasets/improvement_extract.jsonl` | Journal section -> expected improvement statements (spec 015) |
| `datasets/sessions/<flow>/<node>.jsonl` | Session cases: node inputs -> `must_include` / `must_not` / `max_words` |
| `datasets/calibration/<rubric>.jsonl` | Outputs with the owner's 1-5 score, for judge calibration |
| `datasets/flows/*.json` | Flow cases: initial vault, scripted responses, owner decisions -> expected end state |
| `datasets/security.jsonl`, `datasets/injection.jsonl` | Guard detection and prompt-injection cases |
| `datasets/output_guard.jsonl` | Session outputs for the output guard (spec 009): clean, injected, harmful, leaked canary (`{canary}` / `{canary_hex}` placeholders) |
| `datasets/images.jsonl` | Recipes for synthetic images drawn by code (spec 021) -> expected kind, verbatim text, URL, injection flag, topic truth; `cassettes/images.json` is keyed by the recipe of each image |
| `datasets/deterministic.jsonl` | Code rules: release, category check, state transitions, new-item selection |
| `rubrics/*.md` | LLM-judge rubrics |
| `cassettes/<suite>.json` | Responses recorded live (LM Studio, qwen3.5-9b-mlx; judge google/gemma-4-12b) for CI replay |
| `cassettes/classifier.json` | Classifier results (Qwen3Guard + DeBERTa, local models) of the security suite, recorded with it; keyed by classifier config + text |

Five levels: deterministic, step (single session, fixed input), flow (temp vault, scripted responses), security,
production monitoring. Everything here is synthetic; real datasets live in the private overlay under
`<overlay>/evals/` (same paths, the overlay wins per file).

Re-record after changing a prompt, instruction, schema, model profile or dataset:

```bash
uv run pkpipe eval --suite triage,sessions,calibration,improvement,security --cassette record --concurrency 4
```
