"""KRP source resolution and recipe synthesis.

The KRP importer used to require a hand-edited recipe per text. This module
removes that tax: given a text id and either a local kanripo mirror or a
github org, it locates the repo, enumerates its branches, and synthesizes
the same :class:`Recipe` the recipe-driven path would have produced.

Three lookups, all idempotent:

- :func:`resolve_local_repo` walks `<in>/<prefix>/<id>` → `<in>/<id>` →
  `<in>/**/<id>` and warns on ambiguous matches.
- :func:`resolve_github_repo` clones to `<cache>/<user>/<id>` on first use,
  fetches when the working tree is older than ~24h.
- :func:`synthesize_recipe` reads the branch list, classifies it
  (master/imglist/editions), and pulls title+date from `Readme.org`.

Bulk discovery (:func:`list_local_text_ids`, :func:`list_github_text_ids`)
returns the text ids visible under a section prefix or the whole source.
The CLI uses these to build a confirmation prompt before importing.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .recipe import (
    EditionSpec,
    ImglistSpec,
    KrpSource,
    MasterSpec,
    Recipe,
)
from .read.krp import _load_readme_metadata


# ---------- naming convention ----------------------------------------------


def section_prefix(text_id: str) -> str:
    """Return the corpus prefix for a KRP text id (e.g. ``KR3a0013`` → ``KR3a``).

    Centralised so a future change to kanripo's id scheme has one site to fix.
    """
    return text_id[:4]


# ---------- branch discovery -----------------------------------------------


def discover_branches(repo: Path) -> list[str]:
    """Return every local branch in ``repo``.

    Uses ``git for-each-ref`` so the result includes packed refs, not just
    loose ones under ``.git/refs/heads/``.
    """
    out = subprocess.run(
        ["git", "-C", str(repo), "for-each-ref",
         "--format=%(refname:short)", "refs/heads/"],
        check=True, capture_output=True, text=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _classify_branches(
    branches: list[str], master_branch: str, imglist_branch: str,
) -> tuple[bool, bool, list[str]]:
    """Split a branch list into ``(has_master, has_imglist, editions)``.

    Editions exclude the master/imglist branches and any branch starting
    with ``_`` (kanripo reserves that prefix for plumbing).
    """
    has_master = master_branch in branches
    has_imglist = imglist_branch in branches
    editions = [
        b for b in branches
        if b != master_branch and b != imglist_branch and not b.startswith("_")
    ]
    return has_master, has_imglist, sorted(editions)


# ---------- local repo resolution ------------------------------------------


def _looks_like_krp_clone(path: Path, master_branch: str = "master") -> bool:
    """Heuristic: a working-tree git repo with the expected master branch.

    Requires a ``.git`` entry (subdir, file, or symlink) — that excludes
    the ``.git`` directory itself from being misclassified as a clone
    during recursive scans.
    """
    if not (path / ".git").exists():
        return False
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "for-each-ref",
             "--format=%(refname:short)", f"refs/heads/{master_branch}"],
            capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


def resolve_local_repo(in_root: Path, text_id: str) -> Path:
    """Find a kanripo clone for ``text_id`` under ``in_root``.

    Lookup order: ``<in>/<prefix>/<id>`` → ``<in>/<id>`` → recursive
    ``<in>/**/<id>``. Warns to stderr if the recursive search returns more
    than one match (mirrors the TLS-side behaviour in
    :func:`bkk.importer.cli._find_tls_text`).

    Raises :class:`FileNotFoundError` when no candidate exists.
    """
    prefix = section_prefix(text_id)
    candidates = [in_root / prefix / text_id, in_root / text_id]
    for cand in candidates:
        if cand.is_dir():
            return cand

    matches = sorted(
        (p for p in in_root.rglob(text_id) if p.is_dir()),
        key=lambda p: (len(p.parts), str(p)),
    )
    if not matches:
        raise FileNotFoundError(
            f"no directory named {text_id!r} found under {in_root}"
        )
    if len(matches) > 1:
        print(
            f"warning: multiple matches for {text_id} under {in_root}; "
            f"using {matches[0]}",
            file=sys.stderr,
        )
    return matches[0]


# ---------- bulk local discovery -------------------------------------------


def _scan_section_dir(section_dir: Path, prefix: str) -> list[str]:
    """Return text ids found as immediate children of ``section_dir``."""
    if not section_dir.is_dir():
        return []
    out: list[str] = []
    for child in section_dir.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if not name.startswith(prefix):
            continue
        if _looks_like_krp_clone(child):
            out.append(name)
    return sorted(set(out))


def list_local_text_ids(in_root: Path, prefix: str | None) -> list[str]:
    """Enumerate text ids under ``in_root``.

    With ``prefix`` set, prefer ``<in>/<prefix>/*`` (the kanripo mirror
    layout) and fall back to a recursive scan filtering names by prefix
    so flat layouts work too. With ``prefix=None``, walk everything that
    looks like a KRP clone.

    Hidden directories and the contents of already-matched clones are
    pruned during descent.
    """
    if prefix:
        out = _scan_section_dir(in_root / prefix, prefix)
        if out:
            return out

    found: list[str] = []
    for dirpath, dirnames, _filenames in os.walk(in_root):
        # Prune hidden dirs (.git, .cache, …) from the descent.
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]

        path = Path(dirpath)
        if path == in_root:
            continue
        if prefix and not path.name.startswith(prefix):
            continue
        if _looks_like_krp_clone(path):
            found.append(path.name)
            # Don't descend into a matched clone — its contents are not texts.
            dirnames[:] = []
    return sorted(set(found))


# ---------- github source ---------------------------------------------------


_DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60


def _github_clone_url(user: str, text_id: str) -> str:
    return f"https://github.com/{user}/{text_id}.git"


def resolve_github_repo(
    user: str, text_id: str, cache: Path,
    *, ttl_seconds: int = _DEFAULT_CACHE_TTL_SECONDS,
    refresh: bool = False,
) -> Path:
    """Return a local clone of ``github.com/<user>/<text_id>``.

    Clones to ``<cache>/<user>/<text_id>/`` on first use, ``git fetch``es
    when the cache is older than ``ttl_seconds`` (or when ``refresh=True``).
    Always runs :func:`_ensure_local_branches` so subsequent
    ``git show <branch>:`` calls in :mod:`.read.krp` resolve without an
    ``origin/`` prefix — a fresh ``git clone`` only sets up local refs for
    the default branch, leaving every other edition (and ``_data``) behind
    as a remote-tracking ref.
    """
    target = cache / user / text_id
    target.parent.mkdir(parents=True, exist_ok=True)

    if not target.exists():
        url = _github_clone_url(user, text_id)
        print(f"cloning {url} → {target}", file=sys.stderr)
        subprocess.run(
            ["git", "clone", "--quiet", url, str(target)],
            check=True,
        )
        _ensure_local_branches(target)
        return target

    age = time.time() - target.stat().st_mtime
    if refresh or age > ttl_seconds:
        print(f"fetching updates for {target}", file=sys.stderr)
        subprocess.run(
            ["git", "-C", str(target), "fetch", "--all", "--quiet"],
            check=False,
        )
        # bump mtime so the freshness check resets even if fetch was a no-op
        target.touch()
    _ensure_local_branches(target)
    return target


def _ensure_local_branches(repo: Path) -> None:
    """Promote every ``refs/remotes/origin/<X>`` to ``refs/heads/<X>``.

    Idempotent: skips branches that already exist locally. Lets
    :mod:`.read.krp` use bare branch names (``WYG``, ``_data``) the same
    way it does for hand-managed local mirrors.
    """
    # `lstrip=3` drops `refs/remotes/origin/` so we get bare branch names.
    out = subprocess.run(
        ["git", "-C", str(repo), "for-each-ref",
         "--format=%(refname:lstrip=3)", "refs/remotes/origin/"],
        check=True, capture_output=True, text=True,
    )
    for line in out.stdout.splitlines():
        branch = line.strip()
        if not branch or branch == "HEAD":
            continue
        existing = subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "--quiet",
             f"refs/heads/{branch}"],
            capture_output=True,
        )
        if existing.returncode == 0:
            continue
        subprocess.run(
            ["git", "-C", str(repo), "branch", branch, f"origin/{branch}"],
            check=True, capture_output=True,
        )


def list_github_text_ids(
    user: str, prefix: str | None, *, token: str | None = None,
) -> list[str]:
    """List repo names under a github user/org via the public REST API.

    Honours ``GITHUB_TOKEN`` (or the explicit ``token`` arg) to lift the 60
    req/hour anonymous rate limit. Pages through ``?per_page=100`` until the
    API stops handing back results.
    """
    import requests  # lazy: keeps the import optional for non-github runs

    headers = {"Accept": "application/vnd.github+json"}
    token = token or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    out: list[str] = []
    page = 1
    while True:
        url = f"https://api.github.com/users/{user}/repos"
        resp = requests.get(
            url, params={"per_page": 100, "page": page},
            headers=headers, timeout=30,
        )
        resp.raise_for_status()
        items = resp.json()
        if not items:
            break
        for it in items:
            name = it.get("name", "")
            if prefix and not name.startswith(prefix):
                continue
            out.append(name)
        if len(items) < 100:
            break
        page += 1
    return sorted(set(out))


# ---------- recipe synthesis -----------------------------------------------


_DEFAULT_IMGLIST_PATH = "imglist/{text_id}_{NNN}.txt"


def synthesize_recipe(
    repo: Path, text_id: str,
    *, master_branch: str = "master", imglist_branch: str = "_data",
) -> Recipe:
    """Build a :class:`Recipe` by inspecting ``repo``.

    - Editions = every branch except ``master``, ``_data``, and any branch
      starting with ``_``. Edition ``short`` defaults to the branch name.
    - Witnesses = every edition (the recipe-driven path always pinned the
      full set in practice).
    - Imglist defaults to ``_data:imglist/{text_id}_{NNN}.txt``.
    - Title / date are read from ``Readme.org`` on the master branch.

    Warns to stderr if the requested master branch is missing — the
    importer will then produce documentary bundles only.
    """
    branches = discover_branches(repo)
    has_master, has_imglist, editions = _classify_branches(
        branches, master_branch, imglist_branch,
    )

    if not has_master:
        print(
            f"warning: {repo} has no {master_branch!r} branch; "
            f"master bundle will be skipped (override with --master-branch "
            f"or --recipe)",
            file=sys.stderr,
        )

    edition_specs = [EditionSpec(branch=b, short=b) for b in editions]

    master_spec: MasterSpec | None = None
    if has_master:
        master_spec = MasterSpec(
            branch=master_branch, witnesses=list(editions),
        )

    imglist_spec: ImglistSpec | None = None
    if has_imglist:
        imglist_spec = ImglistSpec(
            branch=imglist_branch, path=_DEFAULT_IMGLIST_PATH,
        )

    metadata = _load_readme_metadata(repo, master_branch) if has_master else {}

    source = KrpSource(
        repo=repo,
        editions=edition_specs,
        master=master_spec,
        imglist=imglist_spec,
    )
    return Recipe(
        format="krp",
        text_id=text_id,
        source=source,
        metadata=metadata,
        output_bundle=None,
    )
