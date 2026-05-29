"""Build a lightweight SQLite catalog index (``.bkkc``)."""

from __future__ import annotations

import csv
import json
import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from typing import Any
from pathlib import Path

import yaml

from .merge import discover_bundles

log = logging.getLogger("bkk.index")

CATALOG_SCHEMA_VERSION = 4

DDL = """
CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE catalog_bundle (
  textid TEXT PRIMARY KEY,
  section_code TEXT NOT NULL,
  title TEXT,
  title_pinyin TEXT,
  title_pinyin_search TEXT,
  title_english TEXT,
  not_before INTEGER,
  not_after INTEGER,
  dzt_date INTEGER,
  index_date INTEGER NOT NULL,
  index_date_source TEXT NOT NULL,
  canonical_identifier TEXT,
  manifest_hash TEXT
);

CREATE TABLE catalog_identifier (
  textid TEXT NOT NULL,
  kind TEXT NOT NULL,
  value TEXT NOT NULL,
  value_search TEXT NOT NULL,
  FOREIGN KEY(textid) REFERENCES catalog_bundle(textid)
);

CREATE TABLE catalog_section (
  code TEXT PRIMARY KEY,
  parent_code TEXT,
  title TEXT,
  title_pinyin TEXT,
  title_english TEXT,
  direct_bundle_count INTEGER NOT NULL,
  descendant_bundle_count INTEGER NOT NULL
);

CREATE INDEX idx_catalog_bundle_section ON catalog_bundle(section_code);
CREATE INDEX idx_catalog_bundle_index_date ON catalog_bundle(index_date);
CREATE INDEX idx_catalog_identifier_textid ON catalog_identifier(textid);
CREATE INDEX idx_catalog_identifier_value ON catalog_identifier(value_search);
CREATE INDEX idx_catalog_section_parent ON catalog_section(parent_code);

CREATE TABLE catalog_translation (
  id TEXT PRIMARY KEY,
  source_textid TEXT NOT NULL,
  path TEXT NOT NULL,
  canonical_identifier TEXT,
  source_canonical_identifier TEXT,
  language TEXT,
  title TEXT,
  original_title TEXT,
  responsibility TEXT NOT NULL,
  date TEXT,
  license TEXT,
  juan_count INTEGER NOT NULL,
  seg_count INTEGER NOT NULL
);

CREATE INDEX idx_catalog_translation_source ON catalog_translation(source_textid);
CREATE INDEX idx_catalog_translation_language ON catalog_translation(language);
CREATE INDEX idx_catalog_translation_title ON catalog_translation(title);
"""

REQUIRED_COLUMNS = {
    "id", "title", "titlePinyin", "titleEnglish",
    "notBefore", "notAfter", "dzt_date",
}
_BUNDLE_ID_RE = re.compile(r"^(?P<section>KR\d+[a-z]+)(?P<number>\d+)$")
_YEAR_RE = re.compile(r"(?<!\d)-?\s*\d+")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)
_SOURCE_ID_RE = re.compile(r"bkk:[^/]+/([^/]+)/")
MISSING_INDEX_DATE = 9999


@dataclass(frozen=True)
class CatalogRow:
    id: str
    title: str | None
    title_pinyin: str | None
    title_english: str | None
    not_before: int | None
    not_after: int | None
    dzt_not_before: int | None
    dzt_not_after: int | None


@dataclass(frozen=True)
class IndexDate:
    value: int
    source: str


def build_catalog_index(
    corpus: Path | str,
    csv_path: Path | str,
    out_path: Path | str,
    *,
    prefix: str | None = None,
) -> Path:
    """Build a ``.bkkc`` catalog index for bundles present in ``corpus``."""
    corpus = Path(corpus)
    csv_path = Path(csv_path)
    out_path = Path(out_path)

    catalog_rows = _read_frontmatter_csv(csv_path)
    sections = {
        code: row for code, row in catalog_rows.items()
        if not _section_code(code)
    }
    bundle_rows = {
        code: row for code, row in catalog_rows.items()
        if _section_code(code)
    }
    bundles = discover_bundles(corpus, prefix=prefix)
    if not bundles:
        raise FileNotFoundError(
            f"no bundles found under {corpus}"
            + (f" with prefix {prefix!r}" if prefix else "")
        )

    bundle_records: dict[str, tuple] = {}
    identifier_records: list[tuple[str, str, str, str]] = []
    direct_counts: dict[str, int] = {}
    for bundle_dir in bundles:
        textid = bundle_dir.name
        if textid in bundle_records:
            log.warning(
                "%s: duplicate bundle discovered at %s; keeping first row",
                textid, bundle_dir,
            )
            continue
        manifest = _read_manifest(bundle_dir)
        row = bundle_rows.get(textid)
        section_code = _section_code(textid)
        if section_code is None:
            log.warning("%s: skipping catalog row without section code", textid)
            continue
        if row is None:
            log.warning(
                "%s: catalog row missing from %s; using bundle metadata",
                textid, csv_path,
            )
            row = _catalog_row_from_manifest(textid, manifest)
            index_date = IndexDate(MISSING_INDEX_DATE, "missing")
        else:
            index_date = _calculate_index_date(
                row.not_before, row.not_after,
                row.dzt_not_before, row.dzt_not_after,
            )
            if index_date is None:
                log.warning("%s: catalog row without usable date; using 9999", textid)
                index_date = IndexDate(MISSING_INDEX_DATE, "missing")
        bundle_records[textid] = (
            textid,
            section_code,
            row.title,
            row.title_pinyin,
            normalize_search_text(row.title_pinyin),
            row.title_english,
            row.not_before,
            row.not_after,
            index_date.value if index_date.source.startswith("dzt_date") else None,
            index_date.value,
            index_date.source,
            manifest.get("canonical_identifier"),
            manifest.get("hash"),
        )
        identifier_records.extend(_identifier_records(textid, manifest))
        direct_counts[section_code] = direct_counts.get(section_code, 0) + 1

    section_records = _section_records(sections, direct_counts)
    translation_records = _translation_records(corpus)

    if out_path.exists():
        out_path.unlink()
    conn = sqlite3.connect(str(out_path))
    try:
        conn.executescript(DDL)
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", str(CATALOG_SCHEMA_VERSION)),
                ("kind", "catalog"),
                ("corpus", str(corpus)),
                ("source_csv", str(csv_path)),
            ],
        )
        conn.executemany(
            "INSERT INTO catalog_bundle("
            "textid, section_code, title, title_pinyin, title_pinyin_search, "
            "title_english, "
            "not_before, not_after, dzt_date, index_date, index_date_source, "
            "canonical_identifier, manifest_hash"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            list(bundle_records.values()),
        )
        conn.executemany(
            "INSERT INTO catalog_identifier("
            "textid, kind, value, value_search"
            ") VALUES (?,?,?,?)",
            identifier_records,
        )
        conn.executemany(
            "INSERT INTO catalog_section("
            "code, parent_code, title, title_pinyin, title_english, "
            "direct_bundle_count, descendant_bundle_count"
            ") VALUES (?,?,?,?,?,?,?)",
            section_records,
        )
        conn.executemany(
            "INSERT INTO catalog_translation("
            "id, source_textid, path, canonical_identifier, "
            "source_canonical_identifier, language, title, original_title, "
            "responsibility, date, license, juan_count, seg_count"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            translation_records,
        )
        conn.commit()
    finally:
        conn.close()
    return out_path


def default_catalog_csv(start: Path | None = None) -> Path | None:
    """Return the nearest ``catalog/frontmatter.csv`` from ``start`` upward."""
    start = Path.cwd() if start is None else Path(start)
    base = start if start.is_dir() else start.parent
    for directory in (base, *base.parents):
        candidate = directory / "catalog" / "frontmatter.csv"
        if candidate.is_file():
            return candidate
    return None


def parse_year(raw: str | int | None) -> int | None:
    """Parse catalog year strings, including BCE forms like ``- 390``."""
    years = parse_years(raw)
    return years[0] if years else None


def parse_years(raw: str | int | None) -> list[int]:
    """Parse one or more catalog year values from a cell."""
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []
    return [_year_from_match(m) for m in _YEAR_RE.finditer(text)]


def normalize_search_text(raw: str | int | None) -> str | None:
    """Normalize catalog search text; notably strips pinyin tone marks."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(
        ch for ch in decomposed if unicodedata.category(ch) != "Mn"
    )
    return unicodedata.normalize("NFC", stripped)


def calculate_index_date(
    not_before: int | None,
    not_after: int | None,
    dzt_date: int | tuple[int, int] | None,
) -> tuple[int, str] | None:
    """Return ``(index_date, source)`` using the catalog date rules."""
    if isinstance(dzt_date, tuple):
        dzt_not_before, dzt_not_after = dzt_date
    else:
        dzt_not_before = dzt_not_after = dzt_date
    value = _calculate_index_date(
        not_before, not_after, dzt_not_before, dzt_not_after,
    )
    if value is None:
        return None
    return value.value, value.source


def _calculate_index_date(
    not_before: int | None,
    not_after: int | None,
    dzt_not_before: int | None,
    dzt_not_after: int | None,
) -> IndexDate | None:
    if dzt_not_before is not None and dzt_not_after is not None:
        return _date_from_bounds(
            dzt_not_before, dzt_not_after,
            single_source="dzt_date",
            midpoint_source="dzt_date_midpoint",
            wide_source="dzt_date_not_before_wide_range",
        )
    if not_before is None or not_after is None:
        return None
    return _date_from_bounds(
        not_before, not_after,
        single_source="midpoint",
        midpoint_source="midpoint",
        wide_source="not_before_wide_range",
    )


def _date_from_bounds(
    start: int,
    end: int,
    *,
    single_source: str,
    midpoint_source: str,
    wide_source: str,
) -> IndexDate:
    if end < start:
        start, end = end, start
    if start == end:
        return IndexDate(start, single_source)
    if end - start > 100:
        return IndexDate(start, wide_source)
    return IndexDate((start + end) // 2, midpoint_source)


def _read_frontmatter_csv(csv_path: Path) -> dict[str, CatalogRow]:
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"{csv_path} missing required column(s): {sorted(missing)!r}"
            )
        rows: dict[str, CatalogRow] = {}
        for raw in reader:
            cid = (raw.get("id") or "").strip()
            if not cid:
                continue
            dzt_years = parse_years(raw.get("dzt_date"))
            rows[cid] = CatalogRow(
                id=cid,
                title=_clean(raw.get("title")),
                title_pinyin=_clean(raw.get("titlePinyin")),
                title_english=_clean(raw.get("titleEnglish")),
                not_before=parse_year(raw.get("notBefore")),
                not_after=parse_year(raw.get("notAfter")),
                dzt_not_before=min(dzt_years) if dzt_years else None,
                dzt_not_after=max(dzt_years) if dzt_years else None,
            )
    return rows


def _year_from_match(match: re.Match[str]) -> int:
    raw = match.group(0)
    value = int(raw.replace(" ", ""))
    return value


def _section_code(textid: str) -> str | None:
    match = _BUNDLE_ID_RE.fullmatch(textid)
    if not match:
        return None
    section = match.group("section")
    return section or None


def _clean(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    return text or None


def _read_manifest(bundle_dir: Path) -> dict:
    textid = bundle_dir.name
    manifest_path = bundle_dir / f"{textid}.manifest.yaml"
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        log.warning(
            "%s: manifest parse failed while building catalog index: %s",
            textid, exc,
        )
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _identifier_records(textid: str, manifest: dict) -> list[tuple[str, str, str, str]]:
    values: list[tuple[str, str]] = [("textid", textid)]
    canonical_identifier = manifest.get("canonical_identifier")
    if isinstance(canonical_identifier, (str, int)):
        values.append(("canonical_identifier", str(canonical_identifier)))

    for source in _manifest_identifier_sources(manifest):
        for key, raw in source.items():
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, (str, int)):
                        values.append((str(key), str(item)))
            elif isinstance(raw, (str, int)):
                values.append((str(key), str(raw)))

    seen: set[tuple[str, str]] = set()
    records: list[tuple[str, str, str, str]] = []
    for kind, value in values:
        value = value.strip()
        value_search = normalize_search_text(value)
        if not value or value_search is None:
            continue
        dedupe_key = (kind, value)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        records.append((textid, kind, value, value_search))
    return records


def _manifest_identifier_sources(manifest: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    metadata = manifest.get("metadata") or {}
    if isinstance(metadata, dict):
        identifiers = metadata.get("identifiers") or {}
        if isinstance(identifiers, dict):
            out.append(identifiers)
    top_level = manifest.get("identifiers") or {}
    if isinstance(top_level, dict):
        out.append(top_level)
    return out


def _catalog_row_from_manifest(textid: str, manifest: dict) -> CatalogRow:
    metadata = manifest.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    title = metadata.get("title")
    return CatalogRow(
        id=textid,
        title=title if isinstance(title, str) and title.strip() else textid,
        title_pinyin=None,
        title_english=None,
        not_before=None,
        not_after=None,
        dzt_not_before=None,
        dzt_not_after=None,
    )


def _section_records(
    sections: dict[str, CatalogRow],
    direct_counts: dict[str, int],
) -> list[tuple[str, str | None, str | None, str | None, str | None, int, int]]:
    descendant_counts = {
        code: sum(count for section, count in direct_counts.items()
                  if section == code or section.startswith(code))
        for code in sections
    }
    records = []
    section_codes = set(sections)
    for code in sorted(sections):
        row = sections[code]
        records.append((
            code,
            _parent_code(code, section_codes),
            row.title,
            row.title_pinyin,
            row.title_english,
            direct_counts.get(code, 0),
            descendant_counts.get(code, 0),
        ))
    return records


def _parent_code(code: str, section_codes: set[str]) -> str | None:
    candidates = [
        other for other in section_codes
        if other != code and len(other) < len(code) and code.startswith(other)
    ]
    if not candidates:
        return None
    return max(candidates, key=len)


def _find_translation_roots(corpus: Path) -> list[Path]:
    """Return all ``translations/`` directories directly under or one level below ``corpus``."""
    direct = corpus / "translations"
    if direct.is_dir():
        return [direct]
    out = []
    for sub in sorted(corpus.iterdir()):
        if sub.is_dir() and not sub.name.startswith("_"):
            candidate = sub / "translations"
            if candidate.is_dir():
                out.append(candidate)
    return out


def _translation_records(corpus: Path) -> list[tuple]:
    roots = _find_translation_roots(corpus)
    if not roots:
        return []
    bundle_paths = {
        path
        for root in roots
        for pattern in ("*/*/*/*.md", "*/*/*/*/*.md")
        for path in root.glob(pattern)
    }
    records: list[tuple] = []
    seen: set[str] = set()
    for bundle_md in sorted(bundle_paths):
        bundle_id = bundle_md.stem
        if bundle_md.parent.name != bundle_id or bundle_id in seen:
            continue
        seen.add(bundle_id)
        try:
            manifest, _body = _read_markdown_frontmatter(bundle_md)
        except Exception as exc:
            log.warning("%s: translation manifest parse failed: %s", bundle_md, exc)
            continue
        source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        source_canonical_identifier = source.get("canonical_identifier")
        source_textid = _translation_source_textid(bundle_md.parent, source_canonical_identifier)
        responsibility = [
            item for item in (manifest.get("responsibility") or [])
            if isinstance(item, dict)
        ]
        juan_entries = [
            entry for entry in (manifest.get("juan") or [])
            if isinstance(entry, dict)
            and isinstance(entry.get("seq"), int)
            and isinstance(entry.get("file"), str)
        ]
        juan_count = len(juan_entries)
        seg_count = sum(
            entry["segs"] for entry in juan_entries
            if isinstance(entry.get("segs"), int)
        )
        records.append((
            bundle_id,
            source_textid,
            str(bundle_md.parent),
            manifest.get("canonical_identifier"),
            source_canonical_identifier,
            manifest.get("language"),
            manifest.get("title"),
            manifest.get("original_title"),
            json.dumps(responsibility, ensure_ascii=False, sort_keys=True),
            manifest.get("date"),
            manifest.get("license"),
            juan_count,
            seg_count,
        ))
    return records


def _read_markdown_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    data = yaml.safe_load(match.group(1)) or {}
    return data if isinstance(data, dict) else {}, match.group(2)


def _translation_source_textid(bundle_dir: Path, source_canonical_identifier: Any) -> str:
    if isinstance(source_canonical_identifier, str):
        match = _SOURCE_ID_RE.match(source_canonical_identifier)
        if match:
            return match.group(1)
    try:
        return bundle_dir.parents[1].name
    except IndexError:
        return "_unknown"


