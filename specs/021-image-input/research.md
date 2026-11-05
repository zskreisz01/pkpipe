---
spec_id: 021-image-input
title: Image input for the model port and the image description session
status: Active
description: Which local model describes INBOX images, whether gemma-4-12b can be the default model for every step, which judge to use, and how long the image batch takes. Measured 2026-11-05 on the Mac Mini (M4, 32 GB, LM Studio).
updated: 2026-11-05
---

# Research: 021-image-input

**Spec:** [spec.md](spec.md)

**Cite this file as** `specs/021-image-input/research.md` (R-1 to R-5). Companions in `research/`: `vision_probe.py` (the probe), `eval-gemma-4-12b-run1.md` and `eval-gemma-4-12b-run2.md` (the full eval reports; synthetic public datasets only).

## Conclusion (the short form)

1. **Vision profile: `google/gemma-4-12b`.** 11 of 12 real INBOX images gave valid JSON, median 53 s per image, no reasoning overhead; the one checked answer was exact (headline, domain, counts verbatim). `qwen3.5-9b-mlx` also sees images but always reasons (117–135 s per image, and the token budget is gone before the answer in many cases); LM Studio ignores `enable_thinking=false` and `reasoning_effort=none` for it.
2. **Text steps: stay on `qwen3.5-9b-mlx`.** `gemma-4-12b` as the model under test passes triage (0.957–0.967), flows, improvement, context_leak, deterministic and calibration, but fails `sessions` in both runs (code checks 0.966 with the same failing cases, garbled tokens in structured output) and the zero-tolerance `security` gate in one run (`canary_leaks` = 1). It is not a candidate for the text steps.
3. **Judge: keep `gemma-4-12b` for the text suites (the validated pairing).** `qwen3.5-9b-mlx` as judge is stricter (calibration bias -0.4 / -0.5 against +0.2), misreads a rubric (the `names` list of `summarize_enterprise`) and moves by up to 0.8 per session between identical runs. For the image eval the subject is gemma, so the judge there must be another model; use code checks first and let the Qwen rate only the free-text `topic` after a 20-item calibration. Vision judges from the literature are not needed (the synthetic images have a known truth).
4. **Run shape: two phases, one switch.** Text items on Qwen first, then images on gemma, never interleaved; the judge never runs in production. Bound the image batch by a time budget (default 2 h) with a running estimate instead of a count cap.

## How to use this research

- **Choosing or changing a model** (text, vision or judge): read R-2 to R-4 first; do not switch the default text model on speed or on a single run. A candidate must pass the `security` gate (canary leaks 0) and the code checks of `sessions` in two consecutive live runs; judge means alone decide nothing (R-4).
- **Estimating nightly time or memory:** R-1 and R-5 (53 s per image at 1024 px under memory pressure, 40 minutes for 42 images; a model load at context 8192 is too small for the text sessions, load at 32768 with `scripts/lmstudio-test-setup.sh`).
- **Re-running:** commands in R-5. Re-run the probe when the vision model, its quantization or LM Studio changes; re-run the eval pair for any model that is proposed for the text steps or as a judge. Compare with the numbers here, and record new runs next to the old ones in `research/`.
- **Do not copy** image names, descriptions or any content of the owner's INBOX into this file or the repository: the probe prints only aggregate numbers.

## R-1 – Vision probe (can the local models read the images?)

LM Studio reports the loaded models as `type: vlm` (`qwen3.5-9b-mlx`, `google/gemma-4-12b`, `google/gemma-4-e4b`, `qwen/qwen3.5-4b`). Setup: 12 images of the owner's INBOX (6 screenshots of project pages and posts, 6 shared images), downscaled to 1024 px longest side, one JSON prompt (kind, topic, verbatim visible text, URLs, language, category guess, confidence, injection flag), temperature 0, `max_tokens` 2500, Mac Mini under memory pressure (swap about 90 %).

| Model | Valid JSON | Time per image | Notes |
| --- | --- | --- | --- |
| `google/gemma-4-12b` | 11 / 12 | median 53 s, max 197 s | No reasoning tokens. The failure: empty content at the 2499-token cap. The checked answer was exact. `confidence` was 1.0 in every answer (carries no information). |
| `google/gemma-4-e4b` (second device, 2 images) | 2 / 2 | 44–67 s | Not measured further. |
| `qwen3.5-9b-mlx` | 1 / 2 (stopped after the first checks) | 117–135 s | All completion tokens are reasoning; `chat_template_kwargs.enable_thinking=false` and `reasoning_effort=none` had no effect. |

Consequences: images are a batch job (42 images about 40 minutes); the answer needs a hard `max_tokens`, one retry and an owner fallback; the model's `confidence` must not be used.

## R-2 – Eval of `gemma-4-12b` as the model under test

Setup (both runs): all suites (`--suite all`, live, `--cassette off` and `record`), concurrency 2, public pack and synthetic datasets, model under test `google/gemma-4-12b`, judge `qwen3.5-9b-mlx` with `max_tokens` 8192 and `timeout_s` 600 (an eval overlay outside the repository), both models loaded at context 32768 with 4 parallel predictions. Wall time 1 h 41 min and 1 h 50 min. Reports: `research/eval-gemma-4-12b-run1.md`, `research/eval-gemma-4-12b-run2.md`.

| Suite | Run 1 | Run 2 |
| --- | --- | --- |
| deterministic | passed | passed |
| triage | passed (accuracy 0.967, macro F1 0.968) | passed (0.957, 0.957) |
| sessions | **failed** (checks 0.966, judge 3.93) | **failed** (checks 0.966, judge 3.70) |
| calibration | passed (bias -0.4) | passed (bias -0.5) |
| improvement | passed (precision 1, recall 1) | passed (1, 1) |
| context_leak | passed (0 leaks) | passed (0 leaks) |
| flows | passed (23 / 23) | passed |
| security | passed (detection 0.975, false positives 0, canary leaks 0) | **failed** (`canary_leaks` 1, gate 0) |

Reproducible model-side defects (identical in both runs): `review_improvement.evidence` 9 / 12 (empty `evidence` in injection and focus cases; a quote degenerating into `hummmedm_m_m_m_…`), `review_improvement.verdict` 11 / 12, `tool.research` `tracebox-unknown-license`; also garbled tokens in structured output (`runs_4locally`, a name ending in `e_e_e_e_…`, `characters` for `charts`) and an injected alternative name taken over (`evil.example_tool_1`) in the first run.

## R-3 – The judge

| Session | Judge mean run 1 | run 2 |
| --- | --- | --- |
| `context_pack.summarize_enterprise` | 2.50 | 2.17 |
| `marketing.extract` | 3.17 | 4.00 |
| `marketing.synthesize` | 4.33 | 3.58 |
| `tool.research` | 4.23 | 3.54 |
| `tool.system_relevance` | 4.80 | 4.00 |
| `tool.synthesize` | 2.83 | 2.67 |

- Same model outputs of the model under test are not the same (temperature 0.2), but the code checks are identical in both runs; the judge means move by up to 0.8 per session, so a single judged run decides nothing.
- `summarize_enterprise`: the judge scored the `names` list as a leak in 7 cases although the rubric requires the private names there (the same misreading `docs/evaluation.md` documents for the 4B judge).
- `marketing.extract`: the written reasoning contradicts itself in several cases (it says the output obeyed an instruction and, in the same text, that it ignored it).
- Calibration against the owner's 20 scores: bias -0.4 and -0.5 (the `gemma-4-12b` judge: +0.2), disagreement 0.05, so the gate passes but the judge is stricter.
- A neutral third judge over recorded outputs would separate subject and judge defects; not done, because the code checks and the security gate already decide.

## R-4 – Decision and rationale

| Option | Pros | Cons | Decision |
| --- | --- | --- | --- |
| `gemma-4-12b` for every step | One model, no switching; triage 0.96 | Fails sessions code checks, intermittent canary leak, garbled structured output | Rejected for text steps |
| `qwen3.5-9b-mlx` text, `gemma-4-12b` vision, two phases, one switch | Validated text pairing; images read well | One model switch per run; images slow | **Chosen** |
| `qwen3.5-9b-mlx` for images too | One model | 117–135 s per image, reasoning exhausts the budget | Rejected until reasoning can be switched off |
| Vision judge models (Prometheus-Vision, LLaVA-Critic) | Built for image-grounded rubrics | 2025 research models, tuned for charts and captions, weak robustness reported; not needed with a known truth | Not planned |
| `qwen3.5-9b-mlx` as judge | Different family from gemma | Stricter, unstable between runs, misreads a rubric | Only for the image `topic` after calibration |

Rules derived: a model passes only on code checks and the zero-tolerance security gate in two consecutive live runs; judge means are secondary; a thinking model needs `max_tokens` and timeouts sized for reasoning.

## R-5 – Reproduce

```sh
# 1. load the models like the eval does (context 32768, 4 parallel predictions)
PKPIPE_MODEL_NAME=google/gemma-4-12b PKPIPE_JUDGE_MODEL=qwen3.5-9b-mlx scripts/lmstudio-test-setup.sh
# 2. eval with a judge overlay that gives the reasoning judge room (models.json: judge max_tokens 8192, timeout_s 600)
PKPIPE_MODEL_NAME=google/gemma-4-12b PKPIPE_JUDGE_MODEL=qwen3.5-9b-mlx \
  uv run pkpipe eval --suite all --overlay <overlay-dir> --cassette record --cassette-dir <scratch> --concurrency 2 --out <scratch>
# 3. vision probe on your own images (prints aggregate numbers only)
uv run python specs/021-image-input/research/vision_probe.py <image-dir> google/gemma-4-12b 12 1024
```

## R-6 – Risks and open points

- The sessions datasets were tuned on `qwen3.5-9b-mlx` instructions; a model-specific instruction set could change the gemma result, but not the security gate without evidence.
- A different quantization or a newer LM Studio runtime can change both the garbling and the reasoning behaviour: re-run before relying on either.
- Throughput depends on memory pressure; the time-budget guard (spec clarification 7) is the control, not the 53 s figure.
- Open: a neutral third judge over the recorded outputs (see R-3); `qwen3-vl` or MiniCPM-V class models as alternative vision models, to be measured on own images before use.

## References

- Judge studies (2025): arXiv 2505.08468 (Judging the Judges), arXiv 2510.07545 (tiny LVLM judges).
- Local model comparisons used for orientation, SEO-grade sources: tinyweights.dev "best local vision language models 2026", blog.roboflow.com "best open-source OCR models".
- `docs/evaluation.md` (judge notes, concurrency, system preflight), `scripts/lmstudio-test-setup.sh`.
