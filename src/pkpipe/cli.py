"""Command line entry point. Subcommands are stubs until the engine lands."""

import argparse

from pkpipe import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pkpipe", description="Local-first agent pipeline")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="start API, UI, scheduler and app MCP server")
    run = sub.add_parser("run", help="run one flow now")
    run.add_argument("flow")
    ev = sub.add_parser("eval", help="run an eval suite")
    ev.add_argument("--suite", default="all")
    sub.add_parser("validate", help="validate the layered config")
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0
    print(f"'{args.cmd}' is not implemented yet")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
