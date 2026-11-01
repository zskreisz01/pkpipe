---
spec_id: 012-learning-gateway
title: learning-gateway MCP and Process Learning Material
status: Active
description: Technology options and decision for learning-gateway mcp and process learning material.
updated: 2026-11-01
---

# Research: 012-learning-gateway

**Spec:** [spec.md](spec.md)

## R-1 – Options

| Option | Pros | Cons | Decision |
| --- | --- | --- | --- |
| Open Notebook + local TTS | Official REST API, local, MIT | Voice quality below NotebookLM | Default |
| NotebookLM (consumer) via browser automation | Best quality, mobile app | No official API, fragile | Optional backend |
| NotebookLM Enterprise API | Official | Paid Google Cloud product | Not planned |

## R-2 – Decision and rationale

Open Notebook with Speaches (Kokoro) is the default backend; NotebookLM stays an optional backend behind the same
interface. Both sit behind `LearningBackend`, so the core, the flows and the MCP tools never change (constitution
I, II, III).

## R-3 – Spike results (Open Notebook 1.15.0, Speaches CPU image, Kokoro-82M ONNX, 2026-11-01)

- REST flow verified: `POST /api/notebooks` -> `POST /api/sources/json` (`type: link`, `async_processing: false`
  returns once the page is fetched) -> `POST /api/podcasts/generate` (`episode_profile`, `speaker_profile`,
  `notebook_id`, `briefing_suffix`) -> `GET /api/podcasts/jobs/{id}` (`running` -> `completed` with `episode_id`, or
  `failed` with `error_message`) -> `GET /api/podcasts/episodes/{id}/audio`. The notebook URL is
  `<ui>/notebooks/<id>`.
- Configuration is API only (Open Notebook 1.15 stores credentials encrypted): credential `openai_compatible` per
  endpoint (LM Studio via `host.docker.internal:1234/v1`, Speaches via `speaches:8000/v1`), models, defaults, one
  speaker profile and one episode profile; a profile needs 3 to 20 segments.
- Qwen3.5 as podcast model fails ("Invalid json output": reasoning before the JSON); `google/gemma-4-12b` works.
  A 3 segment overview (28 clips, about 7 minutes of audio) took about 10 minutes on CPU: about 7 minutes for the
  outline and transcript, about 2.5 minutes for Kokoro. Hence the 90 minute default timeout.
- `google/gemma-4-12b` is a thinking model as well (about 7 tokens per second on this machine, almost all of them reasoning): a transcript segment that runs away hits `max_tokens` and is retried once, so the duration varies from about 10 to 40 minutes. The setup script sets the profile's `max_tokens` to 4096 to bound one attempt. Measured with the live test (`PKPIPE_LIVE_AUDIO=1`): the gateway created the notebook from a synthetic page, polled the job to `ready` after 995 s and downloaded a 1.78 MB MP3 (one earlier attempt hit the 30 minute gateway timeout and was reported as `failed: timeout after 30 min`, which also validated that path live). A non-thinking model would make generation much faster; it is a `--podcast-model` setting.
- Open Notebook fetches link sources itself, so the gateway filters URLs before they are forwarded.
- Open points: the voice quality is the owner's call (SC-002); NotebookLM needs a driver the owner brings.
