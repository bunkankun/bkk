"""Build a SQLite index (``.bkki``) over the bkk-core knowledge layer.

The core knowledge layer lives outside the text bundles. It is a tree of
Markdown notes with YAML frontmatter, organized as
``<collection>/<hex>/<uuid>.md``. See ``docs/bkk-core/README.md`` for the
on-disk contract.

The index powers the web frontend's CORE browse activity: a list of records
per collection, label-substring search, and a detail-view lookup by uuid.
For the Words collection the list is two-level — super-entries first, then
their constituent word records — so super-entries are indexed even though
they are not browseable as a collection of their own.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import yaml

from .catalog import normalize_search_text

log = logging.getLogger("bkk.index.core")

CORE_SCHEMA_VERSION = 1

COLLECTIONS: tuple[tuple[str, str], ...] = (
    # (collection dir name, type)
    ("concepts", "concept"),
    ("graphs", "graph"),
    ("syntactic-functions", "syntactic-function"),
    ("semantic-features", "semantic-feature"),
    ("bibliography", "bibliography"),
    ("words", "word"),
    ("super-entries", "super-entry"),
)

DDL = """
CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE notes (
  uuid TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  collection TEXT NOT NULL,
  path TEXT NOT NULL,
  display_label TEXT NOT NULL,
  source_file TEXT
);

CREATE TABLE labels (
  uuid TEXT NOT NULL,
  label TEXT NOT NULL,
  label_type TEXT NOT NULL,
  label_search TEXT NOT NULL,
  FOREIGN KEY(uuid) REFERENCES notes(uuid)
);

CREATE TABLE links (
  source_uuid TEXT NOT NULL,
  source_type TEXT NOT NULL,
  target_uuid TEXT NOT NULL,
  target_type TEXT,
  relation TEXT,
  FOREIGN KEY(source_uuid) REFERENCES notes(uuid)
);

CREATE TABLE super_entries (
  uuid TEXT PRIMARY KEY,
  orth TEXT NOT NULL,
  orth_search TEXT NOT NULL,
  word_count INTEGER NOT NULL
);

CREATE TABLE super_entry_words (
  super_entry_uuid TEXT NOT NULL,
  word_uuid TEXT NOT NULL,
  concept TEXT,
  n TEXT,
  PRIMARY KEY (super_entry_uuid, word_uuid)
);

CREATE INDEX idx_notes_collection ON notes(collection);
CREATE INDEX idx_notes_type ON notes(type);
CREATE INDEX idx_labels_uuid ON labels(uuid);
CREATE INDEX idx_labels_search ON labels(label_search);
CREATE INDEX idx_links_source ON links(source_uuid);
CREATE INDEX idx_links_target ON links(target_uuid);
CREATE INDEX idx_super_entries_orth_search ON super_entries(orth_search);
CREATE INDEX idx_super_entry_words_word ON super_entry_words(word_uuid);
"""

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)

# Markdown link in a record body. Captures (display, href). The href is
# inspected with _BODY_LINK_TARGET_RE to find a core-record reference.
_BODY_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")

# Canonical UUID shape (8-4-4-4-12 hex). The `.md` suffix is optional because
# a few source records have a stray space in the href (markdown parsers
# truncate at the space, and we want to follow those links anyway).
_UUID_PATTERN = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# `<collection>/<hex>/[uuid-]<uuid>[.md]` — used for cross-collection refs.
_BODY_CROSS_RE = re.compile(
    r"(?:^|/)(concepts|graphs|syntactic-functions|semantic-features|bibliography|words|super-entries)/"
    r"[0-9a-f]+/(?:uuid-)?(" + _UUID_PATTERN + r")(?:\.md)?"
)

# `../<hex>/[uuid-]<uuid>[.md]` — same-collection sibling.
_BODY_SAME_RE = re.compile(
    r"(?:^|/)[0-9a-f]+/(?:uuid-)?(" + _UUID_PATTERN + r")(?:\.md)?"
)

# CJK-flavoured wikilink such as `[[修]]`. We only resolve those that map to
# a known super-entry by orth (looked up after the first pass).
_BODY_WIKILINK_RE = re.compile(r"\[\[([^\]\n|]+)\]\]")

# Strip fenced code blocks before mining links so we don't index sample paths
# pasted into a record.
_FENCE_RE = re.compile(r"^\s*```", re.M)

COLLECTION_TO_TYPE: dict[str, str] = {coll: t for coll, t in COLLECTIONS}


def build_core_index(core_root: Path | str, out_path: Path | str) -> Path:
    """Walk ``core_root``, parse frontmatter, write the SQLite index."""
    core_root = Path(core_root)
    out_path = Path(out_path)

    if not core_root.is_dir():
        raise FileNotFoundError(f"core root not found: {core_root}")

    notes: list[tuple] = []
    labels: list[tuple] = []
    links: list[tuple] = []
    super_entries: list[tuple] = []
    super_entry_words: list[tuple] = []
    # (source_uuid, source_type, source_collection, orth) — resolved after the
    # super-entry orth → uuid map is complete.
    pending_wikilinks: list[tuple[str, str, str, str]] = []
    counts: dict[str, int] = {}

    for coll_dir, type_name in COLLECTIONS:
        coll_root = core_root / coll_dir
        if not coll_root.is_dir():
            log.warning("collection %s not found under %s", coll_dir, core_root)
            continue
        for md_path in _iter_collection(coll_root):
            try:
                fm, body = _read_record(md_path)
            except Exception as exc:
                log.warning("%s: frontmatter parse failed: %s", md_path, exc)
                continue
            if not fm:
                continue
            uuid = _uuid(fm, md_path)
            if uuid is None:
                log.warning("%s: missing uuid", md_path)
                continue
            display = _display_label(type_name, fm) or uuid
            rel_path = md_path.relative_to(core_root).as_posix()
            source_file = _source_file(fm)
            notes.append((uuid, type_name, coll_dir, rel_path, display, source_file))
            labels.extend(_label_rows(uuid, type_name, fm, display))
            links.extend(_link_rows(uuid, type_name, fm))
            body_text = _strip_code_fences(body) if body else ""
            if body_text:
                links.extend(_body_link_rows(uuid, type_name, coll_dir, body_text))
                for orth in _body_wikilink_orths(body_text):
                    pending_wikilinks.append((uuid, type_name, coll_dir, orth))
            counts[type_name] = counts.get(type_name, 0) + 1
            if type_name == "super-entry":
                orth = str(fm.get("orth") or display)
                entries = [e for e in fm.get("entries") or [] if isinstance(e, dict)]
                super_entries.append((
                    uuid, orth, normalize_search_text(orth) or "", len(entries),
                ))
                for entry in entries:
                    word_uuid = _strip_uuid_prefix(entry.get("uuid"))
                    if not word_uuid:
                        continue
                    super_entry_words.append((
                        uuid, word_uuid,
                        entry.get("concept"),
                        _str_or_none(entry.get("n")),
                    ))

    # Resolve wikilinks against the super-entry orth → uuid map. A wikilink
    # to an orth with no super-entry is silently dropped.
    orth_to_super: dict[str, str] = {}
    for se_uuid, orth, _orth_search, _count in super_entries:
        orth_to_super.setdefault(orth, se_uuid)
    for src_uuid, src_type, _src_coll, orth in pending_wikilinks:
        target = orth_to_super.get(orth)
        if target and target != src_uuid:
            links.append((src_uuid, src_type, target, "super-entry", "body-wikilink"))

    # Final dedupe of links (same source/target/type/relation).
    seen_links: set[tuple] = set()
    deduped: list[tuple] = []
    for row in links:
        key = (row[0], row[2], row[3], row[4])
        if key in seen_links:
            continue
        seen_links.add(key)
        deduped.append(row)
    links = deduped

    if out_path.exists():
        out_path.unlink()
    conn = sqlite3.connect(str(out_path))
    try:
        conn.executescript(DDL)
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", str(CORE_SCHEMA_VERSION)),
                ("kind", "core"),
                ("core_root", str(core_root)),
            ],
        )
        conn.executemany(
            "INSERT INTO notes(uuid, type, collection, path, display_label, source_file)"
            " VALUES (?,?,?,?,?,?)",
            notes,
        )
        conn.executemany(
            "INSERT INTO labels(uuid, label, label_type, label_search) VALUES (?,?,?,?)",
            labels,
        )
        conn.executemany(
            "INSERT INTO links(source_uuid, source_type, target_uuid, target_type, relation)"
            " VALUES (?,?,?,?,?)",
            links,
        )
        conn.executemany(
            "INSERT INTO super_entries(uuid, orth, orth_search, word_count)"
            " VALUES (?,?,?,?)",
            super_entries,
        )
        conn.executemany(
            "INSERT INTO super_entry_words(super_entry_uuid, word_uuid, concept, n)"
            " VALUES (?,?,?,?)",
            super_entry_words,
        )
        conn.commit()
    finally:
        conn.close()

    log.info("wrote %s; counts=%s", out_path, counts)
    return out_path


# ---------- traversal & I/O --------------------------------------------------


def _iter_collection(coll_root: Path) -> Iterable[Path]:
    for shard in sorted(coll_root.iterdir()):
        if not shard.is_dir() or len(shard.name) != 1:
            continue
        for md_path in sorted(shard.glob("*.md")):
            yield md_path


def _read_frontmatter(path: Path) -> dict[str, Any]:
    fm, _ = _read_record(path)
    return fm


def _read_record(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    data = yaml.safe_load(match.group(1)) or {}
    fm = data if isinstance(data, dict) else {}
    return fm, match.group(2)


def _strip_code_fences(body: str) -> str:
    """Remove fenced code blocks before mining links."""
    parts: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        parts.append(line)
    return "\n".join(parts)


# ---------- field extraction ------------------------------------------------


def _uuid(fm: dict, md_path: Path) -> str | None:
    raw = fm.get("uuid")
    if isinstance(raw, str) and raw.strip():
        return _strip_uuid_prefix(raw.strip())
    return md_path.stem or None


def _source_file(fm: dict) -> str | None:
    source = fm.get("source")
    if isinstance(source, dict):
        sf = source.get("source_file")
        if isinstance(sf, str):
            return sf
    return None


def _display_label(type_name: str, fm: dict) -> str | None:
    if type_name == "concept":
        return _str_or_none(fm.get("concept"))
    if type_name == "bibliography":
        return _str_or_none(fm.get("citation_label"))
    if type_name == "graph":
        graphs = fm.get("graphs") if isinstance(fm.get("graphs"), dict) else {}
        attested = _str_or_none(graphs.get("attested"))
        if attested:
            return attested
        standardised = _str_or_none(graphs.get("standardised"))
        if standardised:
            return f"{standardised} (standardised)"
        return None
    if type_name in ("syntactic-function", "semantic-feature"):
        return _str_or_none(fm.get("code"))
    if type_name == "word":
        form = fm.get("form") if isinstance(fm.get("form"), dict) else {}
        orth = _str_or_none(form.get("orth")) or _str_or_none(fm.get("super_entry_orth"))
        concept = _str_or_none(fm.get("concept"))
        if orth and concept:
            return f"{orth}: {concept}"
        return orth or concept
    if type_name == "super-entry":
        return _str_or_none(fm.get("orth"))
    return None


def _label_rows(
    uuid: str, type_name: str, fm: dict, display: str,
) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []

    def add(value: Any, kind: str) -> None:
        text = _str_or_none(value)
        if text is None:
            return
        norm = normalize_search_text(text)
        if not norm:
            return
        rows.append((uuid, text, kind, norm))

    add(display, "display")

    if type_name == "concept":
        add(fm.get("concept"), "concept")
        for alt in fm.get("labels") or []:
            add(alt, "alt")
        add(fm.get("zh"), "zh")
        add(fm.get("och"), "och")
    elif type_name == "bibliography":
        add(fm.get("citation_label"), "citation_label")
        for title in fm.get("titles") or []:
            if isinstance(title, dict):
                add(title.get("title"), "title")
        for c in fm.get("contributors") or []:
            if not isinstance(c, dict):
                continue
            _add_contributor_labels(c, add)
    elif type_name == "graph":
        graphs = fm.get("graphs") if isinstance(fm.get("graphs"), dict) else {}
        for k in ("attested", "unemended", "emended", "standardised"):
            add(graphs.get(k), k)
    elif type_name in ("syntactic-function", "semantic-feature"):
        add(fm.get("code"), "code")
    elif type_name == "word":
        form = fm.get("form") if isinstance(fm.get("form"), dict) else {}
        add(form.get("orth"), "orth")
        add(fm.get("concept"), "concept")
        add(fm.get("super_entry_orth"), "super_entry_orth")
    elif type_name == "super-entry":
        add(fm.get("orth"), "orth")
        for f in fm.get("forms") or []:
            if isinstance(f, dict):
                add(f.get("orth"), "form_orth")

    # Dedupe (uuid, label, label_type) — preserve order of first occurrence.
    seen: set[tuple[str, str, str]] = set()
    out: list[tuple[str, str, str, str]] = []
    for row in rows:
        key = (row[0], row[1], row[2])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _add_contributor_labels(contributor: dict, add) -> None:
    """Index contributor name variants (top-level + each entry in ``names``)."""
    sources = [contributor]
    for n in contributor.get("names") or []:
        if isinstance(n, dict):
            sources.append(n)
    for src in sources:
        given = _str_or_none(src.get("given"))
        family = _str_or_none(src.get("family"))
        if family and given:
            add(f"{family} {given}", "contributor")
        if family:
            add(family, "contributor_family")
        if given:
            add(given, "contributor_given")


def _link_rows(
    uuid: str, type_name: str, fm: dict,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Extract structured links from frontmatter only (not body)."""
    rows: list[tuple[str, str, str, str | None, str | None]] = []

    def add(target: Any, target_type: str | None, relation: str | None) -> None:
        target_uuid = _strip_uuid_prefix(target)
        if not target_uuid:
            return
        rows.append((uuid, type_name, target_uuid, target_type, relation))

    if type_name == "word":
        add(fm.get("super_entry_uuid"), "super-entry", "super_entry")
        add(fm.get("concept_uuid"), "concept", "concept")
        form = fm.get("form") if isinstance(fm.get("form"), dict) else {}
        add(form.get("graph_uuid"), "graph", "graph")
        for bib in fm.get("bibliography") or []:
            if isinstance(bib, dict):
                add(bib.get("uuid"), "bibliography", "bibliography")
        for sense in fm.get("senses") or []:
            if not isinstance(sense, dict):
                continue
            for sf in sense.get("syntactic_functions") or []:
                if isinstance(sf, dict):
                    add(sf.get("uuid"), "syntactic-function", "syntactic_function")
            for sf in sense.get("semantic_features") or []:
                if isinstance(sf, dict):
                    add(sf.get("uuid"), "semantic-feature", "semantic_feature")
    elif type_name == "super-entry":
        for f in fm.get("forms") or []:
            if isinstance(f, dict):
                add(f.get("graph_uuid"), "graph", "graph")
        for entry in fm.get("entries") or []:
            if isinstance(entry, dict):
                add(entry.get("uuid"), "word", "word")
                add(entry.get("concept_uuid"), "concept", "concept")
    elif type_name in ("syntactic-function", "semantic-feature"):
        for rel in fm.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            rel_label = rel.get("type")
            target_type = rel.get("target_type") or type_name
            for ref in rel.get("refs") or []:
                if isinstance(ref, dict):
                    add(ref.get("uuid"), target_type, rel_label)

    # Dedupe.
    seen: set[tuple[str, str, str | None, str | None]] = set()
    out: list[tuple[str, str, str, str | None, str | None]] = []
    for row in rows:
        key = (row[2], row[3], row[4], row[1])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _body_link_rows(
    source_uuid: str,
    source_type: str,
    source_collection: str,
    body: str,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Extract explicit markdown links from the body to other core records.

    Cross-collection links carry the collection name in their path (e.g.
    ``../../bibliography/a/<uuid>.md``); same-collection sibling links omit
    it (``../9/<uuid>.md``) and default to ``source_collection``.
    """
    rows: list[tuple[str, str, str, str | None, str | None]] = []
    for _label, href in _BODY_MD_LINK_RE.findall(body):
        href = href.strip()
        if not href:
            continue
        # Strip leading scheme-less anchors and split off any title.
        href = href.split(" ", 1)[0]
        m_cross = _BODY_CROSS_RE.search(href)
        if m_cross:
            target_coll = m_cross.group(1)
            target_uuid = _strip_uuid_prefix(m_cross.group(2))
        else:
            m_same = _BODY_SAME_RE.search(href)
            if not m_same:
                continue
            target_coll = source_collection
            target_uuid = _strip_uuid_prefix(m_same.group(1))
        if not target_uuid or target_uuid == source_uuid:
            continue
        target_type = COLLECTION_TO_TYPE.get(target_coll)
        rows.append((source_uuid, source_type, target_uuid, target_type, "body"))
    return rows


def _body_wikilink_orths(body: str) -> list[str]:
    """Return distinct orth strings referenced via ``[[X]]`` wikilinks."""
    seen: set[str] = set()
    out: list[str] = []
    for inner in _BODY_WIKILINK_RE.findall(body):
        orth = inner.strip()
        if not orth or orth in seen:
            continue
        seen.add(orth)
        out.append(orth)
    return out


# ---------- small helpers ---------------------------------------------------


def _strip_uuid_prefix(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith("uuid-"):
        text = text[len("uuid-"):]
    # Super-entry graph_uuid sometimes embeds a "#uuid-..." trailing comment.
    text = text.split()[0].split("#", 1)[0]
    return text or None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
