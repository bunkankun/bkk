"""Render a BKK bundle back into a Kanseki Repository (mandoku-view) tree.

Inverse of :mod:`bkk.importer.read.krp`. Reads the master + every documentary
edition out of the bundle, walks the markers in offset order, and reconstructs
the source format: org-mode header, ``<pb:...>``, ``¶`` line terminators,
``　`` indents, and ``&KRnnnn;`` entity references for every PUA codepoint.

Recipe knobs ``shape`` (dirs/git/single), ``mode`` (split/concat),
``editions`` and ``juans`` shape the on-disk layout. Auxiliary files
(``Readme.org``, ``imglist/...``, ``imginfo.cfg``) are reproduced for the
``dirs`` and ``git`` shapes from manifest metadata + page-break markers;
``single`` skips them.
"""

from __future__ import annotations

import datetime
import re
import subprocess
from pathlib import Path

from ..importer.ir import Bundle, Juan
from ..importer.pua import PUA_BASE, PUA_END
from .read_bundle import read_bundles
from .recipe import Recipe, RecipeError


_PUA_RE = re.compile(f"[{chr(PUA_BASE)}-{chr(PUA_END - 1)}]")


def export_krp_from_recipe(recipe: Recipe) -> list[Path]:
    """Render the bundle named by ``recipe`` into KRP source files."""
    master, documentary = read_bundles(recipe.bundle)
    bundles_by_short = {master.edition_short: master}
    for b in documentary:
        bundles_by_short[b.edition_short] = b

    # Default behaviour with no explicit edition selection: emit only the
    # bkk surface (master) edition, flattened at the output root.
    flatten_master = recipe.shape == "dirs" and recipe.editions is None

    juan_filter = set(recipe.juans) if recipe.juans else None

    base_edition = master.metadata.get("base_edition") or _fallback_base_edition(
        bundles_by_short,
    )
    title = master.metadata.get("title") or master.text_id
    date = datetime.date.today().isoformat()
    image_base_urls = master.metadata.get("image_base_urls") or {}
    editions_meta = master.metadata.get("editions") or []
    if not editions_meta:
        # TLS-sourced bundles don't carry a top-level `editions:` block; fall
        # back to listing the documentary witnesses by their short id so the
        # `* 版本` table in Readme.org is still populated.
        editions_meta = [
            {"short": d.edition_short, "label": d.edition_short}
            for d in documentary
        ]

    out_root = recipe.output_dir
    out_root.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    if flatten_master:
        written.extend(_write_edition_files(
            master, master.text_id, recipe.mode, out_root,
            base_edition=base_edition, title=title, date=date,
            juan_filter=juan_filter,
        ))
        return written

    if recipe.shape == "git":
        # Surface always lands on `master`; documentary editions on branches
        # named by their `edition_short`. Bypass `bundles_by_short` so we
        # don't lose the master when its edition_short collides with a
        # documentary witness (TLS-sourced bundles).
        branches: list[tuple[str, Bundle]] = [("master", master)]
        for doc in documentary:
            branches.append((doc.edition_short, doc))
        for branch, ed in branches:
            branch_dir = out_root / branch
            written.extend(_write_edition_files(
                ed, master.text_id, recipe.mode, branch_dir,
                base_edition=base_edition, title=title, date=date,
                juan_filter=juan_filter,
            ))
            readme_path = branch_dir / "Readme.org"
            readme_path.write_text(
                _render_readme(master, editions_meta, base_edition, title,
                               date, juan_filter),
                encoding="utf-8",
            )
            written.append(readme_path)
        written.extend(_write_data_files(
            documentary, master.text_id, out_root, image_base_urls,
            juan_filter,
        ))
        _stage_as_git_branches(out_root, master.text_id)
        return written

    selected = _select_editions(bundles_by_short, recipe)

    if recipe.shape == "single":
        ed = next(b for b in selected if b.edition_short == recipe.edition)
        written.extend(_write_edition_files(
            ed, master.text_id, recipe.mode, out_root,
            base_edition=base_edition, title=title, date=date,
            juan_filter=juan_filter,
        ))
        return written

    # shape: dirs with an explicit editions filter.
    for ed in selected:
        ed_dir = out_root / ed.edition_short
        written.extend(_write_edition_files(
            ed, master.text_id, recipe.mode, ed_dir,
            base_edition=base_edition, title=title, date=date,
            juan_filter=juan_filter,
        ))
    if any(b.edition_short == "krp" for b in selected):
        readme_path = out_root / "krp" / "Readme.org"
        readme_path.write_text(
            _render_readme(master, editions_meta, base_edition, title,
                           date, juan_filter),
            encoding="utf-8",
        )
        written.append(readme_path)
    documentary_selected = [b for b in selected if b.edition_short != "krp"]
    written.extend(_write_data_files(
        documentary_selected, master.text_id, out_root, image_base_urls,
        juan_filter,
    ))
    return written


def _write_data_files(
    documentary: list[Bundle], text_id: str, out_root: Path,
    image_base_urls: dict[str, str], juan_filter: set[int] | None,
) -> list[Path]:
    """Emit ``_data/imglist/*.txt`` + ``_data/imginfo.cfg`` if any page-break
    markers carry image refs. Returns the written paths (empty list when
    there's nothing to write — TLS-sourced or text-only bundles).
    """
    written: list[Path] = []
    if not documentary:
        return written
    # Imglist needs an edition's worth of page-breaks; pick the first
    # documentary edition (mirrors the input repo, where _data tracks the
    # WYG-style ids).
    ed = documentary[0]
    rendered_imglists: list[tuple[Juan, str]] = []
    for juan in ed.juans:
        if juan_filter is not None and juan.seq not in juan_filter:
            continue
        body = _render_imglist(juan, ed.edition_short)
        if body:
            rendered_imglists.append((juan, body))
    if not rendered_imglists:
        return written
    data_dir = out_root / "_data" / "imglist"
    data_dir.mkdir(parents=True, exist_ok=True)
    for juan, body in rendered_imglists:
        p = data_dir / f"{text_id}_{juan.seq:03d}.txt"
        p.write_text(body, encoding="utf-8")
        written.append(p)
    if image_base_urls:
        cfg = data_dir / "imginfo.cfg"
        cfg.write_text(_render_imginfo(image_base_urls), encoding="utf-8")
        written.append(cfg)
    return written


# ---------- selection helpers ----------------------------------------------


def _fallback_base_edition(bundles_by_short: dict[str, Bundle]) -> str:
    for short in sorted(bundles_by_short):
        if short != "krp":
            return short
    return ""


def _select_editions(bundles_by_short: dict[str, Bundle],
                     recipe: Recipe) -> list[Bundle]:
    available = set(bundles_by_short)
    if recipe.editions is not None:
        unknown = set(recipe.editions) - available
        if unknown:
            raise RecipeError(
                f"editions filter references unknown editions: {sorted(unknown)} "
                f"(bundle has: {sorted(available)})"
            )
        wanted = recipe.editions
    elif recipe.shape == "single":
        wanted = [recipe.edition]
    else:
        wanted = sorted(available, key=lambda s: (s != "krp", s))

    if recipe.shape == "single" and recipe.edition not in available:
        raise RecipeError(
            f"shape: single requested edition {recipe.edition!r} but the "
            f"bundle has only {sorted(available)}"
        )
    return [bundles_by_short[s] for s in wanted]


# ---------- juan flattening ------------------------------------------------


def _flatten_juan(juan: Juan) -> tuple[str, list[dict]]:
    """Concatenate front+body sections into one text + offset-shifted markers.

    The ``back`` bucket (rare in KRP) is skipped — mandoku-view files don't
    model post-text matter. Each section's markers are bucket-relative; we
    re-base them onto the joined stream so the renderer can walk in offset
    order.
    """
    text = ""
    markers: list[dict] = []
    for sec in juan.sections:
        if sec.bucket == "back":
            continue
        offset_base = len(text)
        for m in sec.markers:
            entry = {
                "type": m.type,
                "offset": m.offset + offset_base,
                "content": m.content,
                "id": m.id,
            }
            entry.update(m.extras)
            markers.append(entry)
        text += sec.text
    return text, markers


def _juan_head_text(juan: Juan) -> str:
    return juan.metadata.get("juan_title", "") or ""


# ---------- rendering ------------------------------------------------------


def _encode_pua(text: str) -> str:
    """Replace every PUA codepoint with its ``&KRnnnn;`` entity reference."""
    return _PUA_RE.sub(
        lambda m: f"&KR{ord(m.group(0)) - PUA_BASE:04d};",
        text,
    )


def _render_header(text_id: str, title: str, date: str,
                   base_edition: str, juan_title: str) -> str:
    lines = [
        "# -*- mode: mandoku-view; -*-",
        f"#+TITLE: {title}",
        f"#+DATE: {date}",
        f"#+PROPERTY: ID {text_id}",
        f"#+PROPERTY: BASEEDITION {base_edition}",
        f"#+PROPERTY: JUAN {juan_title}",
    ]
    return "\n".join(lines) + "\n"


def _render_juan_body(text: str, markers: list[dict]) -> str:
    """Splice text and markers back into mandoku-view source.

    `¶` line-breaks emit ``¶\\n`` and `<pb:...>` page-breaks are pushed onto
    their own line so the output resembles canonical Kanripo source format.
    """
    out: list[str] = []
    cursor = 0
    sorted_markers = sorted(
        markers, key=lambda m: (m.get("offset", 0) or 0)
    )
    for m in sorted_markers:
        off = m.get("offset", 0) or 0
        if off > cursor:
            out.append(_encode_pua(text[cursor:off]))
            cursor = off
        kind = m.get("type")
        if kind == "page-break":
            if not _ends_with_newline(out):
                out.append("\n")
            out.append(f"<pb:{m.get('id', '')}>")
        elif kind == "line-break":
            out.append("¶\n")
        elif kind == "indent":
            out.append(m.get("content", "") or "")
        elif kind == "punctuation":
            out.append(m.get("content", "") or "")
        elif kind == "comment":
            out.append("\n" + (m.get("content", "") or "") + "\n")
        elif kind == "head":
            level = int(m.get("level", 1) or 1)
            out.append("\n" + ("*" * level) + " "
                       + (m.get("content", "") or "") + "\n")
        # variant / kr:org-directive / unknown: silently skipped in v1.
    if cursor < len(text):
        out.append(_encode_pua(text[cursor:]))
    if not _ends_with_newline(out):
        out.append("\n")
    return "".join(out)


def _ends_with_newline(out: list[str]) -> bool:
    """True if the buffer is empty or its last chunk ends with ``\\n``.

    Empty counts as newlined so a body that opens with `<pb:...>` does not
    get a leading blank line.
    """
    return not out or out[-1].endswith("\n")


def _render_juan(juan: Juan, text_id: str, title: str, date: str,
                 base_edition: str, with_header: bool = True) -> str:
    text, markers = _flatten_juan(juan)
    head = _juan_head_text(juan)
    body = _render_juan_body(text, markers)
    if not with_header:
        # Used by mode: concat to emit a JUAN directive without re-emitting
        # the file-level header.
        return f"#+PROPERTY: JUAN {head}\n{body}"
    header = _render_header(text_id, title, date, base_edition, head)
    return header + body


# ---------- file-layout writers --------------------------------------------


def _write_edition_files(ed: Bundle, text_id: str, mode: str,
                         out_dir: Path, *, base_edition: str,
                         title: str, date: str,
                         juan_filter: set[int] | None) -> list[Path]:
    """Write one edition's juan files in the chosen ``mode``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    juans = [j for j in ed.juans
             if juan_filter is None or j.seq in juan_filter]

    if mode == "split":
        for juan in juans:
            p = out_dir / f"{text_id}_{juan.seq:03d}.txt"
            p.write_text(
                _render_juan(juan, text_id, title, date, base_edition),
                encoding="utf-8",
            )
            written.append(p)
    elif mode == "concat":
        if not juans:
            return written
        first, rest = juans[0], juans[1:]
        body = _render_juan(first, text_id, title, date, base_edition)
        for juan in rest:
            body += "\n" + _render_juan(
                juan, text_id, title, date, base_edition, with_header=False,
            )
        p = out_dir / f"{text_id}.txt"
        p.write_text(body, encoding="utf-8")
        written.append(p)
    return written


def _render_imglist(juan: Juan, edition_short: str) -> str:
    """Reconstruct a per-juan imglist file from page-break markers."""
    _, markers = _flatten_juan(juan)
    lines: list[str] = []
    for m in markers:
        if m.get("type") != "page-break":
            continue
        image = m.get("image")
        if not image:
            continue
        page_id = m.get("id", "")
        # The id has the form ``<text-id>_<edition>_<juan>-<PaB>``; strip
        # everything before the trailing ``<juan>-<PaB>`` chunk.
        page_short = page_id.rsplit("_", 1)[-1]
        lines.append(f"{page_short}00\t{edition_short} {page_short}\t{image}")
    return "\n".join(lines) + ("\n" if lines else "")


def _render_imginfo(image_base_urls: dict[str, str]) -> str:
    lines = ["[Versions]", "# base urls for the existing versions"]
    for short in sorted(image_base_urls):
        lines.append(f"{short}={image_base_urls[short]}")
    return "\n".join(lines) + "\n"


def _render_readme(master: Bundle, editions_meta: list[dict],
                   base_edition: str, title: str, date: str,
                   juan_filter: set[int] | None) -> str:
    """Render a ``Readme.org`` index in canonical Kanripo source style.

    ``* 版本`` lists each edition (short id + label); ``* 目次`` lists each
    TOC entry as a ``** [[file:<name>::<anchor>][<label>]]`` heading. The
    same content goes on every branch — file references resolve to whatever
    edition's juan files happen to be on the current branch.
    """
    title_line = f"#+TITLE: {title}"
    if base_edition:
        title_line += f" / {base_edition}"
    lines = [
        title_line,
        f"#+DATE: {date}",
        "",
        "* 版本",
    ]
    for entry in editions_meta:
        short = entry.get("short", "")
        label = entry.get("label", short)
        lines.append(f" |       {short}|{label}|")
    lines.append("")
    lines.append("* 目次")
    seen: set[tuple[int, str]] = set()
    toc = master.metadata.get("table_of_contents") or []
    for entry in toc:
        ref = entry.get("ref") or {}
        seq = ref.get("seq")
        marker_id = ref.get("marker_id", "")
        if seq is None:
            continue
        if juan_filter is not None and seq not in juan_filter:
            continue
        key = (seq, marker_id)
        if key in seen:
            continue
        seen.add(key)
        filename = f"{master.text_id}_{seq:03d}.txt"
        anchor = marker_id.rsplit("_", 1)[-1] or filename
        # Strip embedded ``<pb:...>`` markup that occasionally leaks into TOC
        # titles in the source (e.g. "[提要]<pb:KR3a0013_WYG_000-2a>").
        title_raw = entry.get("label", "") or ""
        entry_title = re.sub(r"<pb:[^>]+>", "", title_raw).strip()
        entry_title = entry_title.strip("[]")
        if not entry_title:
            continue
        lines.append(f"** [[file:{filename}::{anchor}][{entry_title}]]")
    return "\n".join(lines) + "\n"


# ---------- shape: git -----------------------------------------------------


def _stage_as_git_branches(out_root: Path, text_id: str) -> None:
    """Initialise a git repo and commit each top-level subdir to its own branch.

    Each directly-under-``out_root`` directory (``master``, ``WYG``, ``_data``,
    …) becomes an orphan branch holding only that directory's files at the
    repo root. The exporter then deletes the per-branch subdirs from the
    working tree, leaving a bare-shaped repo.
    """
    branch_dirs = sorted(
        d for d in out_root.iterdir() if d.is_dir() and d.name != ".git"
    )
    if not branch_dirs:
        return
    _git(out_root, "init", "-q")
    _git(out_root, "config", "user.email", "exporter@bkk.local")
    _git(out_root, "config", "user.name", "bkk-exporter")
    for branch_dir in branch_dirs:
        branch = branch_dir.name
        _git(out_root, "checkout", "-q", "--orphan", branch)
        _git(out_root, "rm", "-rfq", "--cached", "--ignore-unmatch", ".")
        # Stage only files inside this branch's subdir, but check them in at
        # the repo root.
        for src in sorted(branch_dir.rglob("*")):
            if src.is_dir():
                continue
            rel = src.relative_to(branch_dir)
            dest = out_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            _git(out_root, "add", "--", str(rel))
        _git(out_root, "commit", "-q", "-m", f"export {text_id} branch {branch}")
        # Clean the working tree before checking out the next orphan.
        for src in sorted(branch_dir.rglob("*"), reverse=True):
            rel = src.relative_to(branch_dir)
            (out_root / rel).unlink(missing_ok=True) if src.is_file() else None
    # Leave the repo on the first branch (alphabetical).
    _git(out_root, "checkout", "-q", branch_dirs[0].name)
    # Drop the staged subdirs; their content lives in branches now.
    for branch_dir in branch_dirs:
        for f in sorted(branch_dir.rglob("*"), reverse=True):
            if f.is_file():
                f.unlink(missing_ok=True)
        try:
            for d in sorted(branch_dir.rglob("*"), reverse=True):
                if d.is_dir():
                    d.rmdir()
            branch_dir.rmdir()
        except OSError:
            pass


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True)
