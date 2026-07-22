"""Build a ``.bkkx`` index file from a BKK bundle directory."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import yaml

from bkk.marker_assets import hydrate_juan_markers, load_marker_asset

from .schema import SCHEMA_VERSION, TABLES_DDL, create_heavy_indices
from .witness import apply_witness

log = logging.getLogger("bkk.index")
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _yaml_load_text(text: str):
    return yaml.load(text, Loader=_YAML_LOADER)


@dataclass(frozen=True)
class _WitnessRows:
    label: str
    text: str
    segments: bytes


@dataclass(frozen=True)
class _BucketRows:
    kind: str
    text: str
    variants: list[tuple[int, int, str, str, str]]
    voices: list[tuple[int, int, str, str, str | None]]
    witnesses: list[_WitnessRows]


@dataclass(frozen=True)
class _PartRows:
    seq: int
    hash: str | None
    buckets: list[_BucketRows]


def build_index(
    bundle_dir: Path | str,
    out_path: Path | str | None = None,
    *,
    jobs: int = 1,
) -> Path:
    """Build ``<textid>.bkkx`` from ``bundle_dir``.

    The index is written next to the manifest by default; pass ``out_path`` to
    override. ``jobs`` controls how many worker processes parse juan files and
    derive witness rows before the main process writes SQLite rows. Any
    existing file at the destination is overwritten.
    """
    if jobs < 1:
        raise ValueError("jobs must be >= 1")
    bundle_dir = Path(bundle_dir)
    textid = bundle_dir.name
    if out_path is None:
        out_path = bundle_dir / f"{textid}.bkkx"
    else:
        out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()

    manifest_path = bundle_dir / f"{textid}.manifest.yaml"
    manifest = _yaml_load_text(manifest_path.read_text(encoding="utf-8"))
    editions = [e["short"] for e in (manifest.get("editions") or [])]

    conn = sqlite3.connect(str(out_path))
    try:
        conn.executescript(TABLES_DDL)
        cur = conn.cursor()
        cur.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", str(SCHEMA_VERSION)),
                ("textid", textid),
                ("editions", json.dumps(editions, ensure_ascii=False)),
            ],
        )

        for entry in manifest.get("table_of_contents", []) or []:
            ref = entry["ref"]
            span = ref.get("span")
            if not span:
                continue
            if not (isinstance(span, (list, tuple)) and len(span) == 3):
                log.warning(
                    "%s: skipping TOC entry %r with malformed span %r "
                    "(expected [bucket, start, end])",
                    textid, ref.get("marker_id"), span,
                )
                continue
            bucket, start, end = span
            cur.execute(
                "INSERT INTO toc(textid, juan_seq, bucket, span_start, span_end, "
                "label, marker_id) VALUES (?,?,?,?,?,?,?)",
                (textid, ref["seq"], bucket, start, end, entry["label"], ref["marker_id"]),
            )

        part_rows = _build_parts(bundle_dir, textid, manifest, editions, jobs)
        for part in part_rows:
            _insert_part_rows(cur, textid, part)

        create_heavy_indices(conn)
        conn.commit()
    finally:
        conn.close()
    return out_path


def _build_parts(
    bundle_dir: Path,
    textid: str,
    manifest: dict,
    editions: list[str],
    jobs: int,
) -> list[_PartRows]:
    parts = manifest["assets"]["parts"]
    tasks = [
        (str(bundle_dir), textid, manifest, part, editions)
        for part in parts
    ]
    if jobs == 1 or len(tasks) < 2:
        return [_build_part_rows(task) for task in tasks]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(_build_part_rows, tasks))


def _build_part_rows(args) -> _PartRows:
    bundle_dir_s, textid, manifest, part, editions = args
    bundle_dir = Path(bundle_dir_s)
    seq = part["seq"]
    juan_path = bundle_dir / part["filename"]
    juan = _yaml_load_text(juan_path.read_text(encoding="utf-8"))
    if isinstance(juan, dict):
        juan = hydrate_juan_markers(
            juan, load_marker_asset(bundle_dir, manifest, seq),
        )

    buckets = []
    for kind in ("front", "body", "back"):
        bucket = juan.get(kind)
        if not bucket:
            continue
        text = unicodedata.normalize("NFC", bucket.get("text") or "")
        if not text:
            continue
        markers = bucket.get("markers") or []
        variants = [m for m in markers if m.get("type") == "variant"]
        voices = [m for m in markers if m.get("type") == "voice"]
        buckets.append(
            _BucketRows(
                kind=kind,
                text=text,
                variants=_variant_rows(variants),
                voices=_voice_range_rows(voices, len(text), textid, seq, kind),
                witnesses=_witness_rows(text, variants, editions),
            )
        )
    return _PartRows(seq=seq, hash=juan.get("hash"), buckets=buckets)


def _insert_part_rows(cur, textid: str, part: _PartRows) -> None:
    cur.execute(
        "INSERT INTO juan(textid, seq, hash) VALUES (?,?,?)",
        (textid, part.seq, part.hash),
    )
    juan_id = cur.lastrowid
    for bucket in part.buckets:
        cur.execute(
            "INSERT INTO bucket(juan_id, kind, text) VALUES (?,?,?)",
            (juan_id, bucket.kind, bucket.text),
        )
        bucket_id = cur.lastrowid
        _insert_variant_rows(cur, bucket_id, bucket.variants)
        _insert_voice_ranges(cur, bucket_id, bucket.voices)
        _insert_witness_texts(cur, bucket_id, bucket.witnesses)
        _insert_trigrams(cur, "bucket", bucket_id, bucket.text)


def _variant_rows(variants: list[dict]) -> list[tuple[int, int, str, str, str]]:
    rows = []
    for v in variants:
        m_off = v["offset"]
        length = v.get("length")
        if length is None:
            length = len(v.get("content") or "")
        content = v.get("content") or ""
        for k, w_form in v.items():
            if k in ("type", "offset", "length", "content", "id"):
                continue
            rows.append((m_off, length, content, k, w_form or ""))
    return rows


def _insert_variant_rows(
    cur,
    bucket_id: int,
    rows: list[tuple[int, int, str, str, str]],
) -> None:
    if rows:
        cur.executemany(
            "INSERT INTO variant(bucket_id, master_offset, length, content, "
            "witness, witness_form) VALUES (?,?,?,?,?,?)",
            ((bucket_id, m_off, length, content, witness, witness_form)
             for m_off, length, content, witness, witness_form in rows),
        )


def _voice_range_rows(
    voices: list[dict],
    text_len: int,
    textid: str,
    juan_seq: int,
    bucket_kind: str,
) -> list[tuple[int, int, str, str, str | None]]:
    """Validate voice markers and return rows without a bucket id."""
    if not voices:
        return []
    rows = []
    ids_seen: set[str] = set()
    for v in voices:
        off = v.get("offset")
        length = v.get("length")
        name = v.get("name")
        vid = v.get("id")
        responds_to = v.get("responds-to")
        if not isinstance(off, int) or not isinstance(length, int):
            raise ValueError(
                f"{textid}:{juan_seq}/{bucket_kind}: voice marker missing "
                f"integer offset/length: {v!r}"
            )
        if off < 0 or length < 0 or off + length > text_len:
            raise ValueError(
                f"{textid}:{juan_seq}/{bucket_kind}: voice marker out of range "
                f"(offset={off}, length={length}, text_len={text_len})"
            )
        if not name:
            raise ValueError(
                f"{textid}:{juan_seq}/{bucket_kind}: voice marker missing "
                f"non-empty name: {v!r}"
            )
        if not vid:
            raise ValueError(
                f"{textid}:{juan_seq}/{bucket_kind}: voice marker missing "
                f"non-empty id: {v!r}"
            )
        ids_seen.add(vid)
        rows.append((off, length, name, vid, responds_to))

    by_name: dict[str, list[tuple[int, int]]] = {}
    for off, length, name, _vid, _rt in rows:
        by_name.setdefault(name, []).append((off, off + length))
    for name, ranges in by_name.items():
        ranges.sort()
        for (a_start, a_end), (b_start, b_end) in zip(ranges, ranges[1:]):
            if b_start < a_end:
                raise ValueError(
                    f"{textid}:{juan_seq}/{bucket_kind}: overlapping voice "
                    f"ranges with name={name!r}: [{a_start},{a_end}) vs "
                    f"[{b_start},{b_end})"
                )

    for _off, _length, _name, _vid, responds_to in rows:
        if responds_to is not None and responds_to not in ids_seen:
            raise ValueError(
                f"{textid}:{juan_seq}/{bucket_kind}: voice marker "
                f"responds-to={responds_to!r} has no matching id in the same bucket"
            )
    return rows


def _insert_voice_ranges(
    cur,
    bucket_id: int,
    rows: list[tuple[int, int, str, str, str | None]],
) -> None:
    if rows:
        cur.executemany(
            "INSERT INTO voice_range(bucket_id, master_offset, length, name, "
            "voice_id, responds_to) VALUES (?,?,?,?,?,?)",
            ((bucket_id, off, length, name, vid, responds_to)
             for off, length, name, vid, responds_to in rows),
        )


def _witness_rows(
    master_text: str,
    variants: list[dict],
    editions: list[str],
) -> list[_WitnessRows]:
    rows = []
    for ed in editions:
        if not any(ed in v for v in variants):
            continue
        w_text, segs = apply_witness(master_text, variants, ed)
        seg_blob = json.dumps(
            [[s.w_start, s.w_end, s.m_start, s.m_end, int(s.is_variant)] for s in segs],
            ensure_ascii=True,
        ).encode("utf-8")
        rows.append(_WitnessRows(label=ed, text=w_text, segments=seg_blob))
    return rows


def _insert_witness_texts(
    cur,
    bucket_id: int,
    rows: list[_WitnessRows],
) -> None:
    for row in rows:
        cur.execute(
            "INSERT INTO witness(bucket_id, label, text, segments) VALUES (?,?,?,?)",
            (bucket_id, row.label, row.text, row.segments),
        )
        witness_id = cur.lastrowid
        _insert_trigrams(cur, "witness", witness_id, row.text)


def compute_bkkx_hash(path: Path | str) -> str:
    """Return ``sha256:<hex>`` of a ``.bkkx`` file (provenance for merge)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _insert_trigrams(cur, kind: str, source_id: int, text: str) -> None:
    if len(text) < 3:
        return
    cur.executemany(
        "INSERT INTO trigram(gram, source_kind, source_id, position) VALUES (?,?,?,?)",
        ((text[i:i + 3], kind, source_id, i) for i in range(len(text) - 2)),
    )
