"""Read Markdown translation bundles in-place for the serve API."""

from __future__ import annotations

import re
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .schemas import (
    SearchDateFacets,
    SearchFacetValue,
    TranslationAlignedRow,
    TranslationAlignmentResponse,
    TranslationResponsibility,
    TranslationSearchFacets,
    TranslationSearchResponse,
    TranslationSegmentHit,
    TranslationSummary,
)
from bkk.index.catalog import normalize_search_text


FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)
_AI_RE = re.compile(r"\bAI\b")


def _is_ai_translation(title: str | None, responsibility: list[Any] | None = None) -> bool:
    if _AI_RE.search(title or ""):
        return True
    for item in (responsibility or []):
        if isinstance(item, dict) and _AI_RE.search(item.get("name") or ""):
            return True
    return False
SPAN_RE = re.compile(r"\[((?:\\.|[^\]\\])*)\]\{([^}]*)\}")
SOURCE_ID_RE = re.compile(r"bkk:[^/]+/([^/]+)/")


@dataclass(frozen=True)
class TranslationSegment:
    text: str
    ref: list[str]
    corresp: list[str]
    resp: str | None = None


@dataclass(frozen=True)
class TranslationJuan:
    seq: int
    label: str
    path: Path
    segments: list[TranslationSegment]


@dataclass(frozen=True)
class TranslationBundle:
    id: str
    path: Path
    manifest: dict[str, Any]
    source_textid: str
    summary: TranslationSummary
    juans: dict[int, TranslationJuan] = field(default_factory=dict)


def translation_root(corpus_root: Path) -> Path:
    return corpus_root / "translations"


def list_translation_bundles(
    corpus_root: Path,
    *,
    q: str | None = None,
    source_textid: str | None = None,
    lang: str | None = None,
) -> list[TranslationBundle]:
    root = translation_root(corpus_root)
    if not root.is_dir():
        return []
    query = (q or "").casefold().strip()
    out: list[TranslationBundle] = []
    bundle_paths = {
        path
        for pattern in ("*/*/*/*.md", "*/*/*/*/*.md")
        for path in root.glob(pattern)
    }
    for bundle_md in sorted(bundle_paths):
        bundle_id = bundle_md.stem
        if bundle_md.parent.name != bundle_id:
            continue
        try:
            bundle = load_translation_bundle(bundle_md.parent, include_juans=False)
        except Exception:
            continue
        if source_textid and bundle.source_textid != source_textid:
            continue
        if lang and bundle.summary.language != lang:
            continue
        if query:
            if _matches_metadata_query(bundle, query):
                out.append(bundle)
                continue
            bundle = load_translation_bundle(bundle_md.parent, include_juans=True)
            if not _matches_text_query(bundle, query):
                continue
        out.append(bundle)
    return out


def list_translation_bundles_from_catalog(
    conn: sqlite3.Connection,
    *,
    search_conn: sqlite3.Connection | None = None,
    q: str | None = None,
    source_textid: str | None = None,
    lang: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[TranslationBundle], int]:
    where: list[str] = []
    params: list[Any] = []
    if source_textid:
        where.append("t.source_textid = ?")
        params.append(source_textid)
    if lang:
        where.append("t.language = ?")
        params.append(lang)
    query = (q or "").strip()
    if query:
        query_search = normalize_search_text(query) or query.casefold()
        like = f"%{query_search}%"
        raw_like = f"%{query.casefold()}%"
        meta_cond = (
            "lower(t.id) LIKE ? OR lower(t.source_textid) LIKE ? OR "
            "lower(coalesce(t.language, '')) LIKE ? OR "
            "lower(coalesce(t.title, '')) LIKE ? OR "
            "lower(coalesce(t.original_title, '')) LIKE ? OR "
            "lower(t.responsibility) LIKE ?"
        )
        meta_params = [raw_like, raw_like, raw_like, raw_like, raw_like, raw_like]
        if search_conn is not None:
            fulltext_rows = search_conn.execute(
                "SELECT DISTINCT translation_id FROM translation_segment "
                "WHERE text_search LIKE ?",
                [like],
            ).fetchall()
            fulltext_ids = [row[0] for row in fulltext_rows]
            if fulltext_ids:
                placeholders = ",".join("?" * len(fulltext_ids))
                where.append(f"({meta_cond} OR t.id IN ({placeholders}))")
                params.extend(meta_params + fulltext_ids)
            else:
                where.append(f"({meta_cond})")
                params.extend(meta_params)
        else:
            where.append(f"({meta_cond})")
            params.extend(meta_params)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    total = int(conn.execute(
        f"SELECT COUNT(*) FROM catalog_translation t {where_sql}", params,
    ).fetchone()[0])
    rows = conn.execute(
        "SELECT id, source_textid, path, canonical_identifier, "
        "source_canonical_identifier, language, title, original_title, "
        "responsibility, date, license, juan_count, seg_count, source_juans "
        f"FROM catalog_translation t {where_sql} "
        "ORDER BY source_textid, language, id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return [_bundle_from_catalog_row(row) for row in rows], total


def load_translation_bundle_from_catalog(
    conn: sqlite3.Connection,
    *,
    translation_id: str,
    source_textid: str | None = None,
    include_juans: bool = True,
) -> TranslationBundle | None:
    where = ["id = ?"]
    params: list[Any] = [translation_id]
    if source_textid is not None:
        where.append("source_textid = ?")
        params.append(source_textid)
    row = conn.execute(
        "SELECT id, source_textid, path, canonical_identifier, "
        "source_canonical_identifier, language, title, original_title, "
        "responsibility, date, license, juan_count, seg_count, source_juans "
        f"FROM catalog_translation WHERE {' AND '.join(where)}",
        params,
    ).fetchone()
    if row is None:
        return None
    bundle = _bundle_from_catalog_row(row)
    if include_juans:
        return load_translation_bundle(bundle.path, include_juans=True)
    return bundle


def load_translation_bundle(
    bundle_dir: Path,
    *,
    include_juans: bool = True,
) -> TranslationBundle:
    bundle_id = bundle_dir.name
    manifest_path = bundle_dir / f"{bundle_id}.md"
    manifest, _body = _read_frontmatter(manifest_path)
    source_textid = _source_textid(bundle_dir, manifest)
    juans: dict[int, TranslationJuan] = {}
    total_segments = 0
    for entry in manifest.get("juan") or []:
        if not isinstance(entry, dict):
            continue
        seq = entry.get("seq")
        label = entry.get("label")
        filename = entry.get("file")
        if not isinstance(seq, int) or not isinstance(filename, str):
            continue
        try:
            source_seq = int(label)
        except (TypeError, ValueError):
            source_seq = seq
        if not include_juans:
            continue
        path = bundle_dir / filename
        if not path.is_file():
            continue
        juan = _read_translation_juan(path)
        juans[source_seq] = juan
        total_segments += len(juan.segments)
    source_juans: list[int] = []
    for entry in manifest.get("juan") or []:
        if not isinstance(entry, dict):
            continue
        label = entry.get("label")
        try:
            source_juans.append(int(label))
        except (TypeError, ValueError):
            pass
    summary = _summary(bundle_id, manifest, source_textid, len(juans), total_segments, source_juans)
    if not include_juans:
        total_segments = 0
    return TranslationBundle(
        id=bundle_id,
        path=bundle_dir,
        manifest=manifest,
        source_textid=source_textid,
        summary=summary,
        juans=juans,
    )


def _bundle_from_catalog_row(row: sqlite3.Row | tuple) -> TranslationBundle:
    (
        bundle_id,
        source_textid,
        path,
        canonical_identifier,
        source_canonical_identifier,
        language,
        title,
        original_title,
        responsibility_raw,
        date,
        license_,
        juan_count,
        seg_count,
        source_juans_raw,
    ) = row
    try:
        responsibility = json.loads(responsibility_raw or "[]")
    except json.JSONDecodeError:
        responsibility = []
    try:
        source_juans = [x for x in json.loads(source_juans_raw or "[]") if isinstance(x, int)]
    except json.JSONDecodeError:
        source_juans = []
    resp = [
        TranslationResponsibility(
            role=item.get("role") if isinstance(item.get("role"), str) else None,
            name=item.get("name") if isinstance(item.get("name"), str) else None,
        )
        for item in responsibility
        if isinstance(item, dict)
    ]
    summary = TranslationSummary(
        id=bundle_id,
        source_textid=source_textid,
        canonical_identifier=canonical_identifier,
        source_canonical_identifier=source_canonical_identifier,
        language=language,
        title=title,
        original_title=original_title,
        responsibility=resp,
        date=date,
        license=license_,
        juan_count=juan_count,
        segment_count=seg_count,
        source_juans=source_juans,
    )
    return TranslationBundle(
        id=bundle_id,
        path=Path(path),
        manifest={},
        source_textid=source_textid,
        summary=summary,
        juans={},
    )


def align_translation(
    *,
    textid: str,
    seq: int,
    source_juan: dict[str, Any],
    translation: TranslationBundle,
) -> TranslationAlignmentResponse:
    source_body = source_juan.get("body") if isinstance(source_juan, dict) else {}
    if not isinstance(source_body, dict):
        source_body = {}
    source_text = source_body.get("text") if isinstance(source_body.get("text"), str) else ""
    markers = source_body.get("markers") if isinstance(source_body.get("markers"), list) else []
    segs = _source_segments(source_text, markers)
    if not segs:
        return TranslationAlignmentResponse(
            textid=textid,
            juan_seq=seq,
            translation=translation.summary,
            status="no_alignment_markers",
            rows=[],
        )
    trans_juan = translation.juans.get(seq)
    by_corresp: dict[str, list[TranslationSegment]] = {}
    first_corresp_for_segment: dict[int, str] = {}
    if trans_juan is not None:
        for segment in trans_juan.segments:
            if not segment.corresp:
                continue
            first_corresp_for_segment[id(segment)] = segment.corresp[0]
            for corresp in segment.corresp:
                by_corresp.setdefault(corresp, []).append(segment)

    rows: list[TranslationAlignedRow] = []
    for seg in segs:
        parts = by_corresp.get(seg["corresp"], [])
        own_parts = [
            p for p in parts
            if first_corresp_for_segment.get(id(p), seg["corresp"]) == seg["corresp"]
        ]
        continued = bool(parts) and not own_parts
        rows.append(
            TranslationAlignedRow(
                corresp=seg["corresp"],
                source_marker_id=seg["id"],
                source_offset=seg["start"],
                source_end=seg["end"],
                source_text=source_text[seg["start"]:seg["end"]],
                translation_text="\n".join(p.text for p in own_parts),
                translation_refs=[r for p in own_parts for r in p.ref],
                resp=own_parts[0].resp if own_parts else None,
                continued=continued,
            )
        )
    return TranslationAlignmentResponse(
        textid=textid,
        juan_seq=seq,
        translation=translation.summary,
        status="ok",
        rows=rows,
    )


def _read_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    data = yaml.safe_load(match.group(1)) or {}
    if not isinstance(data, dict):
        data = {}
    return data, match.group(2)


def _read_translation_juan(path: Path) -> TranslationJuan:
    header, body = _read_frontmatter(path)
    markers = header.get("markers") if isinstance(header.get("markers"), list) else []
    spans = list(SPAN_RE.finditer(body))
    segments: list[TranslationSegment] = []
    for i, span in enumerate(spans):
        marker = markers[i] if i < len(markers) and isinstance(markers[i], dict) else {}
        corresp = marker.get("corresp")
        refs = marker.get("ref")
        resp = marker.get("resp")
        segments.append(
            TranslationSegment(
                text=_unescape_span_text(span.group(1)),
                ref=_string_list(refs) or _refs_from_attrs(span.group(2)),
                corresp=_string_list(corresp),
                resp=resp if isinstance(resp, str) else None,
            )
        )
    seq = header.get("juan_seq") if isinstance(header.get("juan_seq"), int) else 0
    label = str(header.get("juan_label") or path.stem.rsplit("_", 1)[-1])
    return TranslationJuan(seq=seq, label=label, path=path, segments=segments)


def _summary(
    bundle_id: str,
    manifest: dict[str, Any],
    source_textid: str,
    juan_count: int,
    segment_count: int,
    source_juans: list[int] | None = None,
) -> TranslationSummary:
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    resp = [
        TranslationResponsibility(
            role=item.get("role") if isinstance(item.get("role"), str) else None,
            name=item.get("name") if isinstance(item.get("name"), str) else None,
        )
        for item in (manifest.get("responsibility") or [])
        if isinstance(item, dict)
    ]
    return TranslationSummary(
        id=bundle_id,
        source_textid=source_textid,
        canonical_identifier=manifest.get("canonical_identifier"),
        source_canonical_identifier=source.get("canonical_identifier"),
        language=manifest.get("language"),
        title=manifest.get("title"),
        original_title=manifest.get("original_title"),
        responsibility=resp,
        date=manifest.get("date"),
        license=manifest.get("license"),
        juan_count=juan_count,
        segment_count=segment_count,
        source_juans=source_juans or [],
    )


def _source_textid(bundle_dir: Path, manifest: dict[str, Any]) -> str:
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    cid = source.get("canonical_identifier")
    if isinstance(cid, str):
        match = SOURCE_ID_RE.match(cid)
        if match:
            return match.group(1)
    # translations/<section>/<source>/<lang>/<bundle>
    try:
        return bundle_dir.parents[1].name
    except IndexError:
        return "_unknown"


def _source_segments(text: str, markers: list[Any]) -> list[dict[str, Any]]:
    seg_markers: list[tuple[int, str, str]] = []
    for marker in markers:
        if not isinstance(marker, dict) or marker.get("type") != "tls:seg":
            continue
        marker_id = marker.get("id")
        offset = marker.get("offset")
        if not isinstance(marker_id, str) or not isinstance(offset, int):
            continue
        seg_markers.append((offset, marker_id, _relative_marker_id(marker_id)))
    seg_markers.sort(key=lambda item: item[0])
    out: list[dict[str, Any]] = []
    for i, (start, marker_id, rel) in enumerate(seg_markers):
        end = seg_markers[i + 1][0] if i + 1 < len(seg_markers) else len(text)
        out.append({"start": start, "end": end, "id": marker_id, "corresp": rel})
    return out


def _relative_marker_id(marker_id: str) -> str:
    if "_tls_" in marker_id:
        return marker_id.rsplit("_tls_", 1)[1]
    return marker_id


def _matches_metadata_query(bundle: TranslationBundle, query: str) -> bool:
    hay = [
        bundle.id,
        bundle.source_textid,
        bundle.summary.language or "",
        bundle.summary.title or "",
        bundle.summary.original_title or "",
        " ".join(
            str(v)
            for item in bundle.manifest.get("responsibility") or []
            if isinstance(item, dict)
            for v in item.values()
        ),
    ]
    return query in "\n".join(hay).casefold()


def _matches_text_query(bundle: TranslationBundle, query: str) -> bool:
    for juan in bundle.juans.values():
        for seg in juan.segments:
            if query in seg.text.casefold():
                return True
    return False


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _refs_from_attrs(attrs: str) -> list[str]:
    return [part[1:] for part in attrs.split() if part.startswith("@") and len(part) > 1]


def _unescape_span_text(text: str) -> str:
    out: list[str] = []
    escaped = False
    for ch in text:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            out.append(ch)
    if escaped:
        out.append("\\")
    return "".join(out)


def get_segment_translations(
    corpus_root: Path,
    textid: str,
    seq: int,
    corresp: str,
    source_text: str,
    *,
    search_conn: sqlite3.Connection | None = None,
    catalog_conn: sqlite3.Connection | None = None,
) -> "SegmentTranslationsResponse":
    from .schemas import SegmentTranslationEntry, SegmentTranslationsResponse  # noqa: PLC0415
    if search_conn is not None and catalog_conn is not None:
        entries = _segment_translations_from_index(
            search_conn, catalog_conn, textid=textid, seq=seq, corresp=corresp,
        )
    else:
        entries = _segment_translations_from_fs(
            corpus_root, textid=textid, seq=seq, corresp=corresp,
        )
    return SegmentTranslationsResponse(
        corresp=corresp,
        source_text=source_text,
        entries=entries,
    )


def _segment_translations_from_index(
    search_conn: sqlite3.Connection,
    catalog_conn: sqlite3.Connection,
    *,
    textid: str,
    seq: int,
    corresp: str,
) -> "list":
    from .schemas import SegmentTranslationEntry  # noqa: PLC0415
    rows = search_conn.execute(
        "SELECT ts.translation_id, ts.text"
        " FROM translation_segment ts"
        " WHERE ts.corresp = ? AND ts.juan_seq = ?",
        [corresp, seq],
    ).fetchall()
    if not rows:
        return []
    placeholders = ",".join("?" * len(rows))
    ids = [r[0] for r in rows]
    text_by_id = {r[0]: r[1] for r in rows}
    cat_rows = catalog_conn.execute(
        f"SELECT id, source_textid, language, title, responsibility"
        f" FROM catalog_translation"
        f" WHERE id IN ({placeholders}) AND source_textid = ?",
        [*ids, textid],
    ).fetchall()
    entries = []
    for cat_id, _src, language, title, responsibility_raw in cat_rows:
        text = text_by_id.get(cat_id)
        if not text:
            continue
        try:
            responsibility = json.loads(responsibility_raw or "[]")
        except json.JSONDecodeError:
            responsibility = []
        translator = next(
            (item["name"] for item in responsibility
             if isinstance(item, dict) and item.get("name")),
            None,
        )
        entries.append(SegmentTranslationEntry(
            bundle_id=cat_id,
            title=title,
            language=language,
            translator=translator,
            text=text,
        ))
    return entries


def _segment_translations_from_fs(
    corpus_root: Path,
    *,
    textid: str,
    seq: int,
    corresp: str,
) -> "list":
    from .schemas import SegmentTranslationEntry  # noqa: PLC0415
    bundles = list_translation_bundles(corpus_root, source_textid=textid)
    entries = []
    for bundle_stub in bundles:
        try:
            bundle = load_translation_bundle(bundle_stub.path, include_juans=True)
        except Exception:
            continue
        trans_juan = bundle.juans.get(seq)
        if trans_juan is None:
            continue
        for seg in trans_juan.segments:
            if corresp in seg.corresp and seg.text:
                translator = next(
                    (r.name for r in bundle.summary.responsibility if r.name),
                    None,
                )
                entries.append(SegmentTranslationEntry(
                    bundle_id=bundle.id,
                    title=bundle.summary.title,
                    language=bundle.summary.language,
                    translator=translator,
                    text=seg.text,
                ))
                break
    return entries


def search_translation_segments(
    search_conn: sqlite3.Connection,
    catalog_conn: sqlite3.Connection,
    *,
    q: str,
    sort: str = "textid",
    lang: str | None = None,
    category: str | None = None,
    date_before: int | None = None,
    date_after: int | None = None,
    is_ai: bool | None = None,
    corpus_root: Path | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[TranslationSegmentHit], int, TranslationSearchFacets]:
    # 1. Filter bundle metadata from catalog (JOIN catalog_translation + catalog_bundle).
    cat_where: list[str] = []
    cat_params: list[Any] = []
    if lang:
        cat_where.append("ct.language = ?")
        cat_params.append(lang)
    if category:
        cat_where.append("(cb.section_code = ? OR cb.section_code LIKE ?)")
        cat_params.extend([category, f"{category}%"])
    if date_before is not None:
        cat_where.append("cb.index_date <= ?")
        cat_params.append(date_before)
    if date_after is not None:
        cat_where.append("cb.index_date >= ?")
        cat_params.append(date_after)
    cat_where_sql = f"WHERE {' AND '.join(cat_where)}" if cat_where else ""
    cat_rows = catalog_conn.execute(
        "SELECT ct.id, ct.source_textid, ct.language, ct.title, ct.responsibility, "
        "ct.date, cb.index_date, cb.section_code "
        "FROM catalog_translation ct "
        f"JOIN catalog_bundle cb ON cb.textid = ct.source_textid {cat_where_sql}",
        cat_params,
    ).fetchall()

    bundle_meta: dict[str, dict[str, Any]] = {}
    for row in cat_rows:
        bid, src, blang, title, resp_raw, bdate, idate, sec = row
        try:
            resp = json.loads(resp_raw or "[]")
        except json.JSONDecodeError:
            resp = []
        bundle_meta[bid] = {
            "source_textid": src,
            "language": blang,
            "title": title,
            "responsibility": resp,
            "date": bdate,
            "index_date": idate,
            "section_code": sec,
        }

    if is_ai is not None:
        bundle_meta = {
            k: v for k, v in bundle_meta.items()
            if _is_ai_translation(v.get("title"), v.get("responsibility")) == is_ai
        }

    if not bundle_meta:
        return [], 0, TranslationSearchFacets()

    # 2. Search segments restricted to filtered bundle IDs.
    q_norm = normalize_search_text(q) or q.casefold()
    like = f"%{q_norm}%"
    bundle_ids = list(bundle_meta)
    placeholders = ",".join("?" * len(bundle_ids))
    total = int(search_conn.execute(
        f"SELECT COUNT(*) FROM translation_segment "
        f"WHERE text_search LIKE ? AND translation_id IN ({placeholders})",
        [like, *bundle_ids],
    ).fetchone()[0])

    # 3. Compute facets from actual matching segments (counts per source section/language).
    agg_rows = search_conn.execute(
        f"SELECT translation_id, COUNT(*) "
        f"FROM translation_segment "
        f"WHERE text_search LIKE ? AND translation_id IN ({placeholders}) "
        f"GROUP BY translation_id",
        [like, *bundle_ids],
    ).fetchall()
    lang_counts: dict[str, int] = {}
    sec_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    dates: list[int] = []
    for tid, hit_count in agg_rows:
        meta = bundle_meta.get(tid, {})
        lv = meta.get("language") or ""
        lang_counts[lv] = lang_counts.get(lv, 0) + hit_count
        sv = meta.get("section_code") or ""
        sec_counts[sv] = sec_counts.get(sv, 0) + hit_count
        tv = "AI" if _is_ai_translation(meta.get("title"), meta.get("responsibility")) else "human"
        type_counts[tv] = type_counts.get(tv, 0) + hit_count
        if meta.get("index_date") is not None:
            dates.append(meta["index_date"])

    language_facet = [
        SearchFacetValue(value=k, count=v, selected=(k == (lang or "")))
        for k, v in sorted(lang_counts.items(), key=lambda x: -x[1])
        if k
    ]
    sec_labels: dict[str, str | None] = {}
    if sec_counts:
        sec_placeholders = ",".join("?" * len(sec_counts))
        for code, label in catalog_conn.execute(
            f"SELECT code, title_english FROM catalog_section WHERE code IN ({sec_placeholders})",
            list(sec_counts),
        ).fetchall():
            sec_labels[code] = label
    category_facet = [
        SearchFacetValue(value=k, label=sec_labels.get(k), count=v, selected=(k == (category or "")))
        for k, v in sorted(sec_counts.items(), key=lambda x: -x[1])
        if k
    ]
    date_facet = SearchDateFacets(
        min=min(dates) if dates else None,
        max=max(dates) if dates else None,
    )
    type_facet = [
        SearchFacetValue(value=k, count=v, selected=(
            (k == "AI" and is_ai is True) or (k == "human" and is_ai is False)
        ))
        for k, v in sorted(type_counts.items(), key=lambda x: -x[1])
    ]
    facets = TranslationSearchFacets(
        language=language_facet,
        category=category_facet,
        date=date_facet,
        type=type_facet,
    )

    # 4. Fetch the current page of matching segments.
    rows = search_conn.execute(
        f"SELECT translation_id, juan_seq, corresp, text "
        f"FROM translation_segment "
        f"WHERE text_search LIKE ? AND translation_id IN ({placeholders}) "
        f"ORDER BY translation_id, juan_seq "
        f"LIMIT ? OFFSET ?",
        [like, *bundle_ids, limit, offset],
    ).fetchall()

    # 5. Build hit list with metadata.
    hits: list[TranslationSegmentHit] = []
    for tid, jseq, corresp, text in rows:
        meta = bundle_meta.get(tid, {})
        resp_list = [
            TranslationResponsibility(
                role=item.get("role") if isinstance(item, dict) else None,
                name=item.get("name") if isinstance(item, dict) else None,
            )
            for item in (meta.get("responsibility") or [])
            if isinstance(item, dict)
        ]
        hits.append(TranslationSegmentHit(
            bundle_id=tid,
            source_textid=meta.get("source_textid", ""),
            juan_seq=jseq,
            corresp=corresp,
            text=text,
            language=meta.get("language"),
            title=meta.get("title"),
            responsibility=resp_list,
            date=meta.get("date"),
            is_ai=_is_ai_translation(meta.get("title"), meta.get("responsibility")),
        ))

    # 6. Apply date-based sort (textid sort is already done in SQL).
    if sort == "trans_date":
        hits.sort(key=lambda h: (h.date or "", h.bundle_id))
    elif sort == "source_date":
        hits.sort(
            key=lambda h: (-(bundle_meta.get(h.bundle_id, {}).get("index_date") or 0), h.bundle_id)
        )

    # 7. Optionally look up source segment text.
    if corpus_root is not None:
        from .selection import load_juan  # noqa: PLC0415
        source_cache: dict[tuple[str, int], dict[str, Any]] = {}
        for hit in hits:
            if not hit.corresp:
                continue
            key = (hit.source_textid, hit.juan_seq)
            if key not in source_cache:
                try:
                    _, juan = load_juan(corpus_root, hit.source_textid, hit.juan_seq)
                    body = juan.get("body") or {}
                    body_text = body.get("text") or ""
                    body_markers = body.get("markers") or []
                    source_cache[key] = {
                        seg["corresp"]: body_text[seg["start"]:seg["end"]]
                        for seg in _source_segments(body_text, body_markers)
                        if "corresp" in seg
                    }
                except Exception:
                    source_cache[key] = {}
            seg_texts = source_cache[key]
            # _source_segments uses relative corresp; match the hit's corresp
            rel = _relative_marker_id(hit.corresp)
            src_text = seg_texts.get(rel) or seg_texts.get(hit.corresp)
            if src_text:
                hits[hits.index(hit)] = TranslationSegmentHit(
                    **{**hit.model_dump(), "source_text": src_text}
                )

    return hits, total, facets
