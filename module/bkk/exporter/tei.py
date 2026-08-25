"""Fragment-level TEI exporter driven by CTF references."""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from lxml import etree

from bkk.index.merge import find_bundle
from bkk.marker_assets import hydrate_juan_markers, load_marker_asset
from bkk.rendering.punctuation import (
    PAGE_BREAK_RENDER_TOKEN,
    punctuation_render_rank,
)
from bkk.short_refs import compact_text_id, normalize_text_id

from .recipe import Recipe, RecipeError
from .xml_tree import TEI_NS, XML_NS

BKK_NS = "http://bunkankun.org/ns/1.0"
_TEI_NSMAP = {None: TEI_NS, "bkk": BKK_NS}
_BUCKETS = {"front", "body", "back"}
_NOTE_VOICES = {"commentary", "note"}
_REF_RE = re.compile(
    r"^(?P<text>(?:KR)?[0-9][a-z]{1,2}[0-9]{1,4})"
    r"/(?P<juan>[0-9]+)(?P<tail>(?:/.*)?)$"
)
_SPAN_RE = re.compile(
    r"(?:/(?P<bucket>front|body|back))?@(?P<offset>[0-9]+)\+(?P<length>[0-9]+)$"
)
_XML_ID_SAFE_RE = re.compile(r"[A-Za-z0-9.-]")


@dataclass(frozen=True)
class FragmentRef:
    original: str
    textid: str
    juan: int
    bucket: str
    offset: int | None
    length: int | None
    id_prefix: str


@dataclass(frozen=True)
class ResolvedFragment:
    original: str
    textid: str
    juan: int
    bucket: str
    offset: int
    length: int
    id_prefix: str


@dataclass(frozen=True)
class _TextSeg:
    start: int
    end: int
    xml_id: str


@dataclass(frozen=True)
class _RenderUnit:
    ch: str
    index: float
    kind: str
    marker: dict[str, Any]


class _IdFactory:
    def __init__(self) -> None:
        self._used: dict[str, int] = {}

    def make(self, prefix: str, offset: int) -> str:
        base = f"bkk_{_escape_xml_id_part(prefix)}_o{offset}"
        count = self._used.get(base, 0) + 1
        self._used[base] = count
        if count == 1:
            return base
        return f"{base}.{count}"


def export_tei_from_recipe(
    recipe: Recipe, *, corpus_root: Path, ctf_root: Path | None = None,
) -> list[Path]:
    """Export CTF-selected fragments as TEI-flavored XML div fragments."""

    if recipe.ctf is None:
        raise RecipeError("format tei requires at least one --ctf value")
    fragments = [
        _resolve_fragment(value, ctf_root=ctf_root)
        for value in recipe.ctf
    ]
    if not fragments:
        raise RecipeError("format tei requires at least one --ctf value")

    id_factory = _IdFactory()
    divs = [
        _render_fragment(fragment, corpus_root=corpus_root, id_factory=id_factory)
        for fragment in fragments
    ]
    if len(divs) == 1:
        root = divs[0]
    else:
        root = etree.Element(_q("div"), nsmap=_TEI_NSMAP)
        for div in divs:
            root.append(div)

    recipe.output_dir.mkdir(parents=True, exist_ok=True)
    textids = {fragment.textid for fragment in fragments}
    filename = (
        f"{next(iter(textids))}.tei.xml"
        if len(textids) == 1 else "fragments.tei.xml"
    )
    out_path = recipe.output_dir / filename
    out_path.write_bytes(etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    ))
    return [Path(filename)]


def default_ctf_root(rc: dict) -> Path | None:
    env = os.environ.get("BKK_CTF_ROOT")
    if env:
        return Path(env).resolve()
    value = (rc.get("global") or {}).get("ctf_root")
    if value is None:
        return None
    return Path(value).resolve()


def _q(local: str, ns: str = TEI_NS) -> str:
    return f"{{{ns}}}{local}"


def _resolve_fragment(value: str, *, ctf_root: Path | None) -> ResolvedFragment:
    parsed = _parse_fragment_ref(value)
    ctf = _lookup_ctf_span(parsed, ctf_root)
    if ctf is not None:
        parsed = ctf
    if parsed.offset is None or parsed.length is None:
        return ResolvedFragment(
            original=parsed.original,
            textid=parsed.textid,
            juan=parsed.juan,
            bucket=parsed.bucket,
            offset=0,
            length=-1,
            id_prefix=parsed.id_prefix,
        )
    return ResolvedFragment(
        original=parsed.original,
        textid=parsed.textid,
        juan=parsed.juan,
        bucket=parsed.bucket,
        offset=parsed.offset,
        length=parsed.length,
        id_prefix=parsed.id_prefix,
    )


def _parse_fragment_ref(value: str, *, original: str | None = None) -> FragmentRef:
    ref = value.strip()
    match = _REF_RE.fullmatch(ref)
    if match is None:
        raise RecipeError(f"invalid CTF ref: {value!r}")
    try:
        textid = normalize_text_id(match.group("text"))
    except ValueError as exc:
        raise RecipeError(f"invalid CTF ref text id: {value!r}") from exc
    juan = int(match.group("juan"))
    tail = match.group("tail") or ""
    span = _SPAN_RE.search(tail)
    bucket = "body"
    offset: int | None = None
    length: int | None = None
    if span is not None:
        bucket = span.group("bucket") or "body"
        offset = int(span.group("offset"))
        length = int(span.group("length"))
    elif tail:
        tail_bucket = tail.strip("/")
        if tail_bucket in _BUCKETS:
            bucket = tail_bucket
        elif "@" in tail:
            raise RecipeError(f"invalid CTF span in ref: {value!r}")
    id_prefix = ref.split("@", 1)[0]
    return FragmentRef(
        original=original or value,
        textid=textid,
        juan=juan,
        bucket=bucket,
        offset=offset,
        length=length,
        id_prefix=id_prefix,
    )


def _lookup_ctf_span(
    ref: FragmentRef, ctf_root: Path | None,
) -> FragmentRef | None:
    if ctf_root is None:
        return None
    section = _section_for_textid(ref.textid)
    if section is None:
        return None
    section_dir = ctf_root / section
    if not section_dir.is_dir():
        return None

    ids = _ctf_id_candidates(ref)
    tsv_path = section_dir / f"{ref.textid}.ctf.tsv"
    if tsv_path.is_file():
        match = _lookup_ctf_tsv(tsv_path, ids, ref)
        if match is not None:
            return match

    for path in sorted(section_dir.glob(f"{ref.textid}_*.ctf.yaml")):
        match = _lookup_ctf_yaml(path, ids, ref)
        if match is not None:
            return match
    return None


def _ctf_id_candidates(ref: FragmentRef) -> set[str]:
    raw = ref.original.strip()
    text_match = _REF_RE.fullmatch(raw)
    candidates = {raw}
    if text_match is not None:
        tail = text_match.group("tail") or ""
        candidates.add(f"{ref.textid}/{ref.juan}{tail}")
        candidates.add(f"{compact_text_id(ref.textid)}/{ref.juan}{tail}")
    return candidates


def _lookup_ctf_yaml(
    path: Path, ids: set[str], fallback: FragmentRef,
) -> FragmentRef | None:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return None
    for node in data.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or node_id not in ids:
            continue
        span_ref = node.get("span_ref")
        if isinstance(span_ref, str) and span_ref:
            return _parse_fragment_ref(span_ref, original=fallback.original)
        return _parse_fragment_ref(node_id, original=fallback.original)
    return None


def _lookup_ctf_tsv(
    path: Path, ids: set[str], fallback: FragmentRef,
) -> FragmentRef | None:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            node_id = row.get("id") or ""
            if node_id not in ids:
                continue
            parsed = _parse_fragment_ref(node_id, original=fallback.original)
            end_raw = row.get("end") or ""
            if parsed.offset is not None and end_raw.isdigit():
                end = int(end_raw)
                if end >= parsed.offset:
                    parsed = FragmentRef(
                        original=parsed.original,
                        textid=parsed.textid,
                        juan=parsed.juan,
                        bucket=parsed.bucket,
                        offset=parsed.offset,
                        length=end - parsed.offset,
                        id_prefix=parsed.id_prefix,
                    )
            return parsed
    return None


def _render_fragment(
    fragment: ResolvedFragment, *, corpus_root: Path, id_factory: _IdFactory,
) -> etree._Element:
    bundle_dir = find_bundle(corpus_root, fragment.textid)
    if bundle_dir is None:
        raise RecipeError(f"bundle not found for CTF ref {fragment.original!r}: {fragment.textid}")
    manifest = _load_manifest(bundle_dir, fragment.textid)
    juan = _load_juan(bundle_dir, manifest, fragment.textid, fragment.juan)
    bucket = juan.get(fragment.bucket) or {}
    if not isinstance(bucket, dict):
        raise RecipeError(
            f"bucket {fragment.bucket!r} not found for CTF ref {fragment.original!r}"
        )
    text = bucket.get("text") or ""
    if not isinstance(text, str):
        text = ""
    offset = fragment.offset
    length = len(text) if fragment.length < 0 else fragment.length
    if offset < 0 or length < 0 or offset + length > len(text):
        raise RecipeError(
            f"CTF ref {fragment.original!r} selects offset {offset}+{length}, "
            f"but {fragment.textid}/{fragment.juan}/{fragment.bucket} has length {len(text)}"
        )
    end = offset + length
    markers = _absolute_markers(bucket.get("markers") or [])

    div = etree.Element(_q("div"), nsmap=_TEI_NSMAP)
    div.set(_q("ref", BKK_NS), fragment.original)

    note_spans = _note_voice_spans(markers, offset, end)
    normal_intervals = _subtract_intervals(offset, end, [
        (span["start"], span["end"]) for span in note_spans
    ])
    point_markers = [
        marker for marker in markers
        if marker.get("type") in {"punctuation", "line-break", "page-break"}
        and isinstance(marker.get("offset"), int)
        and offset <= marker["offset"] <= end
    ]

    normal_segments: list[_TextSeg] = []
    for start, stop in normal_intervals:
        normal_segments.extend(_segment_defs(
            start,
            stop,
            point_markers,
            fragment.id_prefix,
            id_factory,
            fragment_end=end,
        ))
    voice_targets = _voice_target_segments(markers, normal_segments)

    events: list[tuple[int, int, str, Any]] = []
    for interval in normal_intervals:
        events.append((interval[0], 0, "normal", interval))
    for span in note_spans:
        events.append((span["start"], 1, "note", span))

    segs_by_interval = {
        (seg.start, seg.end): seg for seg in normal_segments
    }
    for _start, _rank, kind, payload in sorted(events, key=lambda e: (e[0], e[1])):
        if kind == "normal":
            start, stop = payload
            _append_interval(
                div,
                text,
                start,
                stop,
                point_markers,
                fragment.id_prefix,
                id_factory,
                precomputed=segs_by_interval,
                fragment_end=end,
            )
            continue
        _append_note(
            div,
            text,
            payload,
            point_markers,
            fragment.id_prefix,
            id_factory,
            voice_targets,
            fragment_end=end,
        )
    return div


def _absolute_markers(markers: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, marker in enumerate(markers):
        if not isinstance(marker, dict):
            continue
        item = dict(marker)
        item["_index"] = index
        out.append(item)
    return out


def _note_voice_spans(
    markers: list[dict[str, Any]], start: int, end: int,
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for marker in markers:
        if marker.get("type") != "voice" or marker.get("name") not in _NOTE_VOICES:
            continue
        offset = marker.get("offset")
        length = marker.get("length")
        if not isinstance(offset, int) or not isinstance(length, int) or length <= 0:
            continue
        span_start = max(start, offset)
        span_end = min(end, offset + length)
        if span_start >= span_end:
            continue
        spans.append({
            "start": span_start,
            "end": span_end,
            "marker": marker,
        })
    spans.sort(key=lambda span: (span["start"], span["marker"].get("_index", 0)))
    return spans


def _subtract_intervals(
    start: int, end: int, cuts: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    intervals: list[tuple[int, int]] = []
    cursor = start
    for cut_start, cut_end in sorted(cuts):
        cut_start = max(start, min(end, cut_start))
        cut_end = max(cut_start, min(end, cut_end))
        if cursor < cut_start:
            intervals.append((cursor, cut_start))
        cursor = max(cursor, cut_end)
    if cursor < end:
        intervals.append((cursor, end))
    return intervals


def _segment_defs(
    start: int,
    end: int,
    point_markers: list[dict[str, Any]],
    id_prefix: str,
    id_factory: _IdFactory,
    *,
    fragment_end: int,
) -> list[_TextSeg]:
    out: list[_TextSeg] = []
    cursor = start
    for offset in sorted({
        marker["offset"] for marker in point_markers
        if _offset_in_interval(marker["offset"], start, end, fragment_end)
    }):
        if cursor < offset:
            out.append(_TextSeg(cursor, offset, id_factory.make(id_prefix, cursor)))
        cursor = offset
    if cursor < end:
        out.append(_TextSeg(cursor, end, id_factory.make(id_prefix, cursor)))
    return out


def _voice_target_segments(
    markers: list[dict[str, Any]], segments: list[_TextSeg],
) -> dict[str, str]:
    out: dict[str, str] = {}
    for marker in markers:
        if marker.get("type") != "voice" or marker.get("name") in _NOTE_VOICES:
            continue
        marker_id = marker.get("id")
        offset = marker.get("offset")
        if not isinstance(marker_id, str) or not marker_id or not isinstance(offset, int):
            continue
        target = next(
            (
                seg for seg in segments
                if seg.start <= offset < seg.end
            ),
            None,
        )
        if target is None:
            target = next((seg for seg in segments if offset <= seg.start), None)
        if target is not None:
            out[marker_id] = target.xml_id
    return out


def _append_note(
    parent: etree._Element,
    text: str,
    span: dict[str, Any],
    point_markers: list[dict[str, Any]],
    id_prefix: str,
    id_factory: _IdFactory,
    voice_targets: dict[str, str],
    *,
    fragment_end: int,
) -> None:
    marker = span["marker"]
    note = etree.SubElement(parent, _q("note"))
    name = marker.get("name")
    if isinstance(name, str) and name:
        note.set("type", name)
    responds_to = marker.get("responds-to")
    if isinstance(responds_to, str) and responds_to in voice_targets:
        note.set("corresp", f"#{voice_targets[responds_to]}")
    _append_interval(
        note,
        text,
        span["start"],
        span["end"],
        point_markers,
        id_prefix,
        id_factory,
        fragment_end=fragment_end,
    )


def _append_interval(
    parent: etree._Element,
    text: str,
    start: int,
    end: int,
    point_markers: list[dict[str, Any]],
    id_prefix: str,
    id_factory: _IdFactory,
    *,
    precomputed: dict[tuple[int, int], _TextSeg] | None = None,
    fragment_end: int,
) -> None:
    cursor = start
    offsets = sorted({
        marker["offset"] for marker in point_markers
        if _offset_in_interval(marker["offset"], start, end, fragment_end)
    })
    for offset in offsets:
        if cursor < offset:
            _append_seg(
                parent,
                text,
                cursor,
                offset,
                id_prefix,
                id_factory,
                precomputed=precomputed,
            )
        for unit in _ordered_units_at(point_markers, offset):
            _append_unit(parent, unit, offset)
        cursor = offset
    if cursor < end:
        _append_seg(
            parent,
            text,
            cursor,
            end,
            id_prefix,
            id_factory,
            precomputed=precomputed,
        )


def _append_seg(
    parent: etree._Element,
    text: str,
    start: int,
    end: int,
    id_prefix: str,
    id_factory: _IdFactory,
    *,
    precomputed: dict[tuple[int, int], _TextSeg] | None,
) -> None:
    seg = etree.SubElement(parent, _q("seg"))
    known = precomputed.get((start, end)) if precomputed is not None else None
    xml_id = known.xml_id if known is not None else id_factory.make(id_prefix, start)
    seg.set(_q("id", XML_NS), xml_id)
    seg.set(_q("offset", BKK_NS), str(start))
    seg.text = text[start:end]


def _ordered_units_at(
    markers: list[dict[str, Any]], offset: int,
) -> list[_RenderUnit]:
    units: list[_RenderUnit] = []
    for marker in markers:
        if marker.get("offset") != offset:
            continue
        marker_index = float(marker.get("_index", 0))
        marker_type = marker.get("type")
        if marker_type == "punctuation":
            content = marker.get("content")
            if not isinstance(content, str) or not content:
                continue
            chars = list(content)
            denominator = max(1, len(chars))
            for content_index, ch in enumerate(chars):
                units.append(_RenderUnit(
                    ch=ch,
                    index=marker_index + content_index / denominator,
                    kind="punctuation",
                    marker=marker,
                ))
        elif marker_type == "line-break":
            units.append(_RenderUnit(
                ch="\n", index=marker_index, kind="line-break", marker=marker,
            ))
        elif marker_type == "page-break":
            units.append(_RenderUnit(
                ch=PAGE_BREAK_RENDER_TOKEN,
                index=marker_index,
                kind="page-break",
                marker=marker,
            ))
    return _sort_units(units)


def _sort_units(units: list[_RenderUnit]) -> list[_RenderUnit]:
    ordered = sorted(units, key=lambda unit: unit.index)
    result: list[_RenderUnit] = []
    start = 0
    while start < len(ordered):
        first_rank = punctuation_render_rank(ordered[start].ch)
        if first_rank is None:
            result.append(ordered[start])
            start += 1
            continue
        end = start + 1
        while end < len(ordered) and punctuation_render_rank(ordered[end].ch) is not None:
            end += 1
        result.extend(
            sorted(
                ordered[start:end],
                key=lambda unit: (
                    punctuation_render_rank(unit.ch) or 0,
                    unit.index,
                ),
            )
        )
        start = end
    return result


def _append_unit(parent: etree._Element, unit: _RenderUnit, offset: int) -> None:
    if unit.kind == "punctuation":
        el = etree.SubElement(parent, _q("c"))
        el.set("n", unit.ch)
    elif unit.kind == "line-break":
        el = etree.SubElement(parent, _q("lb"))
    elif unit.kind == "page-break":
        el = etree.SubElement(parent, _q("pb"))
    else:
        return
    el.set(_q("offset", BKK_NS), str(offset))


def _offset_in_interval(offset: int, start: int, end: int, fragment_end: int) -> bool:
    if start <= offset < end:
        return True
    return offset == end == fragment_end


def _escape_xml_id_part(value: str) -> str:
    out: list[str] = []
    for ch in value:
        if _XML_ID_SAFE_RE.fullmatch(ch):
            out.append(ch)
        else:
            out.append(f"_x{ord(ch):04X}_")
    escaped = "".join(out)
    return escaped or "ref"


def _section_for_textid(textid: str) -> str | None:
    match = re.fullmatch(r"KR(?P<section>[0-9][a-z]{1,2})[0-9]{3,4}", textid)
    return f"KR{match.group('section')}" if match is not None else None


def _load_manifest(bundle_dir: Path, textid: str) -> dict[str, Any]:
    manifest_path = bundle_dir / f"{textid}.manifest.yaml"
    if not manifest_path.exists():
        raise RecipeError(f"manifest not found: {manifest_path}")
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RecipeError(f"manifest must be a mapping: {manifest_path}")
    return data


def _load_juan(
    bundle_dir: Path, manifest: dict[str, Any], textid: str, seq: int,
) -> dict[str, Any]:
    parts = (manifest.get("assets") or {}).get("parts") or []
    entry = next(
        (part for part in parts if isinstance(part, dict) and part.get("seq") == seq),
        None,
    )
    if entry is None:
        raise RecipeError(f"juan {seq} not found for {textid}")
    filename = entry.get("filename")
    if not isinstance(filename, str):
        raise RecipeError(f"juan {seq} for {textid} has no filename")
    path = bundle_dir / filename
    if not path.exists():
        raise RecipeError(f"juan file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise RecipeError(f"juan file must be a mapping: {path}")
    return hydrate_juan_markers(
        data,
        load_marker_asset(bundle_dir, manifest, seq),
    )
