"""Build a SQLite index (``.bkki``) over the bkk-core knowledge layer.

The core layer is a tree of pure YAML records organized as
``<collection>/<hex>/<uuid>.yml``. See ``docs/bkk-core/README.md`` for the
on-disk contract.

The index powers the web frontend's CORE browse activity: a list of records
per collection, label-substring search, and a detail-view lookup by uuid.
For the Words collection the list is two-level — super-entries first, then
their constituent word records — so super-entries are indexed even though
they are not browseable as a collection of their own. Senses are indexed as
their own collection but addressed through their parent word.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from bkk.serialize.yaml_io import load_record

from .catalog import normalize_search_text

log = logging.getLogger("bkk.index.core")

CORE_SCHEMA_VERSION = 4

COLLECTIONS: tuple[tuple[str, str], ...] = (
    # (collection dir name, type) — ordered so derived JOINs are populated
    # left-to-right (concepts before words; words before senses & super-entries).
    ("concepts", "concept"),
    ("graphs", "graph"),
    ("syntactic-functions", "syntactic-function"),
    ("semantic-features", "semantic-feature"),
    ("bibliography", "bibliography"),
    ("words", "word"),
    ("senses", "sense"),
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
  source_file TEXT,
  source_hash TEXT
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
  concept_uuid TEXT,
  n TEXT,
  pinyin TEXT,
  PRIMARY KEY (super_entry_uuid, word_uuid)
);

CREATE TABLE senses (
  uuid TEXT PRIMARY KEY,
  word_uuid TEXT NOT NULL,
  sense_ord INTEGER,
  n TEXT,
  pos TEXT,
  def_text TEXT,
  syntactic_function_labels TEXT,
  semantic_feature_labels TEXT
);

CREATE INDEX idx_notes_collection ON notes(collection);
CREATE INDEX idx_notes_type ON notes(type);
CREATE INDEX idx_labels_uuid ON labels(uuid);
CREATE INDEX idx_labels_search ON labels(label_search);
CREATE INDEX idx_links_source ON links(source_uuid);
CREATE INDEX idx_links_target ON links(target_uuid);
CREATE INDEX idx_super_entries_orth_search ON super_entries(orth_search);
CREATE INDEX idx_super_entry_words_word ON super_entry_words(word_uuid);
CREATE INDEX idx_senses_word ON senses(word_uuid);
CREATE INDEX idx_senses_ord ON senses(word_uuid, sense_ord);
"""


# `[[X]]` wikilink in prose fields — resolved against the super-entry orth map.
_WIKILINK_RE = re.compile(r"\[\[([^\]\n|]+)\]\]")

# Prose fields scanned for wikilinks, per record type.
_PROSE_FIELDS: dict[str, tuple[str, ...]] = {
    "concept": ("definition", "words_text"),
    "word": ("definition",),
    "sense": ("definition",),
    "syntactic-function": ("description", "notes"),
    "semantic-feature": ("description", "notes"),
}

COLLECTION_TO_TYPE: dict[str, str] = {coll: t for coll, t in COLLECTIONS}


def build_core_index(core_root: Path | str, out_path: Path | str) -> Path:
    """Walk ``core_root``, load each YAML record, write the SQLite index."""
    core_root = Path(core_root)
    out_path = Path(out_path)

    if not core_root.is_dir():
        raise FileNotFoundError(f"core root not found: {core_root}")

    notes: list[tuple] = []
    labels: list[tuple] = []
    links: list[tuple] = []
    super_entry_rows: list[tuple] = []
    super_entry_word_rows: list[tuple] = []
    sense_rows: list[tuple] = []
    counts: dict[str, int] = {}

    # Caches populated as we walk and consulted by later records:
    #   word_info[word_uuid] = (concept_uuid, n, pinyin)
    #   word_display[word_uuid] = "orth: CONCEPT"
    #   sense_position[sense_uuid] = (word_uuid, ord_index)
    #   syn_code[uuid] / sem_code[uuid] = denormalised `code` for senses pass
    word_info: dict[str, tuple[str | None, str | None, str | None]] = {}
    word_display: dict[str, str] = {}
    sense_position: dict[str, tuple[str, int]] = {}
    concept_display: dict[str, str] = {}
    syn_code: dict[str, str] = {}
    sem_code: dict[str, str] = {}

    # (src_uuid, src_type, orth) — resolved after the super-entry pass.
    pending_wikilinks: list[tuple[str, str, str]] = []

    for coll_dir, type_name in COLLECTIONS:
        coll_root = core_root / coll_dir
        if not coll_root.is_dir():
            log.warning("collection %s not found under %s", coll_dir, core_root)
            continue
        for yml_path in _iter_collection(coll_root):
            try:
                raw_bytes = yml_path.read_bytes()
            except OSError as exc:
                log.warning("%s: read failed: %s", yml_path, exc)
                continue
            try:
                data = load_record(yml_path)
            except Exception as exc:
                log.warning("%s: yaml parse failed: %s", yml_path, exc)
                continue
            if not isinstance(data, dict):
                continue
            uuid = _uuid(data, yml_path)
            if uuid is None:
                log.warning("%s: missing uuid", yml_path)
                continue

            display = _display_label(
                type_name, data,
                concept_display=concept_display,
                word_display=word_display,
                sense_position=sense_position,
            ) or uuid
            rel_path = yml_path.relative_to(core_root).as_posix()
            source_file = _source_file(data)
            source_hash = hashlib.sha1(raw_bytes).hexdigest()
            notes.append((uuid, type_name, coll_dir, rel_path, display, source_file, source_hash))
            labels.extend(_label_rows(uuid, type_name, data, display))
            links.extend(_link_rows(uuid, type_name, data))
            counts[type_name] = counts.get(type_name, 0) + 1

            # Wikilink discovery against prose fields only.
            for orth in _wikilink_orths(type_name, data):
                pending_wikilinks.append((uuid, type_name, orth))

            if type_name == "concept":
                concept_display[uuid] = display
            elif type_name == "syntactic-function":
                syn_code[uuid] = display
            elif type_name == "semantic-feature":
                sem_code[uuid] = display
            elif type_name == "word":
                concept_uuid = _strip_uuid_prefix(data.get("concept_uuid"))
                n = _str_or_none(data.get("n"))
                pinyin = _word_pinyin(data)
                word_info[uuid] = (concept_uuid, n, pinyin)
                word_display[uuid] = display
                for ord_index, sense_uuid_raw in enumerate(data.get("sense_uuids") or []):
                    sense_uuid = _strip_uuid_prefix(sense_uuid_raw)
                    if sense_uuid:
                        sense_position[sense_uuid] = (uuid, ord_index)
            elif type_name == "sense":
                word_uuid = _strip_uuid_prefix(data.get("word_uuid"))
                pos_value, ord_index = sense_position.get(uuid, (word_uuid, None))
                syn_labels = _resolve_label_list(
                    data.get("syntactic_function_uuids"), syn_code,
                )
                sem_labels = _resolve_label_list(
                    data.get("semantic_feature_uuids"), sem_code,
                )
                sense_rows.append((
                    uuid,
                    word_uuid or pos_value or "",
                    ord_index,
                    _str_or_none(data.get("n")),
                    _str_or_none(data.get("pos")),
                    _str_or_none(data.get("definition")),
                    syn_labels,
                    sem_labels,
                ))
            elif type_name == "super-entry":
                orth = _str_or_none(data.get("orth")) or display
                word_uuid_list = [
                    _strip_uuid_prefix(u)
                    for u in (data.get("word_uuids") or [])
                ]
                word_uuid_list = [u for u in word_uuid_list if u]
                super_entry_rows.append((
                    uuid, orth, normalize_search_text(orth) or "",
                    len(word_uuid_list),
                ))
                for word_uuid in word_uuid_list:
                    concept_uuid, n, pinyin = word_info.get(
                        word_uuid, (None, None, None),
                    )
                    concept_label = (
                        concept_display.get(concept_uuid)
                        if concept_uuid else None
                    )
                    super_entry_word_rows.append((
                        uuid, word_uuid, concept_label, concept_uuid, n, pinyin,
                    ))

    # Resolve wikilinks against the super-entry orth → uuid map.
    orth_to_super: dict[str, str] = {}
    for se_uuid, orth, _orth_search, _count in super_entry_rows:
        orth_to_super.setdefault(orth, se_uuid)
    for src_uuid, src_type, orth in pending_wikilinks:
        target = orth_to_super.get(orth)
        if target and target != src_uuid:
            links.append((src_uuid, src_type, target, "super-entry", "wikilink"))

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
            "INSERT INTO notes(uuid, type, collection, path, display_label, source_file, source_hash)"
            " VALUES (?,?,?,?,?,?,?)",
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
            super_entry_rows,
        )
        conn.executemany(
            "INSERT INTO super_entry_words"
            "(super_entry_uuid, word_uuid, concept, concept_uuid, n, pinyin)"
            " VALUES (?,?,?,?,?,?)",
            super_entry_word_rows,
        )
        conn.executemany(
            "INSERT INTO senses"
            "(uuid, word_uuid, sense_ord, n, pos, def_text,"
            " syntactic_function_labels, semantic_feature_labels)"
            " VALUES (?,?,?,?,?,?,?,?)",
            sense_rows,
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
        for yml_path in sorted(shard.glob("*.yml")):
            yield yml_path


def _word_pinyin(data: dict) -> str | None:
    """Pull the pinyin pronunciation out of a word's form."""
    form = data.get("form") if isinstance(data.get("form"), dict) else {}
    prons = form.get("pronunciations") if isinstance(form, dict) else None
    if not isinstance(prons, list):
        return None
    for p in prons:
        if not isinstance(p, dict):
            continue
        if p.get("lang") == "zh-Latn-x-pinyin":
            value = _str_or_none(p.get("value"))
            if value:
                return value
    return None


# ---------- field extraction ------------------------------------------------


def _uuid(data: dict, yml_path: Path) -> str | None:
    raw = data.get("uuid")
    if isinstance(raw, str) and raw.strip():
        return _strip_uuid_prefix(raw.strip())
    return yml_path.stem or None


def _source_file(data: dict) -> str | None:
    source = data.get("source")
    if isinstance(source, dict):
        sf = source.get("source_file")
        if isinstance(sf, str):
            return sf
    return None


def _display_label(
    type_name: str,
    data: dict,
    *,
    concept_display: dict[str, str],
    word_display: dict[str, str],
    sense_position: dict[str, tuple[str, int]],
) -> str | None:
    if type_name == "concept":
        return _str_or_none(data.get("concept"))
    if type_name == "bibliography":
        return _str_or_none(data.get("citation_label"))
    if type_name == "graph":
        graphs = data.get("graphs") if isinstance(data.get("graphs"), dict) else {}
        attested = _str_or_none(graphs.get("attested"))
        if attested:
            return attested
        standardised = _str_or_none(graphs.get("standardised"))
        if standardised:
            return f"{standardised} (standardised)"
        return None
    if type_name in ("syntactic-function", "semantic-feature"):
        return _str_or_none(data.get("code"))
    if type_name == "word":
        form = data.get("form") if isinstance(data.get("form"), dict) else {}
        orth = _str_or_none(form.get("orth"))
        concept_uuid = _strip_uuid_prefix(data.get("concept_uuid"))
        concept = concept_display.get(concept_uuid) if concept_uuid else None
        if orth and concept:
            return f"{orth}: {concept}"
        return orth or concept
    if type_name == "super-entry":
        return _str_or_none(data.get("orth"))
    if type_name == "sense":
        uuid = _strip_uuid_prefix(data.get("uuid")) or ""
        word_uuid_field = _strip_uuid_prefix(data.get("word_uuid"))
        pos_word, ord_index = sense_position.get(uuid, (word_uuid_field, None))
        parent_label = word_display.get(pos_word or "") if pos_word else None
        ord_text = str(ord_index + 1) if ord_index is not None else None
        if parent_label and ord_text:
            return f"{parent_label} (sense {ord_text})"
        if parent_label:
            return parent_label
        return _str_or_none(data.get("definition"))
    return None


def _label_rows(
    uuid: str, type_name: str, data: dict, display: str,
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
        add(data.get("concept"), "concept")
        for alt in data.get("alt_labels") or []:
            add(alt, "alt")
        add(data.get("zh"), "zh")
        add(data.get("och"), "och")
    elif type_name == "bibliography":
        add(data.get("citation_label"), "citation_label")
        for title in data.get("titles") or []:
            if isinstance(title, dict):
                add(title.get("title"), "title")
        for c in data.get("contributors") or []:
            if not isinstance(c, dict):
                continue
            _add_contributor_labels(c, add)
    elif type_name == "graph":
        graphs = data.get("graphs") if isinstance(data.get("graphs"), dict) else {}
        for k in ("attested", "unemended", "emended", "standardised"):
            add(graphs.get(k), k)
    elif type_name in ("syntactic-function", "semantic-feature"):
        add(data.get("code"), "code")
    elif type_name == "word":
        form = data.get("form") if isinstance(data.get("form"), dict) else {}
        add(form.get("orth"), "orth")
    elif type_name == "super-entry":
        add(data.get("orth"), "orth")
        for f in data.get("forms") or []:
            if isinstance(f, dict):
                add(f.get("orth"), "form_orth")
    elif type_name == "sense":
        add(data.get("definition"), "definition")

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
    uuid: str, type_name: str, data: dict,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Extract structured links from a record's typed relation fields."""
    rows: list[tuple[str, str, str, str | None, str | None]] = []

    def add(target: Any, target_type: str | None, relation: str | None) -> None:
        target_uuid = _strip_uuid_prefix(target)
        if not target_uuid:
            return
        rows.append((uuid, type_name, target_uuid, target_type, relation))

    def add_each(values: Any, target_type: str | None, relation: str | None) -> None:
        if not isinstance(values, list):
            return
        for v in values:
            add(v, target_type, relation)

    if type_name == "concept":
        add_each(data.get("antonyms"), "concept", "antonym")
        add_each(data.get("hypernyms"), "concept", "hypernym")
        add_each(data.get("hyponyms"), "concept", "hyponym")
        add_each(data.get("see_also"), "concept", "see")
        for other in data.get("other_relations") or []:
            if isinstance(other, dict):
                add_each(other.get("uuids"), "concept", other.get("type"))
        for bib in data.get("bibliography") or []:
            if isinstance(bib, dict):
                add(bib.get("bibliography_uuid"), "bibliography", "bibliography")
    elif type_name == "word":
        add(data.get("super_entry_uuid"), "super-entry", "super_entry")
        add(data.get("concept_uuid"), "concept", "concept")
        form = data.get("form") if isinstance(data.get("form"), dict) else {}
        add_each(form.get("graph_uuids"), "graph", "graph")
        for bib in data.get("bibliography") or []:
            if isinstance(bib, dict):
                add(bib.get("bibliography_uuid"), "bibliography", "bibliography")
        add_each(data.get("sense_uuids"), "sense", "sense")
    elif type_name == "sense":
        add(data.get("word_uuid"), "word", "word")
        add_each(
            data.get("syntactic_function_uuids"),
            "syntactic-function", "syntactic_function",
        )
        add_each(
            data.get("semantic_feature_uuids"),
            "semantic-feature", "semantic_feature",
        )
    elif type_name == "super-entry":
        add_each(data.get("word_uuids"), "word", "word")
        for f in data.get("forms") or []:
            if isinstance(f, dict):
                add_each(f.get("graph_uuids"), "graph", "graph")
    elif type_name == "syntactic-function":
        add_each(data.get("taxonomy_parents"), "syntactic-function", "taxonomy")
    elif type_name == "semantic-feature":
        add_each(data.get("taxonomy_parents"), "semantic-feature", "taxonomy")
        for ref in data.get("source_references") or []:
            if isinstance(ref, dict):
                add(
                    ref.get("bibliography_uuid"),
                    "bibliography", "source_reference",
                )

    # Dedupe.
    seen: set[tuple] = set()
    out: list[tuple[str, str, str, str | None, str | None]] = []
    for row in rows:
        key = (row[2], row[3], row[4], row[1])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


# ---------- wikilink discovery ----------------------------------------------


def _wikilink_orths(type_name: str, data: dict) -> Iterable[str]:
    """Yield distinct orth strings referenced via ``[[X]]`` in prose fields."""
    fields = _PROSE_FIELDS.get(type_name)
    if not fields:
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for source in _prose_sources(type_name, data, fields):
        if not isinstance(source, str):
            continue
        for inner in _WIKILINK_RE.findall(source):
            orth = inner.strip()
            if not orth or orth in seen:
                continue
            seen.add(orth)
            out.append(orth)
    return out


def _prose_sources(type_name: str, data: dict, fields: tuple[str, ...]) -> Iterable[str]:
    for field in fields:
        value = data.get(field)
        if isinstance(value, str):
            yield value
    if type_name == "concept":
        for section in data.get("criteria") or []:
            if isinstance(section, dict):
                text = section.get("text")
                if isinstance(text, str):
                    yield text


# ---------- small helpers ---------------------------------------------------


def _strip_uuid_prefix(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith("uuid-"):
        text = text[len("uuid-"):]
    text = text.split()[0].split("#", 1)[0]
    return text or None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_label_list(
    uuids: Any, lookup: dict[str, str],
) -> str | None:
    """Join the labels for a list of uuids; preserves positional empty slots."""
    if not isinstance(uuids, list):
        return None
    parts: list[str] = []
    any_resolved = False
    for raw in uuids:
        uuid = _strip_uuid_prefix(raw)
        label = lookup.get(uuid, "") if uuid else ""
        if label:
            any_resolved = True
        parts.append(label)
    if not parts or not any_resolved:
        return None
    return ", ".join(parts)
