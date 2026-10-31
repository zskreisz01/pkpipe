"""Record real LM Studio responses as replay fixtures: ``uv run python -m tests.support.record_model``.

Synthetic prompts only. Writes tests/fixtures/model/<name>.json containing request, raw reply, parsed data,
usage and model, for ``tests.fakes.replay.ReplayModel``.
"""

from __future__ import annotations

import json
import sys

from pkpipe.adapters.model_openai import OpenAIModel
from tests.fakes.replay import FIXTURES
from tests.support import live

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"enum": ["tool", "learning", "reference", "other"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["category", "confidence", "reason"],
    "additionalProperties": False,
}
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    },
    "required": ["title", "bullets"],
    "additionalProperties": False,
}
TRIAGE_SYSTEM = "You classify inbox notes. Reply only with JSON matching the schema."
CASES = {
    "triage_tool": (
        TRIAGE_SYSTEM,
        "Note: A synthetic command line tool 'fooctl' that formats YAML files.",
        TRIAGE_SCHEMA,
    ),
    "triage_learning": (
        TRIAGE_SYSTEM,
        "Note: A fictional video course about learning the basics of music theory.",
        TRIAGE_SCHEMA,
    ),
    "triage_reference": (
        TRIAGE_SYSTEM,
        "Note: Synthetic reference table of HTTP status codes and their meanings.",
        TRIAGE_SCHEMA,
    ),
    "summary_note": (
        "You summarize notes into a title and short bullets. Reply only with JSON.",
        "Note: Fictional article about keeping a weekly review habit: pick a fixed time, list open loops, "
        "decide next actions, archive finished items.",
        SUMMARY_SCHEMA,
    ),
}


def main() -> int:
    profiles = {
        "default": {
            "base_url": live.model_base_url(),
            "model": live.model_name(),
            "temperature": 0.0,
            "max_tokens": 2048,
            "timeout_s": 300,
        }
    }
    model = OpenAIModel(profiles)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, (system, user, schema) in CASES.items():
        c = model.complete_full(system, user, schema)
        record = {
            "request": {"system": system, "user": user, "json_schema": schema, "profile": "default"},
            "raw_content": c.raw,
            "data": c.data,
            "usage": {"prompt_tokens": c.prompt_tokens, "completion_tokens": c.completion_tokens},
            "model": c.model,
        }
        (FIXTURES / f"{name}.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(name, c.data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
