"""Command-line interface for LLM-backed BKK asset generation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from bkk.cli_common import add_text_prefix, resolve_rc_path, warn_deprecated
from bkk.config import load_rc
from bkk.short_refs import parse_text_juan_selector, text_id_arg, text_or_path_arg

from .punctuation import (
    collect_batch,
    inspect_batch,
    list_available_models,
    retry_failed_batch,
    run_direct,
    settings_from_rc,
    submit_batch,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bkk llm")
    sub = parser.add_subparsers(dest="task", required=True)
    models = sub.add_parser("models", help="list OpenAI models available to the configured key")
    models.add_argument("--ai-config", dest="ai_config", type=Path, default=None)
    models.add_argument(
        "--contains", default=None,
        help="case-insensitive substring filter for model ids",
    )
    models.add_argument("--json", action="store_true", help="emit full JSON records")

    punct = sub.add_parser("punctuation", help="generate LLM punctuation assets")
    punct_sub = punct.add_subparsers(dest="op", required=True)

    pr = punct_sub.add_parser("run", help="run punctuation requests directly")
    _add_selection(pr)
    _add_settings(pr)
    pr.add_argument(
        "--mode", choices=("direct", "batch", "auto"), default="direct",
        help="direct runs now; batch/auto submit an async batch job",
    )
    pr.add_argument("--dry-run", action="store_true")

    ps = punct_sub.add_parser("submit", help="submit an async OpenAI batch")
    _add_selection(ps)
    _add_settings(ps)

    pc = punct_sub.add_parser("collect", help="collect an async batch result")
    pc.add_argument("state", type=Path, help="state YAML written by submit")
    _add_settings(pc, selection=False)
    pi = punct_sub.add_parser("inspect", help="inspect async batch status and diagnostics")
    pi.add_argument("state", type=Path, help="state YAML written by submit")
    _add_settings(pi, selection=False)
    prt = punct_sub.add_parser(
        "retry",
        help="submit a new async batch for chunks that failed or were rejected",
    )
    prt.add_argument("state", type=Path, help="state YAML written by submit or retry")
    _add_settings(prt, selection=False)
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    rc = load_rc()
    out_root = resolve_rc_path(
        getattr(args, "out_root", None), rc, (("global", "corpus"),),
    )
    try:
        if args.task == "models":
            ai_config = _ai_config_path(rc, getattr(args, "ai_config", None))
            models = list_available_models(
                ai_config,
                contains=getattr(args, "contains", None),
            )
            if getattr(args, "json", False):
                print(json.dumps(models, ensure_ascii=False, indent=2))
            else:
                for model in models:
                    print(model["id"])
            return 0
        if args.task != "punctuation":
            parser.error("unknown task")
        if args.op in {"collect", "inspect", "retry"}:
            state = _load_state(args.state)
            settings = settings_from_rc(
                rc,
                model=getattr(args, "model", None) or state.get("model"),
                ai_config=getattr(args, "ai_config", None),
                prompt=getattr(args, "prompt", None) or state.get("prompt_path"),
                chunk_chars=getattr(args, "chunk_chars", None) or state.get("chunk_chars"),
                overlap=getattr(args, "overlap", None) or state.get("overlap"),
                min_chars=getattr(args, "min_chars", None) or state.get("min_chars"),
                cache_dir=getattr(args, "cache_dir", None),
            )
            if args.op == "inspect":
                return inspect_batch(args.state, settings=settings)
            if args.op == "retry":
                retry_state = retry_failed_batch(args.state, settings=settings)
                print(f"submitted retry batch; state: {retry_state}")
                return 0
            return collect_batch(args.state, settings=settings)
        settings = settings_from_rc(
            rc,
            model=getattr(args, "model", None),
            ai_config=getattr(args, "ai_config", None),
            prompt=getattr(args, "prompt", None),
            chunk_chars=getattr(args, "chunk_chars", None),
            overlap=getattr(args, "overlap", None),
            min_chars=getattr(args, "min_chars", None),
            cache_dir=getattr(args, "cache_dir", None),
        )
        bundle, text_id, text_prefix, selected_juans = _selected_args(args)
        if args.op == "submit" or getattr(args, "mode", "direct") in {"batch", "auto"}:
            state = submit_batch(
                bundle, out_root, text_id=text_id, text_prefix=text_prefix,
                selected_juans=selected_juans, settings=settings,
                include_editions=bool(args.include_editions),
            )
            print(f"submitted batch; state: {state}")
            return 0
        return run_direct(
            bundle, out_root, text_id=text_id, text_prefix=text_prefix,
            selected_juans=selected_juans, settings=settings,
            dry_run=bool(args.dry_run),
            include_editions=bool(args.include_editions),
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _add_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("legacy_bundle", nargs="?", type=text_or_path_arg,
                        help=argparse.SUPPRESS)
    parser.add_argument("--bundle", type=Path, default=None)
    parser.add_argument("--text-id", dest="text_id", type=text_id_arg, default=None)
    add_text_prefix(parser)
    parser.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root used to resolve --text-id/--text-prefix",
    )
    parser.add_argument(
        "--juan", dest="juan_selectors", action="append", default=None,
        help="restrict to one juan; repeatable; accepts TEXT/SEQ or bare seq",
    )
    parser.add_argument(
        "--include-editions",
        action="store_true",
        help="also process documentary editions; default is master only",
    )


def _add_settings(parser: argparse.ArgumentParser, *, selection: bool = True) -> None:
    del selection
    parser.add_argument("--model", default=None)
    parser.add_argument("--ai-config", dest="ai_config", type=Path, default=None)
    parser.add_argument("--prompt", type=Path, default=None)
    parser.add_argument("--chunk-chars", type=int, default=None)
    parser.add_argument("--overlap", type=int, default=None)
    parser.add_argument(
        "--min-chars",
        type=int,
        default=None,
        help="skip streams shorter than this many characters (default: 6)",
    )
    parser.add_argument("--cache-dir", type=Path, default=None)


def _selected_args(
    args: argparse.Namespace,
) -> tuple[str | Path | None, str | None, str | None, set[int] | None]:
    refs: list[tuple[str, int]] = []
    local: list[int] = []
    for raw in getattr(args, "juan_selectors", None) or []:
        value = str(raw).strip()
        if value.isdigit():
            local.append(int(value))
            continue
        text_id, seq = parse_text_juan_selector(value)
        if seq is None:
            raise ValueError(f"--juan selector {raw!r} must include a juan number")
        refs.append((text_id, seq))

    supplied_bundle = [
        bool(getattr(args, "legacy_bundle", None)),
        bool(getattr(args, "bundle", None)),
        bool(getattr(args, "text_id", None)),
        bool(getattr(args, "text_prefix", None)),
    ]
    if refs:
        if any(supplied_bundle):
            raise ValueError(
                "--juan TEXT/SEQ cannot be combined with --bundle, --text-id, "
                "or --text-prefix"
            )
        text_ids = {text_id for text_id, _ in refs}
        if len(text_ids) != 1:
            raise ValueError("--juan TEXT/SEQ selectors must all name the same text")
        return None, refs[0][0], None, {seq for _, seq in refs}

    if sum(supplied_bundle) != 1:
        raise ValueError("provide exactly one of --bundle, --text-id, or --text-prefix")
    if getattr(args, "legacy_bundle", None):
        legacy = args.legacy_bundle
        if "/" in legacy or "\\" in legacy or Path(legacy).is_dir():
            warn_deprecated("positional <bundle>", "--bundle <dir>")
            return legacy, None, None, set(local) if local else None
        warn_deprecated("positional <text-id>", "--text-id <text-id>")
        return None, legacy, None, set(local) if local else None
    return (
        args.bundle,
        args.text_id,
        args.text_prefix,
        set(local) if local else None,
    )


def _load_state(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: state file is not a mapping")
    return data


def _ai_config_path(rc: dict, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser()
    value = (rc.get("llm") or {}).get("ai_config")
    if value is not None:
        return Path(value).expanduser()
    return Path("~/ai-config.xml").expanduser()


def main() -> None:
    raise SystemExit(run())
