"""Kanseki Repository (KRP) reader.

Each KRP text is a git repo whose branches are editions. Documentary editions
(e.g. ``WYG``) carry one ``mode: mandoku-view`` text file per juan; the
``master`` branch carries a curated reading; the ``_data`` branch carries the
imglist mapping ``<juan>-<page>`` ids to image filenames plus an
``imginfo.cfg`` of base URLs.

This module reads each declared edition (per the recipe) into a
:class:`Bundle`, expands ``&KRnnnn;`` entity references to PUA codepoints,
detects variants between master and witnesses, and computes the bundle-wide
PUA-map summary for the master.
"""

from __future__ import annotations

import configparser
import difflib
import io
import re
import subprocess
from pathlib import Path

from ..classify import split_front_by_opening_indent
from ..ir import Bundle, Juan, Marker, Section
from ..pua import summarise_pua_codepoints
from ..recipe import Recipe


# ---------- git plumbing ----------------------------------------------------


def _git_show(repo: Path, branch: str, path: str) -> str:
    """Read ``<path>`` from ``<branch>`` of ``<repo>`` via ``git show``."""
    out = subprocess.run(
        ["git", "-C", str(repo), "show", f"{branch}:{path}"],
        check=True, capture_output=True,
    )
    return out.stdout.decode("utf-8")


def _git_ls_branch(repo: Path, branch: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "--name-only", branch],
        check=True, capture_output=True, text=True,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


# ---------- imglist + imginfo ----------------------------------------------


_JUAN_FILE_RE = re.compile(r"^([A-Z]+\d+[a-z]?\d+)_(\d{3})\.txt$")


def _list_juan_files(repo: Path, branch: str, text_id: str) -> list[tuple[int, str]]:
    """Return ``[(seq, path), ...]`` for each ``<text-id>_NNN.txt`` on
    ``branch``, sorted by seq."""
    out: list[tuple[int, str]] = []
    for name in _git_ls_branch(repo, branch):
        m = _JUAN_FILE_RE.match(name)
        if not m or m.group(1) != text_id:
            continue
        out.append((int(m.group(2)), name))
    return sorted(out)


def _parse_imglist_file(text: str) -> dict[tuple[str, str], str]:
    """Parse one imglist file: ``<juan>-<PaB><LL>\\t<edition> <juan>-<PaB>\\t<image>``.

    Returns ``{ (<edition>, <juan>-<PaB>): <image_path> }``. Keying by
    ``(edition, page_id)`` rather than the bare ``page_id`` keeps SBCK and
    WYG entries that share ``001-1a`` from clobbering each other; the
    page-break image lookup later in :func:`_parse_juan_text` resolves to
    the marker's own edition.
    """
    out: dict[tuple[str, str], str] = {}
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        # Col 2 has the form ``<edition> <juan>-<PaB>``; col 3 is the image.
        try:
            edition, page_id = cols[1].split(" ", 1)
        except ValueError:
            continue
        out[(edition.strip(), page_id.strip())] = cols[2].strip()
    return out


def _load_imglist(repo: Path, branch: str | None, path_template: str,
                  text_id: str, juan_seqs: list[int]) -> dict[tuple[str, str], str]:
    """Build the union imglist across all juans, keyed by ``(edition, page_id)``."""
    if branch is None:
        return {}
    out: dict[tuple[str, str], str] = {}
    for seq in juan_seqs:
        path = path_template.format(text_id=text_id, NNN=f"{seq:03d}")
        try:
            text = _git_show(repo, branch, path)
        except subprocess.CalledProcessError:
            continue
        out.update(_parse_imglist_file(text))
    return out


def _lookup_image(imglist: dict[tuple[str, str], str],
                  page_id: str, short: str) -> str | None:
    """Resolve the image for a page-break by edition.

    Page-break ids follow ``<text-id>_<edition>_<location>`` (project
    memory). We extract the edition and look up
    ``imglist[(edition, short)]`` so each page-break marker resolves to
    its *own* edition's image — no cross-edition bleed once SBCK and WYG
    entries share the same ``<juan>-<PaB>`` shorthand.

    Returns ``None`` when the id doesn't decompose as expected, so a
    malformed marker can't crash the parse.
    """
    parts = page_id.split("_")
    if len(parts) < 3:
        return None
    edition = parts[1]
    return imglist.get((edition, short))


_README_TABLE_RE = re.compile(r"^\s*\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$")


def _load_readme_metadata(repo: Path, branch: str | None) -> dict[str, str]:
    """Parse ``#+TITLE:`` and ``#+DATE:`` from ``branch:Readme.org``.

    Kanripo readmes commonly write the title as ``傅子 / WYG`` (text title +
    base edition); the trailing `` / <ed>`` suffix is stripped so the
    bundle's ``metadata.title`` carries the bare title.

    Returns ``{}`` if the file or fields are missing — the synthesizer
    treats those metadata fields as optional.
    """
    if branch is None:
        return {}
    try:
        text = _git_show(repo, branch, "Readme.org")
    except subprocess.CalledProcessError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines()[:30]:
        s = line.strip()
        if s.startswith("#+TITLE:"):
            value = s[len("#+TITLE:"):].strip()
            if " / " in value:
                value = value.split(" / ", 1)[0].strip()
            if value:
                out["title"] = value
        elif s.startswith("#+DATE:"):
            value = s[len("#+DATE:"):].strip()
            if value:
                out["date"] = value
    return out


def _load_edition_labels(repo: Path, branch: str | None) -> dict[str, str]:
    """Parse ``Readme.org`` from the master branch for edition labels.

    The ``* 版本`` section has rows like ``|       WYG|【四庫全書・文淵閣】|``
    pairing each documentary edition's short id with its human-readable label.
    Returns ``{short: label}``; empty if the file or section is missing.
    """
    if branch is None:
        return {}
    try:
        text = _git_show(repo, branch, "Readme.org")
    except subprocess.CalledProcessError:
        return {}
    out: dict[str, str] = {}
    in_versions = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("*"):
            in_versions = stripped.startswith("* 版本")
            continue
        if not in_versions:
            continue
        m = _README_TABLE_RE.match(line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
    return out


def _load_imginfo(repo: Path, branch: str | None) -> dict[str, str]:
    """Parse ``imglist/imginfo.cfg`` from the imglist branch.

    Returns ``{edition_short: base_url}`` from the ``[Versions]`` section.
    """
    if branch is None:
        return {}
    try:
        text = _git_show(repo, branch, "imglist/imginfo.cfg")
    except subprocess.CalledProcessError:
        return {}
    cp = configparser.ConfigParser()
    cp.read_file(io.StringIO(text))
    if not cp.has_section("Versions"):
        return {}
    return {k: v for k, v in cp.items("Versions")}


# ---------- juan-text parsing ----------------------------------------------


_PB_RE = re.compile(r"^<pb:([^>]+)>$")
_MD_RE = re.compile(r"^<md:[^>]+>$")
_PROP_RE = re.compile(r"^#\+PROPERTY:\s+(\S+)\s*(.*)$")
_HEADER_RE = re.compile(r"^(#\s|#\+)")
_PUA_ENTITY_RE = re.compile(r"&KR(\d+);")


_PB_INLINE_RE = re.compile(r"<pb:[^>]+>")
_MD_INLINE_RE = re.compile(r"<md:[^>]+>")


def _clean_head_text(raw: str) -> str:
    """Strip layout markup from a JUAN directive value.

    The KRP source occasionally embeds ``<pb:...>`` or ``<md:...>`` markers
    and brackets inside ``#+PROPERTY: JUAN`` directives (e.g.
    ``[提要]<pb:KR3a0013_WYG_000-2a>``). The TOC label should be the bare
    kanji title; the ``<md:...>`` references in particular point at other
    editions and are dropped wholesale (see project memory).
    """
    cleaned = _PB_INLINE_RE.sub("", raw)
    cleaned = _MD_INLINE_RE.sub("", cleaned).strip()
    return cleaned.strip("[]").strip()


def _parse_juan_text(text: str, juan_seq: int,
                     text_id: str,
                     imglist: dict[tuple[str, str], str]) -> Juan:
    """Parse one mandoku-view juan source file into a Juan IR.

    Strips the org-mode header, walks ``¶``-terminated logical lines, and
    emits one Section per ``#+PROPERTY: JUAN`` directive. Subsequent JUAN
    directives within the same file emit ``kr:org-directive`` markers in the
    *previous* section before closing it.

    Mid-file ``# ...`` comment lines and ``** ...`` heading lines are not part
    of the canonical text. They are extracted as ``comment`` and ``head``
    markers (the latter carrying ``extras["level"]`` = number of leading stars)
    so the body text stays free of org-mode metadata.
    """
    sections: list[Section] = []

    text_buf: list[str] = []
    markers: list[Marker] = []
    pending_head_text = ""
    head_text = ""
    head_marker_id = ""
    juan_title = ""
    current_page_id = ""
    line_counter = 0
    content_buf: list[str] = []

    def offset() -> int:
        return sum(len(p) for p in text_buf)

    def close_section() -> None:
        nonlocal text_buf, markers, head_text, head_marker_id
        sections.append(Section(
            head_text=head_text,
            head_marker_id=head_marker_id,
            text="".join(text_buf),
            markers=markers,
        ))
        text_buf = []
        markers = []
        head_text = ""
        head_marker_id = ""

    def process_chunk(chunk: str) -> None:
        nonlocal current_page_id, line_counter
        nonlocal head_text, head_marker_id, pending_head_text
        if not chunk:
            return
        if _MD_RE.match(chunk):
            return
        pb = _PB_RE.match(chunk)
        if pb:
            page_id = pb.group(1)
            current_page_id = page_id
            line_counter = 0
            marker = Marker(
                type="page-break",
                offset=offset(),
                content="",
                id=page_id,
            )
            short = page_id.split("_")[-1]  # e.g. "000-1a"
            img = _lookup_image(imglist, page_id, short)
            if img:
                marker.extras["image"] = img
            # If a JUAN directive is pending and this section is empty,
            # this page-break opens the new section's first marker.
            if pending_head_text and head_text == "":
                head_text = pending_head_text
                head_marker_id = page_id
                pending_head_text = ""
            markers.append(marker)
            return
        if chunk.startswith("#+PROPERTY:"):
            # JUAN directive splits sections. Emit a ``kr:org-directive``
            # marker in the *previous* section if there's already one
            # open with content; otherwise just record the head text.
            prop = _PROP_RE.match(chunk)
            if prop and prop.group(1) == "JUAN":
                new_head = _clean_head_text(prop.group(2))
                if head_text:
                    markers.append(Marker(
                        type="kr:org-directive",
                        offset=offset(),
                        content=f"#+PROPERTY: JUAN {new_head}",
                        id="",
                    ))
                    close_section()
                pending_head_text = new_head
            return
        # Plain text line: emit a line-break marker, then walk the chars.
        line_counter += 1
        line_id = f"{current_page_id}{line_counter:02d}"
        markers.append(Marker(
            type="line-break", offset=offset(), content="", id=line_id,
        ))
        _emit_line_chars(chunk, text_buf, markers, offset)

    def flush_content() -> None:
        """Process buffered content lines through ¶-chunk dispatch."""
        if not content_buf:
            return
        rest = "".join(content_buf)
        content_buf.clear()
        # Some kanripo branches (e.g. WYG of KR3a0001) embed ``<pb:...>`` and
        # ``<md:...>`` markers inline mid-chunk instead of as standalone
        # lines. Normalise by wrapping every such marker in ``¶`` so the
        # chunk dispatch sees them as their own chunks.
        rest = re.sub(r"(<(?:pb|md):[^>]+>)", r"¶\1¶", rest)
        for chunk in rest.split("¶"):
            process_chunk(chunk)

    # Consume the org-mode header (before the first <pb:...> or content).
    raw_lines = text.split("\n")
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        if not line:
            i += 1
            continue
        if _HEADER_RE.match(line):
            prop = _PROP_RE.match(line)
            if prop and prop.group(1) == "JUAN":
                pending_head_text = _clean_head_text(prop.group(2))
                if not juan_title:
                    juan_title = pending_head_text
            i += 1
            continue
        break

    # Walk the rest line by line. ``# ...`` comments and ``* ...`` headings
    # become typed markers; everything else is buffered and flushed through
    # the ¶-chunk pipeline (which handles content, page-breaks, md drops, and
    # mid-file JUAN directives).
    for line in raw_lines[i:]:
        if not line:
            continue
        if line.startswith("#") and not line.startswith("#+"):
            flush_content()
            markers.append(Marker(
                type="comment", offset=offset(), content=line, id="",
            ))
            continue
        if line.startswith("*"):
            n_stars = len(line) - len(line.lstrip("*"))
            head_content = line[n_stars:].lstrip()
            flush_content()
            head_marker = Marker(
                type="head", offset=offset(), content=head_content, id="",
            )
            head_marker.extras["level"] = n_stars
            markers.append(head_marker)
            continue
        content_buf.append(line)
    flush_content()

    if text_buf or markers or head_text:
        # Close any final section. If no JUAN directive ever populated
        # head_text, fall back to the file's first JUAN directive (if any)
        # or a synthetic placeholder so the section is still navigable.
        if not head_text:
            head_text = juan_title or f"juan {juan_seq:03d}"
            for m in markers:
                if m.type == "page-break":
                    head_marker_id = m.id
                    break
        close_section()

    return Juan(
        seq=juan_seq,
        sections=sections,
        metadata={
            "juan_title": juan_title,
            "source": {"repository": "kanripo",
                       "path": f"{text_id}/{text_id}_{juan_seq:03d}.txt"},
        },
    )


_PUNCT_CHARS = set(
    "()/"
    "，。、；：？！"           # fullwidth + ideographic basics
    "「」『』《》〈〉〔〕【】〖〗"  # CJK quotation/bracket pairs
    "・…—–·"                # middle-dot, ellipsis, dashes
)


def _emit_line_chars(line: str, text_buf: list[str], markers: list[Marker],
                     offset_fn) -> None:
    """Walk one logical line, separating text vs indent/punctuation markers
    and expanding ``&KRnnnn;`` entities to their PUA codepoints."""
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "\u3000":  # full-width indent
            j = i
            while j < n and line[j] == "\u3000":
                j += 1
            markers.append(Marker(
                type="indent", offset=offset_fn(),
                content=line[i:j], id="",
            ))
            i = j
            continue
        if ch in _PUNCT_CHARS:
            markers.append(Marker(
                type="punctuation", offset=offset_fn(),
                content=ch, id="",
            ))
            i += 1
            continue
        if ch == "&":
            m = _PUA_ENTITY_RE.match(line, i)
            if m:
                cp = 0x105000 + int(m.group(1))
                text_buf.append(chr(cp))
                i = m.end()
                continue
        text_buf.append(ch)
        i += 1


# ---------- variant detection ----------------------------------------------


def _juan_text(juan: Juan) -> str:
    """Concatenated text of all sections in juan order."""
    return "".join(sec.text for sec in juan.sections)


def _section_for_offset(juan: Juan, offset_global: int) -> tuple[int, int]:
    """Return ``(section_index, section_local_offset)`` for ``offset_global``
    in the concatenated juan text."""
    cursor = 0
    for i, sec in enumerate(juan.sections):
        if offset_global <= cursor + len(sec.text):
            return i, offset_global - cursor
        cursor += len(sec.text)
    last = max(0, len(juan.sections) - 1)
    return last, max(0, offset_global - cursor)


def _detect_variants(master: Juan, witness: Juan) -> list[tuple[int, int, str, str]]:
    """Return ``[(global_offset, length, master_str, witness_str), ...]``."""
    a = _juan_text(master)
    b = _juan_text(witness)
    out: list[tuple[int, int, str, str]] = []
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        master_span = a[i1:i2]
        witness_span = b[j1:j2]
        if tag == "replace" and (i2 - i1) == (j2 - j1):
            # Equal-length replace: emit one length-1 variant per position
            # so each character variation is independently navigable.
            for k in range(i2 - i1):
                out.append((i1 + k, 1, master_span[k], witness_span[k]))
        else:
            out.append((i1, i2 - i1, master_span, witness_span))
    return [(o, l, m, w) for o, l, m, w in out]


def _attach_variants(master: Juan, witness: Juan, witness_short: str) -> None:
    """Mutate ``master`` to add variant markers vs ``witness``."""
    for off, length, m_str, w_str in _detect_variants(master, witness):
        sec_idx, local_off = _section_for_offset(master, off)
        master.sections[sec_idx].markers.append(Marker(
            type="variant",
            offset=local_off,
            content=m_str,
            id="",
            extras={"length": length, witness_short: w_str},
        ))


def _map_witness_offset(opcodes: list[tuple[str, int, int, int, int]],
                        j_off: int) -> int:
    """Map a witness offset (``b``-coordinate) to a master offset (``a``).

    Inside an ``equal`` block the mapping is exact. Inside ``replace`` /
    ``delete`` / ``insert`` blocks the witness offset is snapped to the
    start of the corresponding master span — the page-break still lands in
    the right section, just rounded to the nearest aligned boundary.
    """
    last_i2 = 0
    for tag, i1, i2, j1, j2 in opcodes:
        if j1 <= j_off < j2:
            if tag == "equal":
                return i1 + (j_off - j1)
            return i1
        last_i2 = i2
    return last_i2


def _attach_witness_page_breaks(master: Juan, witness: Juan) -> None:
    """Inject witness page-breaks into ``master`` at aligned offsets.

    Each injected marker keeps the witness id (e.g. ``KR3a0001_WYG_001-1a``)
    and a copy of the witness ``extras`` (so the image — already resolved
    per-edition by :func:`_lookup_image` during witness parse — travels
    with the page-break and never bleeds into another edition).

    Markers whose ``id`` already exists on the master are skipped so the
    base edition's page-breaks (already in the master source) aren't
    duplicated. Section markers stay in append order; the writer's
    offset-stable sort puts them in the right place.
    """
    existing_ids = {
        m.id for sec in master.sections
        for m in sec.markers if m.type == "page-break"
    }

    a = _juan_text(master)
    b = _juan_text(witness)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    opcodes = sm.get_opcodes()

    cursor = 0
    for w_section in witness.sections:
        for marker in w_section.markers:
            if marker.type != "page-break":
                continue
            if marker.id in existing_ids:
                continue
            j_off = cursor + marker.offset
            i_off = _map_witness_offset(opcodes, j_off)
            sec_idx, local_off = _section_for_offset(master, i_off)
            master.sections[sec_idx].markers.append(Marker(
                type="page-break",
                offset=local_off,
                content="",
                id=marker.id,
                extras=dict(marker.extras),
            ))
            existing_ids.add(marker.id)
        cursor += len(w_section.text)


# ---------- top-level ------------------------------------------------------


def read_krp(recipe: Recipe) -> tuple[list[Bundle], Bundle | None]:
    """Read all editions declared by ``recipe``.

    Returns ``(documentary_bundles, master_bundle_or_none)``.
    """
    if recipe.source is None:
        raise ValueError("krp recipe has no `source` block")
    if recipe.text_id is None:
        raise ValueError("krp recipe has no `text_id`")

    repo = recipe.source.repo
    text_id = recipe.text_id

    imglist_branch = recipe.source.imglist.branch if recipe.source.imglist else None
    imglist_path = (
        recipe.source.imglist.path if recipe.source.imglist
        else "imglist/{text_id}_{NNN}.txt"
    )
    imginfo = _load_imginfo(repo, imglist_branch)
    edition_labels = (
        _load_edition_labels(repo, recipe.source.master.branch)
        if recipe.source.master is not None else {}
    )

    # Documentary editions
    documentary: list[Bundle] = []
    for ed in recipe.source.editions:
        juan_files = _list_juan_files(repo, ed.branch, text_id)
        seqs = [seq for seq, _ in juan_files]
        imglist = _load_imglist(repo, imglist_branch, imglist_path, text_id, seqs)
        juans = []
        for seq, path in juan_files:
            raw = _git_show(repo, ed.branch, path)
            juan = _parse_juan_text(raw, seq, text_id, imglist)
            juan.sections = split_front_by_opening_indent(juan.sections)
            juans.append(juan)
        bundle = Bundle(
            text_id=text_id,
            juans=juans,
            metadata=_bundle_metadata(
                recipe, imginfo, base_edition=None,
                edition_label=edition_labels.get(ed.short),
                editions=None,
            ),
            edition_short=ed.short,
            source={"repository": "kanripo", "path": text_id},
        )
        bundle.pua_map = summarise_pua_codepoints(
            text_id, [_juan_text(j) for j in bundle.juans],
        )
        documentary.append(bundle)

    # Master
    master: Bundle | None = None
    if recipe.source.master is not None:
        ms = recipe.source.master
        juan_files = _list_juan_files(repo, ms.branch, text_id)
        seqs = [seq for seq, _ in juan_files]
        imglist = _load_imglist(repo, imglist_branch, imglist_path, text_id, seqs)
        juans = []
        for seq, path in juan_files:
            raw = _git_show(repo, ms.branch, path)
            juan = _parse_juan_text(raw, seq, text_id, imglist)
            juan.sections = split_front_by_opening_indent(juan.sections)
            juans.append(juan)

        # Resolve base_edition from the source header (#+PROPERTY: BASEEDITION)
        # of the first juan; fall back to the first witness short.
        base_edition = _read_base_edition(repo, ms.branch, juan_files)
        if base_edition is None and ms.witnesses:
            base_edition = ms.witnesses[0]

        master_editions = [
            {"short": ed.short, **(
                {"label": edition_labels[ed.short]}
                if ed.short in edition_labels else {}
            )}
            for ed in recipe.source.editions
        ]
        master = Bundle(
            text_id=text_id,
            juans=juans,
            metadata=_bundle_metadata(
                recipe, imginfo, base_edition=base_edition,
                edition_label=None,
                editions=master_editions,
            ),
            edition_short="master",
            source={"repository": "kanripo", "path": text_id},
            witnesses=list(ms.witnesses),
        )

        # Variant detection + page-break merge: pair master against each
        # witness in declaration order. Witness page-breaks land in the
        # master at aligned offsets so a master reader sees the page
        # transitions of every edition, each carrying its own image.
        for wshort in ms.witnesses:
            wbundle = next(
                (b for b in documentary if b.edition_short == wshort), None,
            )
            if wbundle is None:
                continue
            for mj, wj in zip(master.juans, wbundle.juans):
                _attach_variants(mj, wj, wshort)
                _attach_witness_page_breaks(mj, wj)

        # PUA-map aggregates across the entire bundle (master + every
        # documentary edition), so PUA characters that only appear on the
        # witness side of a variant still get counted.
        all_texts = [_juan_text(j) for j in master.juans]
        for b in documentary:
            all_texts.extend(_juan_text(j) for j in b.juans)
        master.pua_map = summarise_pua_codepoints(text_id, all_texts)

    return documentary, master


def _bundle_metadata(recipe: Recipe, imginfo: dict[str, str],
                     base_edition: str | None,
                     edition_label: str | None = None,
                     editions: list[dict] | None = None) -> dict:
    md: dict = {}
    if "title" in recipe.metadata:
        md["title"] = recipe.metadata["title"]
    if "date" in recipe.metadata:
        md["date"] = recipe.metadata["date"]
    md["source"] = {"repository": "kanripo", "path": recipe.text_id}
    if imginfo:
        md["image_base_urls"] = dict(imginfo)
    if base_edition is not None:
        md["base_edition"] = base_edition
    if edition_label is not None:
        md["edition_label"] = edition_label
    if editions:
        md["editions"] = list(editions)
    return md


def _read_base_edition(repo: Path, branch: str,
                       juan_files: list[tuple[int, str]]) -> str | None:
    if not juan_files:
        return None
    _, first_path = juan_files[0]
    try:
        text = _git_show(repo, branch, first_path)
    except subprocess.CalledProcessError:
        return None
    for line in text.split("\n")[:20]:
        m = _PROP_RE.match(line)
        if m and m.group(1) == "BASEEDITION":
            return m.group(2).strip() or None
    return None
