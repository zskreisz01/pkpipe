"""``python -m pkpipe.evals.migrate_cassettes [--model PROFILE=ID] FILE...``: re-key format 1 model cassettes
offline to format 2 (model id in the key, :func:`pkpipe.evals.cassette.migrate_v1`). No model is called."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pkpipe.evals.cassette import FORMAT, MigrationError, migrate_v1


def main(argv: list[str] | None = None) -> int:
    """Re-key the given cassettes; exit 1 on the first one that cannot be migrated deterministically."""
    parser = argparse.ArgumentParser(prog="python -m pkpipe.evals.migrate_cassettes")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument(
        "--model",
        action="append",
        default=[],
        metavar="PROFILE=ID",
        help="model id for entries of PROFILE recorded without one",
    )
    args = parser.parse_args(argv)
    models = dict(m.split("=", 1) for m in args.model)
    try:
        for f in args.files:
            n = migrate_v1(f, models=models)
            print(f"{f}: already format {FORMAT}" if n is None else f"{f}: {n} entries re-keyed")
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
