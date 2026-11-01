"""Datasets, rubrics and cassettes on disk (docs/evaluation.md "Datasets").

Layout below an evals root (the public ``evals/`` of this repository, then an overlay's ``evals/``)::

    datasets/triage.jsonl                    one EvalCase per line
    datasets/sessions/<flow>/<node>.jsonl     session (step) cases: inputs + must_include / must_not
    datasets/flows/*.json                     one flow case per file
    datasets/security.jsonl, injection.jsonl  guard detection and prompt-injection cases
    datasets/calibration/<rubric>.jsonl       outputs scored by the owner, for judge calibration
    rubrics/<name>.md                         LLM-judge rubrics
    cassettes/<suite>.json                    recorded model responses (CI replays them)

A later root wins per relative path, so the private overlay can replace or add datasets (constitution VII).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from pkpipe.evals.models import EvalCase

EVALS_ENV = "PKPIPE_EVALS_DIR"


class DatasetError(ValueError):
    """A dataset file is unreadable; the message names file and line."""


def default_evals_dir() -> Path:
    env = os.environ.get(EVALS_ENV)
    return Path(env) if env else Path(__file__).resolve().parents[3] / "evals"


@dataclass(frozen=True)
class EvalPaths:
    roots: tuple[Path, ...]
    cassette_dir: Path

    @classmethod
    def create(
        cls, evals_dir: Path | None = None, overlay_dir: Path | None = None, cassette_dir: Path | None = None
    ) -> EvalPaths:
        roots = [evals_dir or default_evals_dir()]
        if overlay_dir is not None and (overlay_dir / "evals").is_dir():
            roots.append(overlay_dir / "evals")
        return cls(tuple(roots), cassette_dir or roots[-1] / "cassettes")

    def find(self, relative: str) -> Path | None:
        for root in reversed(self.roots):
            p = root / relative
            if p.is_file():
                return p
        return None

    def glob(self, relative_dir: str, pattern: str) -> dict[str, Path]:
        """relative path -> file, later roots replacing earlier ones."""
        found: dict[str, Path] = {}
        for root in self.roots:
            base = root / relative_dir
            if base.is_dir():
                for p in sorted(base.glob(pattern)):
                    if p.is_file():
                        found[p.relative_to(root).as_posix()] = p
        return dict(sorted(found.items()))

    def rubric(self, name: str) -> str | None:
        p = self.find(f"rubrics/{name}.md")
        return p.read_text(encoding="utf-8") if p else None

    def cassette(self, suite: str) -> Path:
        return self.cassette_dir / f"{suite.replace(':', '_').replace('/', '_')}.json"


def load_jsonl(path: Path, suite: str) -> list[EvalCase]:
    """Read a ``.jsonl`` dataset; ``suite`` is filled in when a line omits it. Ids must be unique."""
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise DatasetError(f"{path}:{n}: expected a JSON object")
            raw.setdefault("suite", suite)
            case = EvalCase.model_validate(raw)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path}:{n}: invalid JSON ({exc.msg})") from exc
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(p) for p in first["loc"])
            raise DatasetError(f"{path}:{n}: {loc}: {first['msg']}") from exc
        if case.id in seen:
            raise DatasetError(f"{path}:{n}: duplicate case id '{case.id}'")
        seen.add(case.id)
        cases.append(case)
    return cases


def load_json_case(path: Path, suite: str) -> EvalCase:
    """A case stored as one JSON file (flow cases): ``id`` defaults to the file stem."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{path}: invalid JSON ({exc.msg} at line {exc.lineno})") from exc
    if not isinstance(raw, dict):
        raise DatasetError(f"{path}: expected a JSON object")
    raw.setdefault("id", path.stem)
    raw.setdefault("suite", suite)
    try:
        return EvalCase.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise DatasetError(f"{path}: {'.'.join(str(p) for p in first['loc'])}: {first['msg']}") from exc
