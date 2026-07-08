"""Shared helpers for BKK command-line entry points."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

from bkk.index.merge import find_bundle
from bkk.short_refs import text_id_arg, text_prefix_arg


def warn_deprecated(old: str, new: str) -> None:
    """Emit a consistent compatibility warning for old CLI forms."""
    print(
        f"warning: {old} is deprecated; use {new}",
        file=sys.stderr,
    )


def add_text_id(
    parser: argparse.ArgumentParser,
    *,
    dest: str = "text_id",
    repeatable: bool = False,
    help: str | None = None,
) -> None:
    kwargs = {
        "dest": dest,
        "default": None,
        "type": text_id_arg,
        "help": help or "single text id (e.g. KR6q0053 or shortcut 6q53)",
    }
    if repeatable:
        kwargs["action"] = "append"
    parser.add_argument("--text-id", **kwargs)


def add_text_prefix(
    parser: argparse.ArgumentParser,
    *,
    dest: str = "text_prefix",
    action: str | None = None,
    required: bool = False,
    help: str | None = None,
) -> None:
    kwargs: dict = {
        "dest": dest,
        "default": None,
        "type": text_prefix_arg,
        "required": required,
        "help": help
        or "restrict to text ids starting with this prefix (e.g. KR3a)",
    }
    if action is not None:
        kwargs["action"] = action
    parser.add_argument("--text-prefix", **kwargs)


def resolve_rc_path(
    explicit: Path | str | None,
    rc: dict,
    fallbacks: Iterable[tuple[str, str]],
) -> Path | None:
    """Resolve a CLI path with ordered ``.bkkrc`` section/key fallbacks."""
    if explicit is not None:
        return Path(explicit)
    for section, key in fallbacks:
        value = rc.get(section, {}).get(key)
        if value is not None:
            return Path(value)
    return None


def resolve_bundle_dir(
    *,
    bundle: str | Path | None = None,
    text_id: str | None = None,
    root: Path | str | None = None,
) -> Path:
    """Resolve either an explicit bundle path or a text id under ``root``."""
    if bundle is not None and text_id is not None:
        raise ValueError("provide either --bundle or --text-id, not both")

    if bundle is not None:
        raw = str(bundle)
        path = Path(raw).expanduser()
        if path.is_dir():
            return path.resolve()
        if root is not None and "/" not in raw and "\\" not in raw:
            found = find_bundle(Path(root).expanduser(), raw)
            candidate = (Path(root).expanduser() / raw).resolve()
            if found is not None:
                return found.resolve()
            raise FileNotFoundError(
                f"bundle directory not found: tried {path} and {candidate}"
            )
        raise FileNotFoundError(f"bundle directory not found: {path}")

    if text_id is None:
        raise ValueError("provide --bundle or --text-id")
    if root is None:
        raise FileNotFoundError(
            "bundle directory not found: bundle root not configured; "
            "pass --bundle or configure/pass a corpus root"
        )
    found = find_bundle(Path(root).expanduser(), text_id)
    if found is None:
        raise FileNotFoundError(
            f"bundle directory not found for {text_id!r} under {Path(root)}"
        )
    return found.resolve()
