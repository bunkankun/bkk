"""Build a ``.bkkx`` index file from a BKK bundle directory."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from pathlib import Path

import yaml

from .schema import DDL, SCHEMA_VERSION
from .witness import apply_witness


def build_index(bundle_dir: Path | str, out_path: Path | str | None = None) -> Path:
    """Build ``<textid>.bkkx`` from ``bundle_dir``.

    The index is written next to the manifest by default; pass ``out_path`` to
    override. Any existing file at the destination is overwritten.
    """
    bundle_dir = Path(bundle_dir)
    textid = bundle_dir.name
    if out_path is None:
        out_path = bundle_dir / f"{textid}.bkkx"
    else:
        out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()

    manifest_path = bundle_dir / f"{textid}.manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    editions = [e["short"] for e in (manifest.get("editions") or [])]

    conn = sqlite3.connect(str(out_path))
    try:
        conn.executescript(DDL)
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
            bucket, start, end = span
            cur.execute(
                "INSERT INTO toc(textid, juan_seq, bucket, span_start, span_end, "
                "label, marker_id) VALUES (?,?,?,?,?,?,?)",
                (textid, ref["seq"], bucket, start, end, entry["label"], ref["marker_id"]),
            )

        for part in manifest["assets"]["parts"]:
            seq = part["seq"]
            juan_path = bundle_dir / part["filename"]
            juan = yaml.safe_load(juan_path.read_text(encoding="utf-8"))
            cur.execute(
                "INSERT INTO juan(textid, seq, hash) VALUES (?,?,?)",
                (textid, seq, juan.get("hash")),
            )
            juan_id = cur.lastrowid

            for kind in ("front", "body", "back"):
                bucket = juan.get(kind)
                if not bucket:
                    continue
                text = unicodedata.normalize("NFC", bucket.get("text") or "")
                if not text:
                    continue
                cur.execute(
                    "INSERT INTO bucket(juan_id, kind, text) VALUES (?,?,?)",
                    (juan_id, kind, text),
                )
                bucket_id = cur.lastrowid

                variants = [
                    m for m in (bucket.get("markers") or [])
                    if m.get("type") == "variant"
                ]
                _insert_variant_rows(cur, bucket_id, variants)
                _insert_witness_texts(cur, bucket_id, text, variants, editions)
                _insert_trigrams(cur, "bucket", bucket_id, text)

        conn.commit()
    finally:
        conn.close()
    return out_path


def _insert_variant_rows(cur, bucket_id: int, variants: list[dict]) -> None:
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
            rows.append((bucket_id, m_off, length, content, k, w_form or ""))
    if rows:
        cur.executemany(
            "INSERT INTO variant(bucket_id, master_offset, length, content, "
            "witness, witness_form) VALUES (?,?,?,?,?,?)",
            rows,
        )


def _insert_witness_texts(cur, bucket_id: int, master_text: str,
                          variants: list[dict], editions: list[str]) -> None:
    for ed in editions:
        if not any(ed in v for v in variants):
            continue
        w_text, segs = apply_witness(master_text, variants, ed)
        seg_blob = json.dumps(
            [[s.w_start, s.w_end, s.m_start, s.m_end, int(s.is_variant)] for s in segs],
            ensure_ascii=True,
        ).encode("utf-8")
        cur.execute(
            "INSERT INTO witness(bucket_id, label, text, segments) VALUES (?,?,?,?)",
            (bucket_id, ed, w_text, seg_blob),
        )
        witness_id = cur.lastrowid
        _insert_trigrams(cur, "witness", witness_id, w_text)


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
