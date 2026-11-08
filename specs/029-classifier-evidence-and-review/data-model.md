---
spec_id: 029-classifier-evidence-and-review
status: Completed
description: Data of the classifier evidence, the quarantine note and the review table
updated: 2026-11-08
---

# Data Model: Classifier evidence and review

**Evidence** (`Verdict.evidence["classifier"]`, `flag.detail`, `classifier_reviews.evidence`, `security_flag.classifier`):
`{threshold, max_score, reading, members: [{model, kind: injection|safety, max_score, chunks, over, flagged_chunks[], flagged_chunk_count, label?, categories?}]}`. Numbers, labels, chunk numbers only.

**Quarantine note front matter** (additions): `source_url`; `security_flag.scores: {flag: score}`; `security_flag.classifier: <evidence>`. Body: `Source: \`<url>\`` and the section *Why it was flagged* (table + fixed sentences).

**`malicious_urls.jsonl`** (addition): `detail` (evidence of the flag, optional); `score` is now set for classifier flags.

**App DB** table `classifier_reviews(item_id PK, url, evidence JSON, held_at, waived_at NULL)`.

**Config** `security.json`: `classifier.review: {enabled: bool}`.
