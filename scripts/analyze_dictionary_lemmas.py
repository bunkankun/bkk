#!/usr/bin/env python3
"""Report dictionary lemma-repeat placeholders not covered by lemma voices.

This is a diagnostic script for large dictionary-like BKK bundles, especially
texts such as KR3k0059 where ``bkk voice add --source dictionary`` may miss
some lemma-repeat placeholders (``丨``).

By default the report lists body-text lines that contain one or more
placeholder offsets that:

* are not covered by an existing ``source: dictionary`` voice with lemma
  metadata, and
* are currently rendered as ``default`` voice.

The script also re-runs the current dictionary deriver in memory and includes
whether a missed offset would be derivable now. This helps separate stale
marker assets from heuristic misses.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOT = REPO_ROOT / "module"
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from bkk.marker_assets import effective_markers_for_bucket, load_marker_asset
from bkk.voice.derive_dictionary import (  # noqa: E402
    PLACEHOLDER,
    _head_segments,
    _line_offsets,
    _source_cue_positions,
    derive_dictionary_voice_markers,
)


_BUCKETS = ("front", "body", "back")
_JUAN_RE = re.compile(r"^(?P<text_id>.+?)_(?P<seq>\d{3})\.yaml$")
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@dataclass(frozen=True)
class Range:
    start: int
    end: int
    marker: dict[str, Any]

    def contains(self, offset: int) -> bool:
        return self.start <= offset < self.end


@dataclass(frozen=True)
class MissingOffset:
    offset: int
    voice: str
    reason: str
    head: str
    source_cue: str
    source_offset: str
    lemma_guess: str
    derivable_lemma: str
    lemma_repeat: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze dictionary lemma-repeat placeholders not covered by "
            "dictionary lemma voice markers."
        ),
    )
    parser.add_argument("bundle", type=Path, help="BKK bundle directory")
    parser.add_argument(
        "--bucket",
        choices=_BUCKETS,
        action="append",
        dest="buckets",
        help="bucket to inspect; repeatable (default: body)",
    )
    parser.add_argument(
        "--all-missing",
        action="store_true",
        help=(
            "include placeholder text not covered by lemma voices even when "
            "it is inside a non-default voice"
        ),
    )
    parser.add_argument(
        "--show-covered",
        action="store_true",
        help="also include rows fully covered by existing dictionary lemma voices",
    )
    parser.add_argument(
        "--full-line",
        action="store_true",
        help="write the full logical line instead of clipped context",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=48,
        help="characters of context around the first missed placeholder",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help=(
            "maximum report rows to write; stops scanning once reached "
            "(0 means no limit)"
        ),
    )
    parser.add_argument(
        "--seq",
        type=int,
        action="append",
        default=None,
        help="inspect only this juan sequence number; repeatable",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="TSV output path (default: stdout)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bundle_dir = args.bundle.expanduser().resolve()
    if not bundle_dir.is_dir():
        print(f"error: bundle directory not found: {bundle_dir}", file=sys.stderr)
        return 2

    text_id = bundle_dir.name
    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    if not manifest_path.exists():
        print(f"error: master manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    manifest = _yaml_load(manifest_path)
    if not isinstance(manifest, dict):
        print(f"error: manifest top level is not a mapping: {manifest_path}", file=sys.stderr)
        return 2

    buckets = tuple(args.buckets or ("body",))
    rows, counts = analyze_bundle(
        bundle_dir,
        manifest,
        text_id,
        buckets=buckets,
        default_only=not args.all_missing,
        show_covered=args.show_covered,
        full_line=args.full_line,
        context=args.context,
        limit=args.limit,
        seq_filter=set(args.seq) if args.seq else None,
    )

    fieldnames = [
        "seq",
        "bucket",
        "line_start",
        "line_end",
        "offsets",
        "placeholder_count",
        "voice",
        "reason",
        "head",
        "source_cue",
        "source_offset",
        "lemma_guess",
        "derivable_lemma",
        "lemma_repeat",
        "text",
    ]
    if args.out is None:
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)

    _print_summary(counts, rows, args.out)
    return 0


def analyze_bundle(
    bundle_dir: Path,
    manifest: dict[str, Any],
    text_id: str,
    *,
    buckets: tuple[str, ...],
    default_only: bool,
    show_covered: bool,
    full_line: bool,
    context: int,
    limit: int,
    seq_filter: set[int] | None,
) -> tuple[list[dict[str, str]], Counter[str]]:
    rows: list[dict[str, str]] = []
    counts: Counter[str] = Counter()

    for seq, juan_path in _master_juan_entries(bundle_dir, text_id):
        if seq_filter is not None and seq not in seq_filter:
            continue
        data = _yaml_load(juan_path)
        if not isinstance(data, dict):
            raise RuntimeError(f"{juan_path.name}: top-level YAML is not a mapping")
        marker_asset = load_marker_asset(bundle_dir, manifest, seq)

        for bucket_name in buckets:
            bucket = data.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            text = bucket.get("text") or ""
            if not isinstance(text, str) or PLACEHOLDER not in text:
                continue
            markers = effective_markers_for_bucket(data, bucket_name, marker_asset)
            bucket_rows, bucket_counts = analyze_bucket(
                seq,
                bucket_name,
                text,
                markers,
                default_only=default_only,
                show_covered=show_covered,
                full_line=full_line,
                context=context,
            )
            counts.update(bucket_counts)
            for row in bucket_rows:
                rows.append(row)
                if limit and len(rows) >= limit:
                    counts["scan_stopped_by_limit"] = 1
                    return rows, counts
    return rows, counts


def analyze_bucket(
    seq: int,
    bucket_name: str,
    text: str,
    markers: list[dict[str, Any]],
    *,
    default_only: bool,
    show_covered: bool,
    full_line: bool,
    context: int,
) -> tuple[list[dict[str, str]], Counter[str]]:
    line_starts = _line_offsets(len(text), markers)
    line_ranges = _line_ranges(len(text), line_starts)
    existing_dict_ranges = _dictionary_voice_ranges(markers)
    fresh_dict_ranges = _dictionary_voice_ranges(
        derive_dictionary_voice_markers(text, markers),
    )
    all_voice_ranges = _voice_ranges(markers)
    lemma_repeat = _lemma_repeat_by_offset(markers)
    head_segments = _head_segments(text, line_starts)

    missing_by_line: dict[tuple[int, int], list[MissingOffset]] = {}
    covered_by_line: dict[tuple[int, int], list[MissingOffset]] = {}
    counts: Counter[str] = Counter()

    for offset, ch in enumerate(text):
        if ch != PLACEHOLDER:
            continue
        counts["placeholders"] += 1
        existing = _first_containing(existing_dict_ranges, offset)
        voice = _voice_at_offset(offset, all_voice_ranges)
        fresh = _first_containing(fresh_dict_ranges, offset)
        if existing is not None:
            counts["covered_existing_dict"] += 1
            if show_covered:
                line_range = _containing_line(line_ranges, offset)
                covered_by_line.setdefault(line_range, []).append(
                    _offset_info(
                        text,
                        offset,
                        voice,
                        "covered-by-existing-dict",
                        head_segments,
                        fresh,
                        lemma_repeat,
                    ),
                )
            continue
        if default_only and voice != "default":
            counts[f"skipped_non_default:{voice}"] += 1
            continue

        info = _offset_info(
            text,
            offset,
            voice,
            _reason(text, offset, head_segments, fresh),
            head_segments,
            fresh,
            lemma_repeat,
        )
        counts[info.reason] += 1
        line_range = _containing_line(line_ranges, offset)
        missing_by_line.setdefault(line_range, []).append(info)

    rows = [
        _line_row(
            seq,
            bucket_name,
            text,
            line_range,
            offsets,
            full_line=full_line,
            context=context,
        )
        for line_range, offsets in sorted(missing_by_line.items())
    ]
    if show_covered:
        rows.extend(
            _line_row(
                seq,
                bucket_name,
                text,
                line_range,
                offsets,
                full_line=full_line,
                context=context,
            )
            for line_range, offsets in sorted(covered_by_line.items())
        )
        rows.sort(key=lambda r: (int(r["seq"]), r["bucket"], int(r["line_start"])))
    return rows, counts


def _line_row(
    seq: int,
    bucket_name: str,
    text: str,
    line_range: tuple[int, int],
    offsets: list[MissingOffset],
    *,
    full_line: bool,
    context: int,
) -> dict[str, str]:
    start, end = line_range
    first_offset = offsets[0].offset
    if full_line:
        snippet = text[start:end]
    else:
        left = max(start, first_offset - max(context, 0))
        right = min(end, first_offset + max(context, 0) + 1)
        prefix = "..." if left > start else ""
        suffix = "..." if right < end else ""
        snippet = f"{prefix}{text[left:right]}{suffix}"
    return {
        "seq": f"{seq:03d}",
        "bucket": bucket_name,
        "line_start": str(start),
        "line_end": str(end),
        "offsets": ",".join(str(o.offset) for o in offsets),
        "placeholder_count": str(len(offsets)),
        "voice": "|".join(sorted({o.voice for o in offsets})),
        "reason": "|".join(sorted({o.reason for o in offsets})),
        "head": "|".join(sorted({o.head for o in offsets if o.head})),
        "source_cue": "|".join(sorted({o.source_cue for o in offsets if o.source_cue})),
        "source_offset": "|".join(
            sorted({o.source_offset for o in offsets if o.source_offset}, key=int)
        ),
        "lemma_guess": "|".join(sorted({o.lemma_guess for o in offsets if o.lemma_guess})),
        "derivable_lemma": "|".join(
            sorted({o.derivable_lemma for o in offsets if o.derivable_lemma})
        ),
        "lemma_repeat": "|".join(sorted({o.lemma_repeat for o in offsets if o.lemma_repeat})),
        "text": snippet,
    }


def _offset_info(
    text: str,
    offset: int,
    voice: str,
    reason: str,
    head_segments: list[tuple[int, int, str]],
    fresh: Range | None,
    lemma_repeat: dict[int, str],
) -> MissingOffset:
    segment = _containing_head_segment(head_segments, offset)
    head = segment[2] if segment else ""
    source_pos, source_cue = _nearest_source_before(text, offset, segment)
    lemma_guess = _lemma_guess(text, source_pos) if source_pos is not None else ""
    derivable_lemma = ""
    if fresh is not None:
        lemma = fresh.marker.get("lemma")
        derivable_lemma = lemma if isinstance(lemma, str) else ""
    return MissingOffset(
        offset=offset,
        voice=voice,
        reason=reason,
        head=head,
        source_cue=source_cue or "",
        source_offset=str(source_pos) if source_pos is not None else "",
        lemma_guess=lemma_guess,
        derivable_lemma=derivable_lemma,
        lemma_repeat=lemma_repeat.get(offset, ""),
    )


def _reason(
    text: str,
    offset: int,
    head_segments: list[tuple[int, int, str]],
    fresh: Range | None,
) -> str:
    if fresh is not None:
        return "derivable-now-not-persisted"
    segment = _containing_head_segment(head_segments, offset)
    if segment is None:
        return "default:no-head-segment"
    source_pos, _source_cue = _nearest_source_before(text, offset, segment)
    if source_pos is None:
        return "default:no-source-cue-before-placeholder"
    head = segment[2]
    if source_pos <= 0 or text[source_pos - 1] != head:
        return "default:source-cue-not-preceded-by-head"
    return "default:no-derived-candidate-covers-placeholder"


def _voice_ranges(markers: Iterable[dict[str, Any]]) -> list[Range]:
    ranges: list[Range] = []
    for marker in markers:
        if marker.get("type") != "voice":
            continue
        start = marker.get("offset")
        length = marker.get("length")
        if not _valid_range(start, length):
            continue
        ranges.append(Range(start, start + length, marker))
    ranges.sort(key=lambda r: (r.start, -r.end))
    return ranges


def _dictionary_voice_ranges(markers: Iterable[dict[str, Any]]) -> list[Range]:
    return [
        rng for rng in _voice_ranges(markers)
        if rng.marker.get("source") == "dictionary"
        and isinstance(rng.marker.get("lemma"), str)
        and rng.marker.get("name") in {"def", "dict", "note"}
    ]


def _voice_at_offset(offset: int, ranges: list[Range]) -> str:
    best = -1
    lo = 0
    hi = len(ranges) - 1
    while lo <= hi:
        mid = (lo + hi) >> 1
        if ranges[mid].start <= offset:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    for index in range(best, -1, -1):
        rng = ranges[index]
        if rng.contains(offset):
            name = rng.marker.get("name")
            return name.strip() if isinstance(name, str) and name.strip() else "default"
    return "default"


def _first_containing(ranges: list[Range], offset: int) -> Range | None:
    for rng in ranges:
        if rng.contains(offset):
            return rng
    return None


def _line_ranges(text_len: int, line_starts: list[int]) -> list[tuple[int, int]]:
    starts = sorted(set(line_starts))
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    return [
        (start, starts[index + 1] if index + 1 < len(starts) else text_len)
        for index, start in enumerate(starts)
        if start < text_len
    ]


def _containing_line(
    line_ranges: list[tuple[int, int]],
    offset: int,
) -> tuple[int, int]:
    for start, end in line_ranges:
        if start <= offset < end:
            return start, end
    return (0, 0)


def _containing_head_segment(
    segments: list[tuple[int, int, str]],
    offset: int,
) -> tuple[int, int, str] | None:
    for segment in segments:
        start, end, _head = segment
        if start <= offset < end:
            return segment
    return None


def _nearest_source_before(
    text: str,
    offset: int,
    segment: tuple[int, int, str] | None,
) -> tuple[int | None, str | None]:
    if segment is None:
        return None, None
    start, end, _head = segment
    local_text = text[start:end]
    candidates = [
        (start + pos, cue)
        for pos, cue in _source_cue_positions(local_text)
        if start + pos <= offset
    ]
    if not candidates:
        return None, None
    return max(candidates, key=lambda item: item[0])


def _lemma_guess(text: str, source_pos: int) -> str:
    left = max(0, source_pos - 4)
    return text[left:source_pos]


def _lemma_repeat_by_offset(markers: Iterable[dict[str, Any]]) -> dict[int, str]:
    out: dict[int, str] = {}
    for marker in markers:
        if marker.get("type") != "substitution:lemma-repeat":
            continue
        offset = marker.get("offset")
        if not isinstance(offset, int) or isinstance(offset, bool):
            continue
        lemma = marker.get("lemma")
        replacement = marker.get("replacement")
        bits = []
        if isinstance(lemma, str) and lemma:
            bits.append(lemma)
        if isinstance(replacement, str) and replacement:
            bits.append(f"=>{replacement}")
        out[offset] = "".join(bits)
    return out


def _valid_range(start: Any, length: Any) -> bool:
    return (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(length, int)
        and not isinstance(length, bool)
        and start >= 0
        and length > 0
    )


def _master_juan_entries(bundle_dir: Path, text_id: str) -> list[tuple[int, Path]]:
    entries: list[tuple[int, Path]] = []
    for entry in sorted(bundle_dir.iterdir()):
        if not entry.is_file():
            continue
        match = _JUAN_RE.match(entry.name)
        if not match or match.group("text_id") != text_id:
            continue
        entries.append((int(match.group("seq")), entry))
    return entries


def _yaml_load(path: Path) -> Any:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_YAML_LOADER)


def _print_summary(
    counts: Counter[str],
    rows: list[dict[str, str]],
    out_path: Path | None,
) -> None:
    print(
        "summary: "
        f"{counts['placeholders']} placeholder(s), "
        f"{counts['covered_existing_dict']} covered by existing dictionary voice, "
        f"{len(rows)} row(s) written",
        file=sys.stderr,
    )
    if counts.get("scan_stopped_by_limit"):
        print("  partial scan: stopped after --limit rows", file=sys.stderr)
    reason_counts = {
        key: value
        for key, value in counts.items()
        if key not in {
            "placeholders",
            "covered_existing_dict",
            "scan_stopped_by_limit",
        }
    }
    for reason, count in sorted(reason_counts.items()):
        print(f"  {reason}: {count}", file=sys.stderr)
    if out_path is not None:
        print(f"wrote TSV: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
