# Evals

Datasets, rubrics and recorded model responses for `pkpipe eval` (spec 013, [docs/evaluation.md](../docs/evaluation.md)).

| Path | Content |
| --- | --- |
| `datasets/triage.jsonl` | Triage cases: URL, title, owner's note -> expected category |
| `datasets/sessions/<flow>/<node>.jsonl` | Session cases: node inputs -> `must_include` / `must_not` / `max_words` |
| `datasets/calibration/<rubric>.jsonl` | Outputs with the owner's 1-5 score, for judge calibration |
| `datasets/flows/*.json` | Flow cases: initial vault, scripted responses, owner decisions -> expected end state |
| `datasets/security.jsonl`, `datasets/injection.jsonl` | Guard detection and prompt-injection cases |
| `datasets/deterministic.jsonl` | Code rules: release, category check, state transitions, new-item selection |
| `rubrics/*.md` | LLM-judge rubrics |
| `cassettes/<suite>.json` | Responses recorded live (LM Studio, qwen3.5-9b-mlx; judge google/gemma-4-12b) for CI replay |

Five levels: deterministic, step (single session, fixed input), flow (temp vault, scripted responses), security,
production monitoring. Everything here is synthetic; real datasets live in the private overlay under
`<overlay>/evals/` (same paths, the overlay wins per file).

Re-record after changing a prompt, instruction, schema, model profile or dataset:

```bash
uv run pkpipe eval --suite triage,sessions,calibration,security --cassette record --workers 4
```
