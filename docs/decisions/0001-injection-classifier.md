---
title: "0001 Injection and harm classifier"
---

# 0001: Injection and harm classifier (Qwen3Guard + ProtectAI DeBERTa)

- **Status:** Accepted, 2026-11-01
- **Context:** specs [008 Secure fetch](https://github.com/zskreisz01/pkpipe/blob/master/specs/008-secure-fetch/spec.md) (FR-004) and [009 Output guard and quarantine](https://github.com/zskreisz01/pkpipe/blob/master/specs/009-output-guard-and-quarantine/spec.md) (FR-002)
- **Supersedes:** the earlier choice of Meta's Llama Prompt Guard 2 in specs 008 and 009

## Context

Secure fetch scans the sanitized page and the output guard scans every session output that derives from external content. Both need a local classifier for prompt injection / jailbreak, and the output guard also one for harmful content. The first design used Meta's Llama Prompt Guard 2 (86M). That model is gated: downloading it requires accepting the Meta licence and sharing account data with Meta through the Hugging Face gate. **The owner decided not to use Meta models** (no Prompt Guard, no Llama Guard), so pkpipe neither ships an adapter for them nor tells anyone to accept that licence.

## Decision

`security.classifier` names a local backend; every backend implements `ClassifierPort` and only reports scores, labels and categories. Code decides what is a flag (constitution V).

| Backend | Model | Licence, source | Role |
| --- | --- | --- | --- |
| `qwen3guard` | Qwen3Guard-Gen-0.6B | Apache-2.0, official `Qwen/` repository | Safety moderation: `Safe` / `Controversial` / `Unsafe` + categories (Violent, PII, ..., Jailbreak). The text is sent as the user prompt (prompt moderation, the policy with the Jailbreak category); the score comes from the label-token probabilities |
| `deberta` | protectai/deberta-v3-base-prompt-injection-v2 | Apache-2.0, official `protectai/` repository, not gated | Binary prompt-injection classifier, 512-token window |
| `combined` | its `members` | | Maximum score; every member's result is kept, so the output guard can tell a harm category (`malicious_content`) from an injection (`injection_or_jailbreak`) |

The reference pack uses `combined` with both models. Models are read from local directories only (`local_files_only`, `trust_remote_code=False`, safetensors), never downloaded at run time; `torch` and `transformers` are the optional extra `guard`. A missing model fails closed unless the owner sets `security.classifier.required: false`.

## Measurements

Synthetic sets of the security eval, threshold 0.5 (2026-11-01, Apple silicon, MPS):

| Backend | Direct injections (`security.jsonl`, 40 attacks / 40 clean) | Indirect injections in web pages (`secure_fetch.jsonl`, sanitized text, 40 / 41) |
| --- | --- | --- |
| Qwen3Guard-Gen-0.6B | detection 0.35, false positives 0.00 | detection 0.15, false positives 0.00 |
| DeBERTa PI v2 (512 tokens) | detection 0.90, false positives 0.00 | detection 0.425, false positives 0.024 |
| Combined (max) | detection 0.95, false positives 0.00 | detection 0.45, false positives 0.024 |

Qwen3Guard is a harm moderator: it marks most plain instruction overrides `Safe`, which is why it cannot meet the 0.90 detection gate alone and the DeBERTa injection classifier was added. Smaller DeBERTa chunks raise indirect detection (0.775 at 64 tokens) but also false positives (0.098), so the window stays at 512. No single classifier reaches 0.90 on indirect injections buried in long pages; secure fetch therefore never relies on the classifier alone: sanitizing, heuristics (with the hidden text), the tool-less Reader with its canary and the code verdict together reach 1.0 detection on the same pages (gate `fetch_detection_rate`), and the output guard adds canary, active-markup and heuristic checks (gate `output_guard_detection_rate`). The gate on the classifier itself is `classifier_direct_detection_rate >= 0.90`; the indirect rate is reported per model (`classifier_by_model`) without a gate. Qwen3Guard-Gen-4B was not measured: it applies the same moderation policy, so it is not expected to change the injection picture.

## Vetting of the model files

- Qwen3Guard-Gen-0.6B: official `Qwen/Qwen3Guard-Gen-0.6B` weights (safetensors), installed in `~/tools/qwen3guard/Qwen3Guard-Gen-0.6B`; not a third-party conversion.
- ProtectAI DeBERTa: official repository at revision `90c9989b1a342275dd0d1a95aad283c04e075671`; only `model.safetensors`, `config.json`, tokenizer files, licence and readme were downloaded (`training_args.bin`, a pickle, was not). `model.safetensors` and `spm.model` match the SHA-256 of the Hugging Face API metadata, the other files their git blob ids. `config.json` names `DebertaV2ForSequenceClassification` with labels `SAFE` / `INJECTION` and no `auto_map` (no remote code). Installed in `~/tools/protectai-deberta-pi-v2` with a `VETTING.md` record.

## Consequences

- The former classifier settings key is gone; `security.classifier {backend, model, model_dir, chunk_tokens, members, threshold, required}` replaces it, and `security.output_guard` holds only `block_on`.
- CI has no models: the security eval replays the recorded classifier results (`evals/cassettes/classifier.json`), recorded live like the model cassettes; unit and e2e tests use a fake classifier, `tests/integration/security/test_classifiers_live.py` (`live("guard")`) uses the real models.
- A new model (for example a larger Qwen3Guard) is a config change plus a measured eval run (constitution IX).
