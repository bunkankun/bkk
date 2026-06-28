"""Top-level ``bkk`` dispatcher.

Routes ``bkk <subcommand> ...`` to the matching sub-package CLI:

    bkk import   ...   -> bkk.importer.cli:run
    bkk export   ...   -> bkk.exporter.cli:run
    bkk index    ...   -> bkk.index.cli:run
    bkk validate ...   -> bkk.validator.cli:main
    bkk serve    ...   -> bkk.serve.cli:run
    bkk repair   ...   -> bkk.repair.cli:run
    bkk voice    ...   -> bkk.voice.cli:run
    bkk recipe   ...   -> bkk.recipe.cli:run
    bkk info     ...   -> bkk.info.cli:run
    bkk annotations ... -> bkk.annotations.cli:run
    bkk core     ...   -> bkk.core_cli.cli:run

The dispatcher parses only the first positional (the subcommand name); every
remaining argument is forwarded verbatim to the sub-CLI so each one keeps its
own ``--help`` and option grammar.
"""

from __future__ import annotations

import signal
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


def _load_repair() -> SubCommand:
    from bkk.repair.cli import run
    return run


def _load_voice() -> SubCommand:
    from bkk.voice.cli import run
    return run


def _load_recipe() -> SubCommand:
    from bkk.recipe.cli import run
    return run


def _load_info() -> SubCommand:
    from bkk.info.cli import run
    return run


def _load_annotations() -> SubCommand:
    from bkk.annotations.cli import run
    return run


def _load_core() -> SubCommand:
    from bkk.core_cli.cli import run
    return run


def _load_chars() -> SubCommand:
    from bkk.chars.cli import run
    return run


def _load_repo() -> SubCommand:
    from bkk.repo.cli import run
    return run


SUBCOMMANDS: dict[str, tuple[Callable[[], SubCommand], str]] = {
    "import":   (_load_importer,  "import an external source (TLS, KRP) into a BKK bundle"),
    "export":   (_load_exporter,  "export bundles via a recipe to TEI/etc."),
    "index":    (_load_index,     "build / merge / search the corpus index (.bkkx)"),
    "validate": (_load_validator, "validate a bundle directory"),
    "serve":    (_load_serve,     "run the HTTP server over a corpus"),
    "repair":   (_load_repair,    "repair a bundle (e.g. rebuild manifests from juan files)"),
    "voice":    (_load_voice,     "derive voice markers from (...) punctuation in a bundle"),
    "recipe":   (_load_recipe,    "render recipe templates"),
    "info":     (_load_info,      "show corpus, index, and config summary"),
    "annotations": (_load_annotations, "harvest Bluesky annotation records into the archive"),
    "core":     (_load_core,      "maintain the bkk-core knowledge layer (sync, …)"),
    "chars":    (_load_chars,     "canonicalize text against the BKK character set"),
    "repo":     (_load_repo,      "manage text bundles as git repositories"),
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
    # Restore default SIGPIPE so piping output to ``head`` etc. exits cleanly
    # instead of raising BrokenPipeError on the next print.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)

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
