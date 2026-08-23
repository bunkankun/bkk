"""Build citation tree fragment sidecars from heading voice markers."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Literal

from bkk.importer.hashing import ZERO_HASH, sha256_jcs
from bkk.short_refs import (
    compact_text_id,
    format_ctf_node_ref,
    format_short_ref,
    text_family_id,
)

from .derive_indent_headings import (
    HEADING_INDENT_VOICE_SOURCE,
    derive_voice_markers_from_indent_headings,
    heading_sequence_paths,
)

HeadingSource = Literal["auto", "voices", "derive", "manifest"]
_JUAN_STARTER_RE = re.compile(
    r"^(.{1,40}?卷[一二三四五六七八九十百千兩〇零]+)"
)


@dataclass(frozen=True)
class HeadingRecord:
    offset: int
    length: int
    level: int
    marker_id: str | None
    index: int
    path: tuple[int, ...] | None = None
    name: str = "head"
    label: str | None = None


def collect_indent_heading_voices(
    markers: list[dict[str, Any]],
    *,
    text_len: int,
) -> list[HeadingRecord]:
    """Return usable persisted indent-heading voice records."""
    out: list[HeadingRecord] = []
    note_spans: list[tuple[int, int]] = []
    for marker in markers:
        if (
            isinstance(marker, dict)
            and marker.get("type") == "voice"
            and marker.get("name") == "note"
            and isinstance(marker.get("offset"), int)
            and isinstance(marker.get("length"), int)
            and marker.get("length") > 0
        ):
            note_spans.append((marker["offset"], marker["offset"] + marker["length"]))
    note_starts = {note_start for note_start, _ in note_spans}
    for index, marker in enumerate(markers):
        if not isinstance(marker, dict):
            continue
        if (
            marker.get("type") != "voice"
            or marker.get("name") not in {"head", "label"}
            or marker.get("source") != HEADING_INDENT_VOICE_SOURCE
        ):
            continue
        offset = marker.get("offset")
        length = marker.get("length")
        level = marker.get("indent_depth")
        if (
            not isinstance(offset, int)
            or not isinstance(length, int)
            or not isinstance(level, int)
            or offset < 0
            or length <= 0
            or offset + length > text_len
        ):
            continue
        if (level == 1 and offset + length in note_starts) or any(
            note_start <= offset and offset + length <= note_end
            for note_start, note_end in note_spans
        ):
            continue
        marker_id = marker.get("id")
        path = _coerce_heading_path(marker.get("path"))
        name = marker.get("name")
        out.append(HeadingRecord(
            offset=offset,
            length=length,
            level=level,
            marker_id=marker_id if isinstance(marker_id, str) and marker_id else None,
            index=index,
            path=path,
            name=name if name in {"head", "label"} else "head",
        ))
    return sorted(out, key=lambda h: (h.offset, h.index))


def collect_manifest_headings(
    manifest: dict[str, Any],
    *,
    seq: int,
    bucket_name: str,
    text_len: int,
) -> list[HeadingRecord]:
    """Return heading records derived from manifest TOC entries.

    Older CBETA imports wrote ``mulu`` TOC spans as point spans.  For CTF
    purposes only the start offset, level, marker id, and label are trusted;
    the heading length is the label length, and the node span is inferred
    later from the next heading at the same or a shallower level.
    """
    toc = manifest.get("table_of_contents")
    if not isinstance(toc, list):
        return []
    out: list[HeadingRecord] = []
    for index, entry in enumerate(toc):
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "juan":
            continue
        ref = entry.get("ref")
        if not isinstance(ref, dict) or ref.get("seq") != seq:
            continue
        span = ref.get("span")
        if (
            not isinstance(span, list)
            or len(span) < 2
            or span[0] != bucket_name
            or not isinstance(span[1], int)
        ):
            continue
        label = entry.get("label")
        if not isinstance(label, str) or not label:
            continue
        level = _coerce_positive_int(entry.get("level"))
        if level is None:
            continue
        offset = span[1]
        length = len(label)
        if offset < 0 or length <= 0 or offset + length > text_len:
            continue
        marker_id = ref.get("marker_id")
        out.append(HeadingRecord(
            offset=offset,
            length=length,
            level=level,
            marker_id=marker_id if isinstance(marker_id, str) and marker_id else None,
            index=index,
            label=label,
        ))
    return sorted(out, key=lambda h: (h.offset, h.index))


def ctf_hash(asset: dict[str, Any]) -> str:
    data = copy.deepcopy(asset)
    data["hash"] = ZERO_HASH
    return sha256_jcs(data)


def build_ctf_asset(
    *,
    text_id: str,
    seq: int,
    bucket_name: str,
    text: str,
    markers: list[dict[str, Any]],
    manifest_hash: str | None,
    bucket_hash: str | None,
    manifest: dict[str, Any] | None = None,
    heading_source: HeadingSource = "auto",
    short_refs: bool = False,
) -> dict[str, Any]:
    """Build one CTF asset for a juan bucket."""
    if bucket_name != "body":
        raise ValueError("CTF v1 only supports the body bucket")
    if heading_source not in {"auto", "voices", "derive", "manifest"}:
        raise ValueError(f"unknown heading source: {heading_source!r}")

    existing = collect_indent_heading_voices(markers, text_len=len(text))
    mode = "voices"
    headings = existing
    if heading_source == "manifest":
        if manifest is None:
            raise ValueError("heading source 'manifest' requires a manifest")
        headings = collect_manifest_headings(
            manifest,
            seq=seq,
            bucket_name=bucket_name,
            text_len=len(text),
        )
        mode = "manifest"
    elif heading_source == "derive":
        derived = derive_voice_markers_from_indent_headings(len(text), markers, text)
        headings = collect_indent_heading_voices(derived, text_len=len(text))
        mode = "derive"
    elif heading_source == "auto":
        derived = derive_voice_markers_from_indent_headings(len(text), markers, text)
        derived_headings = collect_indent_heading_voices(derived, text_len=len(text))
        if len(derived_headings) > len(existing):
            headings = derived_headings
            mode = "derive"

    label = build_ctf_juan_label(
        text=text,
        headings=headings,
    )
    nodes = build_ctf_nodes(
        text_id=text_id,
        seq=seq,
        bucket_name=bucket_name,
        text=text,
        headings=headings,
        juan_label=label,
        short_refs=short_refs,
    )
    asset: dict[str, Any] = {
        "kind": "bkk.ctf/v1",
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/ctf/{seq}",
        "textid": text_id,
        "seq": seq,
        "bucket": bucket_name,
        "source": {
            "mode": mode,
            "voice_source": (
                "manifest" if mode == "manifest" else HEADING_INDENT_VOICE_SOURCE
            ),
            "manifest_hash": manifest_hash,
            "bucket_hash": bucket_hash,
        },
        "nodes": nodes,
        "hash": ZERO_HASH,
    }
    if label is not None:
        asset["label"] = label
    asset["hash"] = ctf_hash(asset)
    return asset


def build_ctf_juan_label(
    *,
    text: str,
    headings: list[HeadingRecord],
) -> str | None:
    unique = _unique_headings(headings)
    assignments = heading_sequence_paths([
        (heading.offset, heading.level) for heading in unique
    ])
    parts: list[str] = []
    if unique and not (
        assignments
        and assignments[0].path is None
        and unique[0].offset == 0
    ):
        starter = _text_prefix_juan_starter_label(text, unique[0].offset)
        if starter:
            parts.append(starter)
    elif not unique:
        starter = _text_prefix_juan_starter_label(text, len(text))
        if starter:
            parts.append(starter)

    for assignment, heading in zip(assignments, unique):
        if assignment.path is not None:
            continue
        label = _heading_label(text, heading)
        if label and label not in parts:
            parts.append(label)
    return "\u3000".join(parts) if parts else None


def build_ctf_nodes(
    *,
    text_id: str,
    seq: int,
    bucket_name: str,
    text: str,
    headings: list[HeadingRecord],
    juan_label: str | None = None,
    short_refs: bool = False,
) -> list[dict[str, Any]]:
    unique = _unique_headings(headings)

    root_id = compact_text_id(text_id) if short_refs else text_id
    juan_id = f"{root_id}/{seq}"
    nodes: list[dict[str, Any]] = [{
        "id": juan_id,
        "label": juan_label or juan_id,
        "level": 0,
        "parent_id": root_id,
    }]
    assignments = heading_sequence_paths([
        (heading.offset, heading.level) for heading in unique
    ])
    node_ids_by_path: dict[tuple[int, ...], str] = {}
    citation_indexes: list[int] = [
        index for index, assignment in enumerate(assignments)
        if assignment.path is not None
    ]
    for index, heading in enumerate(unique):
        assignment = assignments[index]
        sequence_path = assignment.path
        if sequence_path is None:
            node: dict[str, Any] = {
                "id": format_short_ref(
                    text_id,
                    seq,
                    offset=heading.offset,
                    length=heading.length,
                    bucket=bucket_name,
                    compact=short_refs,
                ),
                "label": _heading_label(text, heading),
                "level": 0,
                "parent_id": juan_id,
            }
            if heading.marker_id is not None:
                node["marker_id"] = heading.marker_id
            nodes.append(node)
            continue
        parent_path = sequence_path[:-1]
        if not parent_path:
            parent_id = juan_id
        else:
            parent_id = node_ids_by_path.get(parent_path)
            if parent_id is None:
                parent_id = _format_ctf_path_ref(
                    text_id, seq, parent_path, compact=short_refs,
                )
        node_id = format_ctf_node_ref(
            text_id,
            seq,
            sequence_path,
            offset=heading.offset,
            length=heading.length,
            bucket=bucket_name,
            compact=short_refs,
        )

        span_end = len(text)
        for later_index in citation_indexes:
            if later_index <= index:
                continue
            later_assignment = assignments[later_index]
            later = unique[later_index]
            if later_assignment.level <= assignment.level:
                span_end = later.offset
                break
        span_length = max(0, span_end - heading.offset)
        node: dict[str, Any] = {
            "id": node_id,
            "label": _heading_label(text, heading),
            "level": assignment.level,
            "parent_id": parent_id,
        }
        if heading.marker_id is not None:
            node["marker_id"] = heading.marker_id
        node["span_ref"] = format_short_ref(
            text_id,
            seq,
            offset=heading.offset,
            length=span_length,
            bucket=bucket_name,
            compact=short_refs,
        )
        nodes.append(node)
        node_ids_by_path[sequence_path] = node_id
    return nodes


def ctf_tsv_text(
    *,
    text_id: str,
    text_label: str | None,
    nodes: list[dict[str, Any]],
    juan_labels: dict[int, str] | None = None,
    short_refs: bool = False,
) -> str:
    root_id = compact_text_id(text_id) if short_refs else text_id
    root_parent_id = text_family_id(text_id, compact=short_refs)
    root_label = text_label or text_id
    rows = [("id", "parent_id", "label"), (root_id, root_parent_id, root_label)]
    nodes_by_seq: dict[int, list[dict[str, Any]]] = {}
    juan_nodes: dict[int, dict[str, Any]] = {}
    for node in nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str):
            continue
        seq = _ctf_tsv_juan_seq(node_id, root_id)
        if seq is not None:
            juan_nodes[seq] = node
            continue
        seq = _ctf_tsv_node_seq(node_id, root_id)
        if seq is None:
            continue
        nodes_by_seq.setdefault(seq, []).append(node)

    labels = juan_labels or {}
    for seq in sorted(set(nodes_by_seq) | set(labels) | set(juan_nodes)):
        juan_id = f"{root_id}/{seq}"
        juan_node = juan_nodes.get(seq)
        juan_parent = root_id
        juan_label = labels.get(seq) or juan_id
        if juan_node is not None:
            parent_id = juan_node.get("parent_id")
            label = juan_node.get("label")
            if isinstance(parent_id, str):
                juan_parent = parent_id
            if isinstance(label, str) and label:
                juan_label = label
        rows.append((juan_id, juan_parent, juan_label))
        for node in nodes_by_seq.get(seq, []):
            node_id = node.get("id")
            parent_id = node.get("parent_id")
            label = node.get("label")
            if not isinstance(node_id, str) or not isinstance(parent_id, str):
                continue
            if parent_id == root_id:
                parent_id = juan_id
            rows.append((node_id, parent_id, label if isinstance(label, str) else ""))
    return "".join(
        "\t".join(_tsv_cell(cell) for cell in row) + "\n"
        for row in rows
    )


def _unique_headings(headings: list[HeadingRecord]) -> list[HeadingRecord]:
    sorted_headings = sorted(headings, key=lambda h: (h.offset, h.index))
    unique: list[HeadingRecord] = []
    seen_spans: set[tuple[int, int]] = set()
    for heading in sorted_headings:
        span = (heading.offset, heading.length)
        if span in seen_spans:
            continue
        seen_spans.add(span)
        unique.append(heading)
    return unique


def _text_prefix_juan_starter_label(text: str, first_heading_offset: int) -> str | None:
    limit = max(0, min(first_heading_offset, 64))
    match = _JUAN_STARTER_RE.match(text[:limit])
    return match.group(1) if match is not None else None


def _ctf_tsv_node_seq(node_id: str, root_id: str) -> int | None:
    prefix = f"{root_id}/"
    if not node_id.startswith(prefix):
        return None
    rest = node_id[len(prefix):]
    juan, sep, _tail = rest.partition("/")
    if not sep or not juan.isdigit():
        return None
    return int(juan)


def _ctf_tsv_juan_seq(node_id: str, root_id: str) -> int | None:
    prefix = f"{root_id}/"
    if not node_id.startswith(prefix):
        return None
    rest = node_id[len(prefix):]
    return int(rest) if rest.isdigit() else None


def _tsv_cell(value: str) -> str:
    return value.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _coerce_heading_path(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, list):
        return None
    path: list[int] = []
    for part in value:
        if not isinstance(part, int) or part <= 0:
            return None
        path.append(part)
    return tuple(path) if path else None


def _coerce_positive_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        coerced = int(value)
        return coerced if coerced > 0 else None
    return None


def _heading_label(text: str, heading: HeadingRecord) -> str:
    if heading.label is not None:
        return heading.label
    return text[heading.offset:heading.offset + heading.length]


def _format_ctf_path_ref(
    text_id: str,
    seq: int,
    path: tuple[int, ...],
    *,
    compact: bool,
) -> str:
    ref_text_id = compact_text_id(text_id) if compact else text_id
    return f"{ref_text_id}/{seq}/{'/'.join(str(part) for part in path)}"
