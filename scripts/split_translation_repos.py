#!/usr/bin/env python3
"""Split dense translation bundles into one GitHub-backed repo per translation.

Default source: ``global.corpus`` from .bkkrc.
Default destination: ``global.translation_root`` from .bkkrc.

The destination layout is:

    <translation-root>/<section>/<source-text-id>/<lang>/<bundle-id>/

Each bundle directory becomes a standalone git repository and, unless
``--no-github`` is passed, is created as a private repo under ``bkktranslations``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOT = REPO_ROOT / "module"
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from bkk.config import load_rc  # noqa: E402
from bkk.importer.source import section_prefix  # noqa: E402
from bkk.index.translation import discover_translation_bundles  # noqa: E402
from bkk.repo.cli import (  # noqa: E402
    _first_err_line,
    _is_repo,
    _origin_matches_org_repo,
    _run,
    _run_gh_with_rate_limit_backoff,
)
from bkk.serve.translations import load_translation_bundle  # noqa: E402


_GITIGNORE = """\
*.bkkt
*.bkkt-journal
*.bkkt.sha256.json
"""


@dataclass(frozen=True)
class TranslationRepoPlan:
    bundle_id: str
    source_textid: str
    language: str
    source_dir: Path
    target_dir: Path
    remote: str
    title: str | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Copy dense translation bundles to global.translation_root, "
            "initialize one git repo per translation, and publish private "
            "repos under bkktranslations."
        ),
    )
    parser.add_argument("--source-root", type=Path, default=None)
    parser.add_argument("--dest-root", type=Path, default=None)
    parser.add_argument("--org", default="bkktranslations")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--message", default="Initial translation bundle import")
    parser.add_argument("--text-prefix", default=None)
    parser.add_argument("--source-text-id", default=None)
    parser.add_argument("--lang", default=None)
    parser.add_argument("--translation-id", action="append", dest="translation_ids")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-github", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--create-delay-s", type=float, default=2.0)
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSONL status report path (default: <dest-root>/_translation_repo_split.jsonl)",
    )
    return parser


def _resolve_source_root(args: argparse.Namespace, rc: dict) -> Path:
    root = args.source_root or rc.get("global", {}).get("corpus")
    if root is None:
        sys.exit("source root required: pass --source-root or set global.corpus")
    return Path(root)


def _resolve_dest_root(args: argparse.Namespace, rc: dict) -> Path:
    root = args.dest_root or rc.get("global", {}).get("translation_root")
    if root is None:
        sys.exit("destination root required: pass --dest-root or set global.translation_root")
    return Path(root)


def _selected(plan: TranslationRepoPlan, args: argparse.Namespace) -> bool:
    if args.text_prefix and not plan.source_textid.startswith(args.text_prefix):
        return False
    if args.source_text_id and plan.source_textid != args.source_text_id:
        return False
    if args.lang and plan.language != args.lang:
        return False
    if args.translation_ids and plan.bundle_id not in set(args.translation_ids):
        return False
    return True


def plan_translation_repos(
    source_root: Path,
    dest_root: Path,
    *,
    org: str,
) -> list[TranslationRepoPlan]:
    plans: list[TranslationRepoPlan] = []
    for bundle_dir in discover_translation_bundles(source_root):
        try:
            bundle = load_translation_bundle(bundle_dir, include_juans=False)
        except Exception as exc:  # noqa: BLE001
            print(f"{bundle_dir}: skipped, could not read translation bundle: {exc}", file=sys.stderr)
            continue
        source_textid = bundle.source_textid or "_unknown"
        language = bundle.summary.language or "_unknown"
        target = (
            dest_root /
            section_prefix(source_textid) /
            source_textid /
            language /
            bundle.id
        )
        plans.append(TranslationRepoPlan(
            bundle_id=bundle.id,
            source_textid=source_textid,
            language=language,
            source_dir=bundle_dir,
            target_dir=target,
            remote=f"{org}/{bundle.id}",
            title=bundle.summary.title,
        ))
    return sorted(plans, key=lambda p: (p.source_textid, p.language, p.bundle_id))


def _copy_ignore(_dir: str, names: list[str]) -> set[str]:
    ignored = {".git", "__pycache__"}
    for name in names:
        if name.endswith((".bkkt", ".bkkt-journal", ".bkkt.sha256.json")):
            ignored.add(name)
    return ignored


def _copy_bundle(plan: TranslationRepoPlan, *, dry_run: bool) -> str | None:
    if plan.target_dir.exists():
        if _is_repo(plan.target_dir):
            return None
        return "target exists and is not a git repo"
    if dry_run:
        return None
    plan.target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(plan.source_dir, plan.target_dir, ignore=_copy_ignore)
    return None


def _ensure_local_repo(
    plan: TranslationRepoPlan,
    *,
    branch: str,
    message: str,
    dry_run: bool,
) -> str:
    if dry_run:
        return "plan: copy + git init/add/commit"

    err = _copy_bundle(plan, dry_run=False)
    if err:
        return f"error: {err}"

    (plan.target_dir / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")

    if not _is_repo(plan.target_dir):
        r = _run(["git", "init", "-b", branch], cwd=plan.target_dir)
        if r.returncode != 0:
            return f"error: git init: {_first_err_line(r)}"

    r = _run(["git", "status", "--porcelain"], cwd=plan.target_dir)
    if r.returncode != 0:
        return f"error: git status: {_first_err_line(r)}"
    dirty = bool(r.stdout.strip())

    has_commit = _run(["git", "rev-parse", "--verify", "HEAD"], cwd=plan.target_dir)
    if has_commit.returncode == 0 and not dirty:
        return "local clean"

    for cmd in (["git", "add", "-A"], ["git", "commit", "-m", message]):
        r = _run(cmd, cwd=plan.target_dir)
        if r.returncode != 0:
            return f"error: {' '.join(cmd[:2])}: {_first_err_line(r)}"
    sha = _run(["git", "rev-parse", "--short", "HEAD"], cwd=plan.target_dir).stdout.strip()
    return f"committed {sha}"


def _repo_description(plan: TranslationRepoPlan) -> str:
    parts = [plan.source_textid, plan.language]
    if plan.title:
        parts.append(plan.title)
    return " · ".join(parts)


def _push_existing_origin(plan: TranslationRepoPlan, *, dry_run: bool) -> str:
    if dry_run:
        return "plan: git push -u origin HEAD"
    r = _run(["git", "push", "-u", "origin", "HEAD"], cwd=plan.target_dir)
    if r.returncode != 0:
        return f"error: git push: {_first_err_line(r)}"
    return "pushed"


def _publish(
    plan: TranslationRepoPlan,
    *,
    org: str,
    create_delay_s: float,
    dry_run: bool,
) -> str:
    if dry_run:
        return f"plan: gh repo create {plan.remote} --private --push"

    r = _run(["git", "remote", "get-url", "origin"], cwd=plan.target_dir)
    if r.returncode == 0:
        ok, detail = _origin_matches_org_repo(plan.target_dir, org, plan.bundle_id)
        if not ok:
            return f"error: origin mismatch: {detail}"
        return _push_existing_origin(plan, dry_run=False)

    cmd = ["gh", "repo", "create", plan.remote, "--private"]
    desc = _repo_description(plan)
    if desc:
        cmd.extend(["--description", desc])
    cmd.extend(["--source", str(plan.target_dir), "--push"])
    if create_delay_s > 0:
        time.sleep(create_delay_s)
    r = _run_gh_with_rate_limit_backoff(cmd)
    if r.returncode == 0:
        return "published private"

    msg = (r.stderr or r.stdout or "").casefold()
    if "already exists" in msg or "name already exists" in msg:
        remote_url = f"https://github.com/{plan.remote}.git"
        rr = _run(["git", "remote", "add", "origin", remote_url], cwd=plan.target_dir)
        if rr.returncode != 0:
            return f"error: git remote add: {_first_err_line(rr)}"
        return _push_existing_origin(plan, dry_run=False)
    return f"error: gh repo create: {_first_err_line(r)}"


def _confirm(plans: list[TranslationRepoPlan], args: argparse.Namespace) -> None:
    if args.dry_run or args.yes:
        return
    print(
        f"About to create/sync {len(plans)} local translation repos"
        f"{' without GitHub' if args.no_github else ' and private GitHub repos'}."
    )
    answer = input("Continue? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        sys.exit("aborted")


def _write_report(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rc = load_rc()
    source_root = _resolve_source_root(args, rc)
    dest_root = _resolve_dest_root(args, rc)
    report = args.report or dest_root / "_translation_repo_split.jsonl"

    plans = [
        plan for plan in plan_translation_repos(source_root, dest_root, org=args.org)
        if _selected(plan, args)
    ]
    if not plans:
        print("no translation bundles matched", file=sys.stderr)
        return 0

    _confirm(plans, args)

    ok = skipped = errors = 0
    report_rows: list[dict] = []
    for plan in plans:
        local_status = _ensure_local_repo(
            plan,
            branch=args.branch,
            message=args.message,
            dry_run=args.dry_run,
        )
        if local_status.startswith("error"):
            final = local_status
            errors += 1
        elif args.no_github:
            final = local_status
            ok += 1
        else:
            publish_status = _publish(
                plan,
                org=args.org,
                create_delay_s=args.create_delay_s,
                dry_run=args.dry_run,
            )
            final = f"{local_status}; {publish_status}"
            if publish_status.startswith("error"):
                errors += 1
            elif local_status == "local clean" and publish_status == "pushed":
                skipped += 1
            else:
                ok += 1
        print(f"{plan.bundle_id}  {final}")
        report_rows.append({
            "bundle_id": plan.bundle_id,
            "source_textid": plan.source_textid,
            "language": plan.language,
            "source_dir": str(plan.source_dir),
            "target_dir": str(plan.target_dir),
            "remote": plan.remote,
            "status": final,
        })

    if not args.dry_run:
        _write_report(report, report_rows)
    print(f"\n{ok} ok, {skipped} skipped, {errors} errors", file=sys.stderr)
    return 1 if errors else 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
