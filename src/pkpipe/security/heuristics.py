"""Regex heuristics of secure fetch (spec 008 FR-003): cheap scoring of naive override attempts.

Each rule has a code and a weight; the score is the sum of the weights of the distinct rules that match the
visible text. Text the sanitizer removed as hidden (hidden elements, comments, Unicode tag smuggling) is
scanned too: an instruction-like phrase a page hides from its human reader scores ``hidden_<code>`` with
weight 1. Only rule codes are returned, never the matched text (it may be an attack).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_I = re.IGNORECASE | re.MULTILINE


@dataclass(frozen=True)
class Rule:
    code: str
    pattern: re.Pattern[str]
    weight: float


RULES: tuple[Rule, ...] = (
    Rule(
        "override",
        re.compile(
            r"\b(ignore|disregard|forget|override|bypass|skip)\b[^.\n]{0,40}"
            r"\b(previous|prior|above|earlier|preceding|all|your|any|the|system)\b[^.\n]{0,30}"
            r"\b(instructions?|prompts?|rules|guidelines|directions|directives)\b",
            _I,
        ),
        1.0,
    ),
    Rule(
        "role_tags",
        re.compile(
            r"<\|im_(start|end)\|>|\[/?INST\]|<<\s*/?SYS\s*>>|<\|(system|assistant|user|endoftext)\|>"
            r"|^\s*#{0,3}\s*(system|assistant)\s*(prompt)?\s*:\s*(you\b|ignore|from\s+now|new\s+instructions)",
            _I,
        ),
        1.0,
    ),
    Rule(
        "new_instructions",
        re.compile(
            r"\b(new|updated|real|actual|revised)\s+(system\s+)?(instructions?|prompt)\s*:"
            r"|\bsystem\s+(override|prompt\s*:)|\bend\s+of\s+(the\s+)?(document|data|untrusted)\b[^.\n]{0,40}\b"
            r"(instructions?|now)\b",
            _I,
        ),
        1.0,
    ),
    Rule(
        "addressed_to_model",
        re.compile(
            r"\b(note|message|attention|instructions?|important)\s*(to|for)\s+(the\s+|any\s+|all\s+)?"
            r"(ai|llm|assistant|language\s+model|model|agent|summari[sz]er|reader|bot|crawler)s?\b"
            r"|\b(ai|llm|assistant|language\s+model|summari[sz]er|agent)s?\s+(reading|processing|summari[sz]ing)"
            r"\s+this\b",
            _I,
        ),
        1.0,
    ),
    Rule(
        "role_change",
        re.compile(
            r"\byou\s+are\s+now\s+(an?\s+|in\s+)?(unrestricted|jailbroken|dan|evil|developer|admin|root|"
            r"different|new)\b|\b(enable|enter|activate)\s+(developer|god|jailbreak)\s+mode\b"
            r"|\bdo\s+anything\s+now\b|\bwithout\s+(any\s+)?(restrictions|filters|guidelines|safety)\b",
            _I,
        ),
        1.0,
    ),
    Rule(
        "prompt_leak",
        re.compile(
            r"\b(reveal|print|repeat|output|show|disclose|leak|tell\s+me)\b[^.\n]{0,40}"
            r"\b(system\s+prompt|hidden\s+instructions|your\s+(instructions|prompt|rules)|reference\s+code|"
            r"canary|secret\s+(token|code|key))",
            _I,
        ),
        1.0,
    ),
    Rule(
        "exfiltration",
        re.compile(
            r"!\[[^\]]*\]\(\s*https?://[^)\s]*[?&][^)\s]*=|"
            r"\b(send|post|upload|forward|exfiltrate|leak|email)\b[^.\n]{0,60}"
            r"\b(system\s+prompt|api\s+keys?|passwords?|secrets?|credentials|tokens?|private\s+notes|journal|"
            r"conversation|context)\b[^.\n]{0,40}\b(to|at|via)\b",
            _I,
        ),
        1.0,
    ),
    Rule(
        "output_hijack",
        re.compile(
            r"\b(set|mark|classify|categori[sz]e|rate|label)\b[^.\n]{0,30}\b(this|the)\s+"
            r"(item|page|document|article|url|resource)\b[^.\n]{0,30}\b(as|to)\b[^.\n]{0,30}"
            r"\b(safe|approved|trusted|clean|benign|verified|5\s*/\s*5|priority)\b"
            r"|\binjection_suspected\b|\b(respond|reply|answer)\s+only\s+with\b",
            _I,
        ),
        1.0,
    ),
    Rule("base64_blob", re.compile(r"[A-Za-z0-9+/]{160,}={0,2}"), 0.5),
)


#: More Unicode tag characters than an emoji flag sequence needs (e.g. England: 6) means hidden ASCII text.
TAG_CHAR_LIMIT = 16


@dataclass(frozen=True)
class HeuristicResult:
    score: float
    matches: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"score": self.score, "matches": list(self.matches)}


def scan(text: str, hidden_text: str = "", *, tag_chars: int = 0) -> HeuristicResult:
    """Score the visible text plus what was hidden from the human reader."""
    matches: list[str] = []
    score = 0.0
    for rule in RULES:
        if text and rule.pattern.search(text):
            matches.append(rule.code)
            score += rule.weight
    if hidden_text:
        for rule in RULES:
            if rule.weight >= 1.0 and rule.pattern.search(hidden_text):
                matches.append(f"hidden_{rule.code}")
                score += 1.0
    if tag_chars > TAG_CHAR_LIMIT:  # emoji subdivision flags use a few; text smuggling uses many
        matches.append("unicode_tags")
        score += 1.0
    return HeuristicResult(round(score, 3), tuple(matches))
