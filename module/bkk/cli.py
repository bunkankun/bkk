"""Top-level ``bkk`` dispatcher.

Routes ``bkk <subcommand> ...`` to the matching sub-package CLI:

    bkk import   ...   -> bkk.importer.cli:run
    bkk export   ...   -> bkk.exporter.cli:run
    bkk index    ...   -> bkk.index.cli:run
    bkk validate ...   -> bkk.validator.cli:main
    bkk serve    ...   -> bkk.serve.cli:run

The dispatcher parses only the first positional (the subcommand name); every
remaining argument is forwarded verbatim to the sub-CLI so each one keeps its
own ``--help`` and option grammar.
"""

from __future__ import annotations

import sys
from typing import Callable

# Each entry is (loader, label). The loader returns a callable taking
# ``argv`` (list[str]) and returning an int exit code. Loaders are lazy so
# that ``bkk index ...`` does not import FastAPI, etc.
SubCommand = Callable[[list[str]], int]


def _load_importer() -> SubCommand:
    from bkk.importer.cli import run
    return run


def _load_exporter() -> SubCommand:
    from bkk.exporter.cli import run
    return run


def _load_index() -> SubCommand:
    from bkk.index.cli import run
    return run


def _load_validator() -> SubCommand:
    from bkk.validator.cli import main as run
    return run


def _load_serve() -> SubCommand:
    from bkk.serve.cli import run
    return run


SUBCOMMANDS: dict[str, tuple[Callable[[], SubCommand], str]] = {
    "import":   (_load_importer,  "import an external source (TLS, KRP) into a BKK bundle"),
    "export":   (_load_exporter,  "export bundles via a recipe to TEI/etc."),
    "index":    (_load_index,     "build / merge / search the corpus index (.bkkx)"),
    "validate": (_load_validator, "validate a bundle directory"),
    "serve":    (_load_serve,     "run the HTTP server over a corpus"),
}

# Aliases so familiar verbs work too.
ALIASES: dict[str, str] = {
    "importer":  "import",
    "exporter":  "export",
    "validator": "validate",
}


def _print_help() -> None:
    print("usage: bkk <subcommand> [args...]\n")
    print("subcommands:")
    width = max(len(name) for name in SUBCOMMANDS)
    for name, (_, descr) in SUBCOMMANDS.items():
        print(f"  {name:<{width}}  {descr}")
    print("\nRun ``bkk <subcommand> --help`` for subcommand options.")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        _print_help()
        return 0

    name = args[0]
    name = ALIASES.get(name, name)
    if name not in SUBCOMMANDS:
        print(f"bkk: unknown subcommand {args[0]!r}\n", file=sys.stderr)
        _print_help()
        return 2

    loader, _ = SUBCOMMANDS[name]
    sub_run = loader()
    rc = sub_run(args[1:])
    return int(rc or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
