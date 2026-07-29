"""Apply legacy KRP ``&KRnnnn;`` replacement tables to source clones."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re
import subprocess
import sys
from pathlib import Path

from bkk.importer.source import list_local_text_ids, resolve_local_repo

from .refs import DEFAULT_REFS_DIR


NORM_LIST_FILENAME = "normlist-2016-02-05.txt"
REP_LIST_FILENAME = "replist-2016-02-05.txt"

_KRP_ENTITY_RE = re.compile(r"&(?P<key>KR\d{4});")


@dataclass
class KrpRepRepoResult:
    repo: Path
    branch: str
    list_name: str
    scanned_files: int = 0
    changed_files: int = 0
    replacements: int = 0
    unresolved: Counter[str] = field(default_factory=Counter)
    changed_paths: list[Path] = field(default_factory=list)
    error: str | None = None


def load_krp_replacement_list(path: Path) -> dict[str, str]:
    """Load a tab-separated ``KR####`` replacement table."""
    table: dict[str, str] = {}
    text = Path(path).read_text(encoding="utf-8")
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" not in raw:
            raise ValueError(f"{path.name}:{lineno}: expected <KRID><tab><replacement>")
        key, replacement = raw.split("\t", 1)
        key = key.strip()
        if not re.fullmatch(r"KR\d{4}", key):
            raise ValueError(f"{path.name}:{lineno}: invalid key {key!r}")
        if not replacement:
            raise ValueError(f"{path.name}:{lineno}: empty replacement for {key}")
        table[key] = replacement
    return table


def apply_krp_replacements(text: str, table: dict[str, str]) -> tuple[str, int, Counter[str]]:
    """Replace mapped KRP entity references, preserving unmapped references."""
    replacements = 0
    unresolved: Counter[str] = Counter()

    def repl(match: re.Match[str]) -> str:
        nonlocal replacements
        key = match.group("key")
        value = table.get(key)
        if value is None:
            unresolved[key] += 1
            return match.group(0)
        replacements += 1
        return value

    return _KRP_ENTITY_RE.sub(repl, text), replacements, unresolved


def run_krp_rep(
    *,
    repos: list[Path] | None = None,
    krp_root: Path | None = None,
    text_ids: list[str] | None = None,
    text_prefixes: list[str] | None = None,
    all_repos: bool = False,
    refs_dir: Path = DEFAULT_REFS_DIR,
    dry_run: bool = True,
    master_branch: str = "master",
) -> int:
    """Apply KRP entity replacement tables to selected source repos."""
    try:
        targets = _select_repos(
            repos=repos,
            krp_root=krp_root,
            text_ids=text_ids,
            text_prefixes=text_prefixes,
            all_repos=all_repos,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not targets:
        print("no KRP repos selected", file=sys.stderr)
        return 1

    refs = Path(refs_dir).expanduser().resolve()
    try:
        norm_table = load_krp_replacement_list(refs / NORM_LIST_FILENAME)
        rep_table = load_krp_replacement_list(refs / REP_LIST_FILENAME)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    results: list[KrpRepRepoResult] = []
    for repo in targets:
        result = process_krp_repo(
            repo,
            norm_table=norm_table,
            rep_table=rep_table,
            dry_run=dry_run,
            master_branch=master_branch,
        )
        results.append(result)
        _print_repo_result(result, dry_run=dry_run)

    total_scanned = sum(r.scanned_files for r in results)
    total_changed = sum(r.changed_files for r in results)
    total_replacements = sum(r.replacements for r in results)
    unresolved: Counter[str] = Counter()
    for result in results:
        unresolved.update(result.unresolved)
    failed = [r for r in results if r.error]

    verb = "would replace" if dry_run else "replaced"
    print(
        f"{verb} {total_replacements} KRP entity occurrence(s) in "
        f"{total_changed}/{total_scanned} worktree file(s) across "
        f"{len(results)} repo(s)"
    )
    if unresolved:
        print(
            f"unresolved {sum(unresolved.values())} occurrence(s) across "
            f"{len(unresolved)} KRP id(s): {_format_counter(unresolved)}",
            file=sys.stderr,
        )
    if failed:
        print(
            f"skipped {len(failed)} repo(s) due to errors: "
            f"{', '.join(r.repo.name for r in failed)}",
            file=sys.stderr,
        )
    if dry_run:
        print("dry-run only; pass --write to update files")
    return 1 if failed or unresolved else 0


def process_krp_repo(
    repo: Path,
    *,
    norm_table: dict[str, str],
    rep_table: dict[str, str],
    dry_run: bool,
    master_branch: str = "master",
) -> KrpRepRepoResult:
    repo = Path(repo).expanduser().resolve()
    try:
        branch = _current_branch(repo)
        table = norm_table if branch == master_branch else rep_table
        list_name = NORM_LIST_FILENAME if branch == master_branch else REP_LIST_FILENAME
        files = _worktree_files(repo)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return KrpRepRepoResult(repo=repo, branch="", list_name="", error=str(exc))

    result = KrpRepRepoResult(repo=repo, branch=branch, list_name=list_name)
    for rel_path in files:
        if _skip_tracked_path(rel_path):
            continue
        path = repo / rel_path
        if not path.is_file():
            continue
        result.scanned_files += 1
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        new_text, replacements, unresolved = apply_krp_replacements(text, table)
        result.unresolved.update(unresolved)
        if replacements == 0:
            continue
        result.replacements += replacements
        result.changed_files += 1
        result.changed_paths.append(rel_path)
        if not dry_run and new_text != text:
            path.write_text(new_text, encoding="utf-8")
    return result


def _select_repos(
    *,
    repos: list[Path] | None,
    krp_root: Path | None,
    text_ids: list[str] | None,
    text_prefixes: list[str] | None,
    all_repos: bool,
) -> list[Path]:
    direct = [Path(p).expanduser().resolve() for p in (repos or [])]
    selectors = bool(text_ids or text_prefixes or all_repos)
    if direct and selectors:
        raise ValueError("--repo cannot be combined with --text-id, --text-prefix, or --all")
    if direct:
        return direct
    if krp_root is None:
        raise ValueError("KRP root is required; pass --krp-root or set global.krp_root")
    root = Path(krp_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"KRP root not found: {root}")

    selected_ids: set[str] = set()
    for text_id in text_ids or []:
        selected_ids.add(text_id)
    for prefix in text_prefixes or []:
        selected_ids.update(list_local_text_ids(root, prefix))
    if all_repos:
        selected_ids.update(list_local_text_ids(root, None))
    if not selected_ids:
        raise ValueError("provide --repo, --text-id, --text-prefix, or --all")
    return [resolve_local_repo(root, text_id) for text_id in sorted(selected_ids)]


def _current_branch(repo: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    )
    branch = out.stdout.strip()
    if branch:
        return branch
    out = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() or "HEAD"


def _worktree_files(repo: Path) -> list[Path]:
    out = subprocess.run(
        [
            "git", "-C", str(repo), "ls-files",
            "--cached", "--others", "--exclude-standard",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [Path(line) for line in out.stdout.splitlines() if line.strip()]


def _skip_tracked_path(path: Path) -> bool:
    parts = set(path.parts)
    if ".git" in parts:
        return True
    return any(part == "objects" for part in parts)


def _print_repo_result(result: KrpRepRepoResult, *, dry_run: bool) -> None:
    if result.error:
        print(f"[{result.repo.name}] error: {result.error}", file=sys.stderr)
        return
    verb = "would replace" if dry_run else "replaced"
    print(
        f"[{result.repo.name}] branch {result.branch}: {verb} "
        f"{result.replacements} occurrence(s) in {result.changed_files}/"
        f"{result.scanned_files} worktree file(s) using {result.list_name}"
    )
    for path in result.changed_paths[:10]:
        print(f"  {path}")
    if len(result.changed_paths) > 10:
        print(f"  ... {len(result.changed_paths) - 10} more file(s)")
    if result.unresolved:
        print(f"  unresolved: {_format_counter(result.unresolved)}")


def _format_counter(counter: Counter[str], *, limit: int = 10) -> str:
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    bits = [f"{key}={count}" for key, count in items[:limit]]
    if len(items) > limit:
        bits.append(f"... {len(items) - limit} more")
    return ", ".join(bits)
