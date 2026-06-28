"""``bkk repo`` — manage text bundles as git repositories.

Each bundle dir (``<corpus>/<section>/<textid8>/``) becomes a standalone git
repo. Source files (``*.bkkx`` SQLite index, ``*.source.yaml`` cache) are
gitignored; manifest + juan YAMLs are tracked.

Actions: ``init``, ``clone``, ``commit``, ``push``, ``pull``, ``status``.

Scope: a positional ``<prefix>`` (textid8/4/3, e.g. ``KR1a0001``, ``KR1a``)
or ``--all``. The prefix is forwarded to
:func:`bkk.index.merge.discover_bundles`.

GitHub interaction shells out to ``gh``; git interaction shells out to
``git``. The remote org defaults to ``bkkbooks`` and is configurable via
``[repo].github_org`` in ``.bkkrc``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

from bkk.config import load_rc
from bkk.index.merge import discover_bundles

_GITIGNORE = """\
*.bkkx
*.bkkx-journal
*.source.yaml
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bkk repo",
        description=(
            "Manage text bundles as git repositories. `init` is local-only "
            "by default; pass `--github` (or run `publish` later) to create "
            "the corresponding remote in the configured GitHub org."
        ),
    )
    p.add_argument(
        "--corpus", type=Path, default=None,
        help="corpus root (default: [repo].corpus, [info].corpus, [global].corpus)",
    )
    sub = p.add_subparsers(dest="action", required=True)

    def _scope(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "prefix", nargs="?",
            help="textid prefix (8/4/3 chars). Omit together with --all.",
        )
        sp.add_argument(
            "--all", action="store_true", dest="all_flag",
            help="operate on every bundle under the corpus root",
        )
        sp.add_argument(
            "--dry-run", action="store_true",
            help="print planned actions without executing",
        )

    p_init = sub.add_parser(
        "init",
        help=(
            "git-init bundles (README, .gitignore, initial commit); "
            "pass --github to also create <org>/<textid> on GitHub and push"
        ),
    )
    _scope(p_init)
    p_init.add_argument(
        "--github", action="store_true",
        help="also create <org>/<textid> on GitHub and push (else publish later)",
    )

    p_clone = sub.add_parser(
        "clone", help="clone bundles from the GitHub org into the corpus",
    )
    p_clone.add_argument("prefix", nargs="?")
    p_clone.add_argument("--all", action="store_true", dest="all_flag")
    p_clone.add_argument("--dry-run", action="store_true")

    p_commit = sub.add_parser("commit", help="stage and commit local changes")
    _scope(p_commit)
    p_commit.add_argument(
        "-m", "--message", default=None,
        help="commit message (default: 'Update <textid>')",
    )

    p_publish = sub.add_parser(
        "publish",
        help="create <org>/<textid> on GitHub and push (for bundles already git-initialized)",
    )
    _scope(p_publish)

    p_push = sub.add_parser("push", help="push to origin")
    _scope(p_push)

    p_update = sub.add_parser(
        "update",
        help="commit local changes (if any) and push to origin",
    )
    _scope(p_update)
    p_update.add_argument(
        "-m", "--message", default=None,
        help="commit message (default: 'Update <textid>')",
    )

    p_pull = sub.add_parser("pull", help="pull --ff-only from origin")
    _scope(p_pull)

    p_status = sub.add_parser("status", help="summarize per-bundle git status")
    _scope(p_status)

    return p


def _resolve_corpus(args: argparse.Namespace, rc: dict) -> Path:
    corpus = (
        args.corpus
        or rc.get("repo", {}).get("corpus")
        or rc.get("info", {}).get("corpus")
        or rc.get("global", {}).get("corpus")
    )
    if corpus is None:
        sys.exit(
            "bkk repo: corpus required (set global.corpus or repo.corpus in .bkkrc, "
            "or pass --corpus)"
        )
    return Path(corpus)


def _resolve_bundles(corpus: Path, prefix: str | None, all_flag: bool) -> list[Path]:
    if not all_flag and not prefix:
        sys.exit("bkk repo: provide a textid prefix (8/4/3 chars) or --all")
    return discover_bundles(corpus, prefix=prefix)


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
    )


def _first_err_line(proc: subprocess.CompletedProcess) -> str:
    msg = (proc.stderr or proc.stdout or "").strip()
    return msg.splitlines()[0] if msg else f"exit {proc.returncode}"


def _is_repo(bundle_dir: Path) -> bool:
    return (bundle_dir / ".git").is_dir()


_TITLES_CACHE: dict[str, str] | None = None


def _load_titles() -> dict[str, str]:
    """Lazy-load ``catalog/krp-titles.txt`` once per process."""
    global _TITLES_CACHE
    if _TITLES_CACHE is None:
        from bkk.repair.krp_titles import default_titles_path, parse_titles
        p = default_titles_path()
        _TITLES_CACHE = parse_titles(p) if p.is_file() else {}
    return _TITLES_CACHE


def _read_description(bundle_dir: Path) -> str:
    """Build the GitHub repo description.

    Format: ``<alt_ids> <title-tail>`` where the lead is the
    ``metadata.identifiers.alt_id`` list (space-joined, omitted when absent)
    and the tail is the ``<title>-<dynasty>-<author>`` line from
    ``catalog/krp-titles.txt`` — falling back to ``metadata.title`` from the
    manifest when the titles file has no entry for this text.
    """
    textid = bundle_dir.name
    manifest = bundle_dir / f"{textid}.manifest.yaml"
    ids: list[str] = []
    manifest_title = ""
    if manifest.is_file():
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        meta = data.get("metadata") or {}
        manifest_title = meta.get("title") or ""
        alt = (meta.get("identifiers") or {}).get("alt_id") or []
        if isinstance(alt, str):
            ids = [alt]
        elif isinstance(alt, list):
            ids = [str(x) for x in alt if x]
    tail = _load_titles().get(textid) or manifest_title
    lead = " ".join(ids)
    return f"{lead} {tail}".strip()


def _catalog_path(corpus: Path, rc: dict) -> Path:
    info_rc = rc.get("info", {})
    serve_rc = rc.get("serve", {})
    return Path(
        info_rc.get("catalog") or serve_rc.get("catalog") or corpus / "_catalog.bkkc"
    )


def _action_init(
    bundle_dir: Path,
    *,
    corpus: Path,
    rc: dict,
    github: bool,
    org: str,
    visibility: str,
    default_branch: str,
    create_delay_s: float,
    dry_run: bool,
) -> str:
    textid = bundle_dir.name
    if _is_repo(bundle_dir):
        return "skipped (already a repo)"
    if dry_run:
        steps = ["readme", ".gitignore", "git init/add/commit"]
        if github:
            steps.append(f"gh repo create {org}/{textid}")
        return "plan: " + ", ".join(steps)

    from bkk.info.cli import write_readme
    try:
        write_readme(textid, corpus, _catalog_path(corpus, rc), fix_editions=True)
    except Exception as exc:  # noqa: BLE001 — surface message, keep batch going
        return f"error: readme: {exc}"

    (bundle_dir / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")

    for cmd in (
        ["git", "init", "-b", default_branch],
        ["git", "add", "-A"],
        ["git", "commit", "-m", "Initial commit"],
    ):
        r = _run(cmd, cwd=bundle_dir)
        if r.returncode != 0:
            return f"error: {' '.join(cmd[:2])}: {_first_err_line(r)}"

    if github:
        cmd = ["gh", "repo", "create", f"{org}/{textid}", f"--{visibility}"]
        desc = _read_description(bundle_dir)
        if desc:
            cmd.extend(["--description", desc])
        cmd.extend(["--source", str(bundle_dir), "--push"])
        if create_delay_s > 0:
            time.sleep(create_delay_s)
        r = _run(cmd)
        if r.returncode != 0:
            return f"partial: local repo created, github pending ({_first_err_line(r)})"
    return "ok"


def _action_clone(
    corpus: Path,
    prefix: str | None,
    all_flag: bool,
    org: str,
    dry_run: bool,
) -> int:
    if not all_flag and not prefix:
        sys.exit("bkk repo clone: provide a textid prefix or --all")
    r = _run(["gh", "repo", "list", org, "--limit", "5000", "--json", "name"])
    if r.returncode != 0:
        sys.exit(f"gh repo list {org} failed: {r.stderr.strip() or r.stdout.strip()}")
    names = [item["name"] for item in json.loads(r.stdout or "[]")]
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    names.sort()
    if not names:
        print("no matching repos in org", file=sys.stderr)
        return 0

    ok = skipped = errors = 0
    for name in names:
        section = name[:4]
        target = corpus / section / name
        if target.exists():
            print(f"{name}  skipped (exists)")
            skipped += 1
            continue
        if dry_run:
            print(f"{name}  plan: gh repo clone {org}/{name} {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        cr = _run(["gh", "repo", "clone", f"{org}/{name}", str(target)])
        if cr.returncode != 0:
            print(f"{name}  error: {_first_err_line(cr)}")
            errors += 1
        else:
            print(f"{name}  ok")
            ok += 1
    print(f"\n{ok} ok, {skipped} skipped, {errors} errors", file=sys.stderr)
    return 1 if errors else 0


def _action_commit(bundle_dir: Path, message: str | None, dry_run: bool) -> str:
    textid = bundle_dir.name
    if not _is_repo(bundle_dir):
        return "skipped (not a repo)"
    r = _run(["git", "status", "--porcelain"], cwd=bundle_dir)
    if r.returncode != 0:
        return f"error: git status: {_first_err_line(r)}"
    if not r.stdout.strip():
        return "skipped (no changes)"
    if dry_run:
        return "plan: git add -A && git commit"
    msg = message or f"Update {textid}"
    for cmd in (["git", "add", "-A"], ["git", "commit", "-m", msg]):
        r = _run(cmd, cwd=bundle_dir)
        if r.returncode != 0:
            return f"error: {' '.join(cmd[:2])}: {_first_err_line(r)}"
    sha = _run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=bundle_dir,
    ).stdout.strip()
    return f"committed {sha}"


def _action_publish(
    bundle_dir: Path,
    *,
    org: str,
    visibility: str,
    create_delay_s: float,
    dry_run: bool,
) -> str:
    textid = bundle_dir.name
    if not _is_repo(bundle_dir):
        return "skipped (not a repo)"
    r = _run(["git", "remote", "get-url", "origin"], cwd=bundle_dir)
    if r.returncode == 0:
        return f"skipped (origin exists: {r.stdout.strip()})"
    desc = _read_description(bundle_dir)
    if dry_run:
        return f"plan: gh repo create {org}/{textid} --{visibility} --push"
    cmd = ["gh", "repo", "create", f"{org}/{textid}", f"--{visibility}"]
    if desc:
        cmd.extend(["--description", desc])
    cmd.extend(["--source", str(bundle_dir), "--push"])
    if create_delay_s > 0:
        time.sleep(create_delay_s)
    r = _run(cmd)
    if r.returncode != 0:
        return f"error: gh repo create: {_first_err_line(r)}"
    return "ok"


def _action_update(
    bundle_dir: Path, message: str | None, dry_run: bool,
) -> str:
    textid = bundle_dir.name
    if not _is_repo(bundle_dir):
        return "skipped (not a repo)"
    r = _run(["git", "status", "--porcelain"], cwd=bundle_dir)
    if r.returncode != 0:
        return f"error: git status: {_first_err_line(r)}"
    dirty = bool(r.stdout.strip())

    rev = _run(["git", "rev-list", "--count", "@{u}..HEAD"], cwd=bundle_dir)
    ahead = (
        int(rev.stdout.strip())
        if rev.returncode == 0 and rev.stdout.strip().isdigit() else 0
    )
    if not dirty and ahead == 0:
        return "skipped (no changes)"

    if dry_run:
        steps = (["commit"] if dirty else []) + ["push"]
        return "plan: " + " + ".join(steps)

    parts: list[str] = []
    if dirty:
        msg = message or f"Update {textid}"
        for cmd in (["git", "add", "-A"], ["git", "commit", "-m", msg]):
            r = _run(cmd, cwd=bundle_dir)
            if r.returncode != 0:
                return f"error: {' '.join(cmd[:2])}: {_first_err_line(r)}"
        sha = _run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=bundle_dir,
        ).stdout.strip()
        parts.append(f"committed {sha}")
    r = _run(["git", "push"], cwd=bundle_dir)
    if r.returncode != 0:
        return f"error: git push: {_first_err_line(r)}"
    parts.append("pushed")
    return " + ".join(parts)


def _action_push(bundle_dir: Path, dry_run: bool) -> str:
    if not _is_repo(bundle_dir):
        return "skipped (not a repo)"
    if dry_run:
        return "plan: git push"
    r = _run(["git", "push"], cwd=bundle_dir)
    if r.returncode != 0:
        return f"error: git push: {_first_err_line(r)}"
    return "ok"


def _action_pull(bundle_dir: Path, dry_run: bool) -> str:
    if not _is_repo(bundle_dir):
        return "skipped (not a repo)"
    if dry_run:
        return "plan: git pull --ff-only"
    r = _run(["git", "pull", "--ff-only"], cwd=bundle_dir)
    if r.returncode != 0:
        return f"error: git pull: {_first_err_line(r)}"
    tail = r.stdout.strip().splitlines()
    return tail[-1] if tail else "ok"


def _action_status(bundle_dir: Path) -> str:
    if not _is_repo(bundle_dir):
        return "not a repo"
    porcelain = _run(["git", "status", "--porcelain"], cwd=bundle_dir).stdout
    base = "dirty" if porcelain.strip() else "clean"
    rev = _run(
        ["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"],
        cwd=bundle_dir,
    )
    if rev.returncode == 0 and rev.stdout.strip():
        parts = rev.stdout.strip().split()
        if len(parts) == 2:
            behind, ahead = parts
            if int(ahead) or int(behind):
                base += f" (ahead {ahead}, behind {behind})"
    else:
        base += " (no upstream)"
    return base


def run(argv: list[str] | None = None) -> int:
    rc = load_rc()
    parser = build_parser()
    args = parser.parse_args(argv)
    corpus = _resolve_corpus(args, rc)

    repo_rc = rc.get("repo", {})
    org = repo_rc.get("github_org", "bkkbooks")
    visibility = repo_rc.get("visibility", "public")
    default_branch = repo_rc.get("default_branch", "main")
    create_delay_s = float(repo_rc.get("create_delay_s", 2.0))

    if args.action == "clone":
        return _action_clone(corpus, args.prefix, args.all_flag, org, args.dry_run)

    bundles = _resolve_bundles(corpus, args.prefix, args.all_flag)
    if not bundles:
        print("no bundles matched", file=sys.stderr)
        return 0

    ok = skipped = errors = partial = 0
    for b in bundles:
        if args.action == "init":
            result = _action_init(
                b, corpus=corpus, rc=rc,
                github=args.github, org=org, visibility=visibility,
                default_branch=default_branch,
                create_delay_s=create_delay_s, dry_run=args.dry_run,
            )
        elif args.action == "commit":
            result = _action_commit(b, args.message, args.dry_run)
        elif args.action == "publish":
            result = _action_publish(
                b, org=org, visibility=visibility,
                create_delay_s=create_delay_s, dry_run=args.dry_run,
            )
        elif args.action == "push":
            result = _action_push(b, args.dry_run)
        elif args.action == "update":
            result = _action_update(b, args.message, args.dry_run)
        elif args.action == "pull":
            result = _action_pull(b, args.dry_run)
        elif args.action == "status":
            result = _action_status(b)
        else:
            sys.exit(f"bkk repo: unknown action: {args.action}")
        print(f"{b.name}  {result}")
        if result.startswith("error"):
            errors += 1
        elif result.startswith("partial"):
            partial += 1
        elif result.startswith("skipped") or result.startswith("not a repo"):
            skipped += 1
        else:
            ok += 1
    print(
        f"\n{ok} ok, {partial} partial, {skipped} skipped, {errors} errors",
        file=sys.stderr,
    )
    return 1 if errors else 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
