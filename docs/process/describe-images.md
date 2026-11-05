---
title: Describe Images
sidebar_position: 13
---

# Describe Images

Sub-process of [Process Inbox Items](process-inbox-items.md) (spec 021), called after the text items have been categorized. An image entry of the INBOX list is read by the vault server (`read_image`, `attachments_dir` is read-only), prepared by code (EXIF removal, scaling, hash), looked up in the description cache, and otherwise described by a local vision model in a session without tools. The description passes the output guard and the canary check before the existing category session sees it; a suspected injection goes to Handle Security Flag and only that image is quarantined.

The batch is bounded by a time budget, not by a count. At the start the guard counts the images that need a model call and reports `count × per-image estimate` (seed: median of the last runs from the timing table, else `images.estimate_s`). Before every image it stops when `elapsed + estimate` reaches `images.time_budget_s` (default 2 hours): that image and the rest stay in the INBOX list untouched, with no row and no removal, and are first in line the next day. After every image the estimate becomes the mean of the images done in this run. A boundary timer (`images.per_image_timeout_s`, 300 s) sends one stuck image to `needs_owner` so it cannot spend the budget. A run has two model phases with one switch between them, never interleaved: the text items run first on `qwen3.5-9b-mlx`, then the images on `gemma-4-12b` (the text model may be unloaded in between); the judge is not used in production runs. The images get the time that is left of the cycle after the text items. The batch report is written in both exits (budget reached, or no images left).

![Describe Images](../img/bpmn/describe-images.png)

```mermaid
flowchart LR
    s((Image entries<br/>in INBOX list)) --> t1[Count images that need a model call<br/>cache hits are free<br/>estimate = count x per-image estimate<br/>seed: median of last runs, else images.estimate_s] --> t2[Report the estimate<br/>log, notification] --> gm{ } --> gl{Images left?}
    gl -- yes --> g1{"elapsed + estimate #lt; time budget?<br/>images.time_budget_s, default 2 h"}
    gl -- no --> gj{ }
    g1 -- no --> td[Leave this and the remaining images in the INBOX untouched<br/>no row, no removal<br/>count them as deferred] --> gj
    g1 -- "yes" --> one
    subgraph one [Describe one image]
        ms(( )) --> rd[read_image<br/>vault tool, attachments_dir read-only] --> pr[Prepare, code<br/>EXIF removal, scale, hash] --> gc{Cache hit?}
        gc -- no --> de[Describe<br/>local vision model, no tools, one retry]
        de -. "per-image timeout 300 s" .-> ef((Item needs_owner<br/>failed))
        gc -- "yes, free" --> gd
        de --> gd[Output guard + canary<br/>visible_text, topic] --> gf{Injection suspected /<br/>guard flag?}
        gf -- yes --> hq[[Handle security flag]] --> eq((Quarantined))
        gf -- no --> pa[Pass the description to the<br/>existing category session] --> up[Update estimate<br/>mean of images done in this run] --> ei((Image done))
    end
    one -- next image --> gm
    gj --> rp[Write batch report<br/>processed, cached, failed, deferred<br/>estimate start / end, elapsed] --> gs{Stopped by budget?}
    gs -- yes --> e1((Deferred to next day))
    gs -- no --> e2((All images processed))
    pr -.-> dc[(Description cache, app DB)]
    up -.-> dt[(Timing table, app DB)]
    t2 -. estimate .-> nc[/Discord or Teams/]
    rp -. batch report .-> nc
    de -. image + prompt .-> lm[/Local model server, loopback/]
```

## Implementation (spec 021, planned)

| BPMN element | Code | Notes |
| --- | --- | --- |
| Count and estimate, Report the estimate, budget gateway, Update estimate, Leave the rest untouched | Code step `image.budget` (FR-014) | Pure function with an injectable clock: counts uncached images, estimates, decides `continue` or `stop` before every image, records per-image seconds; a deferred item gets no row and no removal |
| Time budget, estimate seed, timeout | `config/images.json`: `time_budget_s` (7200), `estimate_s` (60), `per_image_timeout_s` (300) | The seed is the median of the last 50 timing rows of the model |
| read_image | Vault tool `read_image` (FR-004) | Basename only, extensions `jpg jpeg png webp gif`, size limit; `attachments_dir` is read-only (FR-010) |
| Prepare | Code step `image.prepare` (FR-005) | EXIF removal, scaling to `images.max_side`, JPEG re-encode; the hash of the prepared bytes is the cache key |
| Cache hit? | Description cache table in the app DB (FR-012) | Key: hash, model, prompt version; the triage row shows `topic` only |
| Describe | Session `describe_image` (FR-006), data class `image` (FR-009) | No tools, loopback model only, `max_tokens`, one retry, then `needs_owner` |
| per-image timeout (300 s) | Per-image timeout around the model call | Counts as a failure of that image; the loop continues |
| Output guard + canary | Output guard and canary rules of spec 009 (FR-007) | On `visible_text` and `topic`; URLs found in the image pass the URL policy first |
| Handle security flag | [Handle Security Flag](handle-security-flag.md) | Only that image is quarantined |
| Pass the description to the category session | Triage image branch (FR-008) | The extract of an image item is its description; the categorizing session is the existing one |
| Write batch report | `ImageBatchReport` (run output and notification) | `images_total`, `cached`, `described`, `failed`, `deferred`, `estimate_start_s`, `estimate_end_s`, `elapsed_s`, `stopped_by_budget` |
