"""Query a ``.bkkx`` index and assemble variant-aware KWIC results."""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from collections.abc import Iterator
from pathlib import Path

from .ir import Hit, VariantOverlay
from .schema import SCHEMA_VERSION
from .witness import Segment, witness_to_master_span


class Index:
    """Read-only handle on a ``.bkkx`` file."""

    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        meta = dict(self._conn.execute("SELECT key, value FROM meta").fetchall())
        version = int(meta.get("schema_version", "0") or "0")
        if version != SCHEMA_VERSION:
            self._conn.close()
            raise ValueError(
                f"index at {path} has schema version {version}, "
                f"expected {SCHEMA_VERSION}; rebuild it with `bkk index build` "
                f"(or `bkk index merge --rebuild` for a corpus index)"
            )
        self.textid = meta.get("textid", "")
        self.editions = json.loads(meta.get("editions", "[]"))
        # Corpus indices populate the `bundle` table; per-bundle indices don't
        # have one until v2. Keep `bundles` always available so callers can
        # treat the two shapes uniformly.
        try:
            self.bundles = [
                r[0] for r in self._conn.execute(
                    "SELECT textid FROM bundle ORDER BY textid"
                )
            ]
        except sqlite3.OperationalError:
            self.bundles = [self.textid] if self.textid else []

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Index":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def search(
        self,
        query: str,
        context: int = 20,
        witnesses: set[str] | None = None,
        textid: str | None = None,
        voices: set[str] | None = None,
    ) -> Iterator[Hit]:
        """Yield :class:`Hit` for every position matching ``query``.

        ``query`` is plain CJK substring; matching is variant-aware via the
        per-witness derived texts. ``context`` is the KWIC window to each
        side (in chars). ``witnesses`` restricts which witness texts are
        searched; master matches are always returned. ``textid`` scopes
        results to a single bundle when the index is a merged corpus.

        ``voices``, when given, filters hits to those with at least one
        fully-containing voice range whose ``name`` is in the set. A hit
        nested inside two ranges (e.g. ``sound-gloss`` inside
        ``commentary``) qualifies for either name. ``None`` means no
        voice filter — all hits are emitted, each tagged with its voice.
        """
        query = unicodedata.normalize("NFC", query)
        if not query:
            return
        candidates = self._candidate_positions(query)
        for (kind, src_id), positions in candidates.items():
            yield from self._verify_and_emit(
                query, kind, src_id, sorted(positions), context,
                witnesses, textid, voices,
            )

    def available_voices(self) -> list[str]:
        """Return the sorted set of distinct voice names present in the index."""
        return [
            r[0] for r in self._conn.execute(
                "SELECT DISTINCT name FROM voice_range ORDER BY name"
            )
        ]

    # -- candidate enumeration ------------------------------------------------

    def _candidate_positions(self, query: str) -> dict[tuple[str, int], set[int]]:
        if len(query) < 3:
            return self._scan_all_sources(query)
        grams = [query[i:i + 3] for i in range(len(query) - 2)]
        result: dict[tuple[str, int], set[int]] = {}
        first = self._postings_for_gram(grams[0])
        for kind, src_id, pos in first:
            result.setdefault((kind, src_id), set()).add(pos)
        for gi, gram in enumerate(grams[1:], start=1):
            shifted: dict[tuple[str, int], set[int]] = {}
            for kind, src_id, pos in self._postings_for_gram(gram):
                shifted.setdefault((kind, src_id), set()).add(pos - gi)
            new_result: dict[tuple[str, int], set[int]] = {}
            for key, base in result.items():
                inter = base & shifted.get(key, set())
                if inter:
                    new_result[key] = inter
            result = new_result
            if not result:
                break
        return result

    def _scan_all_sources(self, query: str) -> dict[tuple[str, int], set[int]]:
        out: dict[tuple[str, int], set[int]] = {}
        for row in self._conn.execute(
            "SELECT bucket_id AS id, text FROM bucket"
        ):
            positions = _find_all(row["text"], query)
            if positions:
                out[("bucket", row["id"])] = set(positions)
        for row in self._conn.execute(
            "SELECT witness_id AS id, text FROM witness"
        ):
            positions = _find_all(row["text"], query)
            if positions:
                out[("witness", row["id"])] = set(positions)
        return out

    def _postings_for_gram(self, gram: str):
        return self._conn.execute(
            "SELECT source_kind, source_id, position FROM trigram WHERE gram = ?",
            (gram,),
        ).fetchall()

    # -- verification + emission ---------------------------------------------

    def _verify_and_emit(
        self, query, kind, src_id, positions, context, witnesses, textid, voices,
    ) -> Iterator[Hit]:
        if kind == "bucket":
            row = self._conn.execute(
                "SELECT b.bucket_id, b.text, b.kind, j.seq, j.textid "
                "FROM bucket b JOIN juan j ON b.juan_id = j.juan_id "
                "WHERE b.bucket_id = ?",
                (src_id,),
            ).fetchone()
            if row is None:
                return
            if textid is not None and row["textid"] != textid:
                return
            for pos in positions:
                if row["text"][pos:pos + len(query)] != query:
                    continue
                hit = self._make_hit(
                    row["textid"], row["seq"], row["kind"], row["bucket_id"],
                    row["text"], pos, len(query), "master", query, context,
                )
                if _passes_voice_filter(hit, voices):
                    yield hit
            return

        # witness
        row = self._conn.execute(
            "SELECT w.witness_id, w.text AS wtext, w.label, w.segments, "
            "b.bucket_id, b.text AS btext, b.kind, j.seq, j.textid "
            "FROM witness w JOIN bucket b ON w.bucket_id = b.bucket_id "
            "JOIN juan j ON b.juan_id = j.juan_id WHERE w.witness_id = ?",
            (src_id,),
        ).fetchone()
        if row is None:
            return
        if textid is not None and row["textid"] != textid:
            return
        label = row["label"]
        if witnesses is not None and label not in witnesses:
            return
        wtext = row["wtext"]
        btext = row["btext"]
        segments = _decode_segments(row["segments"])
        for pos in positions:
            if wtext[pos:pos + len(query)] != query:
                continue
            m_off, m_len = witness_to_master_span(segments, pos, pos + len(query))
            # Suppress witness hits where the witness reading at this span is
            # identical to the master reading — that hit is already covered by
            # the master-text scan and would render as a duplicate KWIC line.
            if btext[m_off:m_off + m_len] == query:
                continue
            hit = self._make_hit(
                row["textid"], row["seq"], row["kind"], row["bucket_id"],
                btext, m_off, m_len, label, query, context,
                witness_text=wtext[pos:pos + len(query)],
            )
            if _passes_voice_filter(hit, voices):
                yield hit

    def _make_hit(
        self, textid, juan_seq, bucket_kind, bucket_id, bucket_text,
        m_off, m_len, matched_via, query, context, *, witness_text=None,
    ) -> Hit:
        win_lo = max(0, m_off - context)
        win_hi = m_off + m_len + context
        voice, voice_stack = self._classify_voice(bucket_id, m_off, m_off + m_len)
        return Hit(
            textid=textid,
            juan_seq=juan_seq,
            bucket=bucket_kind,
            master_offset=m_off,
            master_length=m_len,
            matched_via=matched_via,
            matched_text=witness_text if witness_text is not None else query,
            left=bucket_text[win_lo:m_off],
            match=bucket_text[m_off:m_off + m_len],
            right=bucket_text[m_off + m_len:win_hi],
            overlays=tuple(self._overlays(bucket_id, win_lo, win_hi)),
            toc_label=self._toc_label(textid, juan_seq, bucket_kind, m_off),
            voice=voice,
            voice_stack=voice_stack,
        )

    def _classify_voice(
        self, bucket_id: int, hit_start: int, hit_end: int,
    ) -> tuple[str, tuple[str, ...]]:
        """Return (voice, voice_stack) under strict containment.

        - ≥1 range fully contains the hit → ``voice`` is the innermost name
          (smallest covering range); ``voice_stack`` lists every fully
          containing range's name, outermost → innermost.
        - some range intersects but none fully contains → ``("mixed", ())``.
        - no range touches the hit → ``("none", ())``.
        """
        rows = self._conn.execute(
            "SELECT master_offset, length, name FROM voice_range "
            "WHERE bucket_id = ? "
            "AND master_offset < ? "
            "AND master_offset + length > ?",
            (bucket_id, hit_end, hit_start),
        ).fetchall()
        if not rows:
            return "none", ()
        containing: list[tuple[int, int, str]] = []
        for r in rows:
            r_start = r["master_offset"]
            r_end = r_start + r["length"]
            if r_start <= hit_start and r_end >= hit_end:
                containing.append((r_start, r_end, r["name"]))
        if not containing:
            return "mixed", ()
        # Outer-to-inner: widest range first, narrowest last.
        containing.sort(key=lambda t: (t[1] - t[0], t[0]), reverse=True)
        stack = tuple(name for _s, _e, name in containing)
        return stack[-1], stack

    def _overlays(self, bucket_id: int, lo: int, hi: int) -> list[VariantOverlay]:
        rows = self._conn.execute(
            "SELECT master_offset, length, content, witness, witness_form "
            "FROM variant WHERE bucket_id = ? "
            "AND master_offset < ? AND master_offset + length > ? "
            "ORDER BY master_offset, witness",
            (bucket_id, hi, lo),
        ).fetchall()
        return [
            VariantOverlay(
                master_offset=r["master_offset"],
                length=r["length"],
                content=r["content"],
                witness=r["witness"],
                witness_form=r["witness_form"],
            )
            for r in rows
        ]

    def _toc_label(self, textid, juan_seq, bucket, m_off) -> str | None:
        row = self._conn.execute(
            "SELECT label FROM toc "
            "WHERE textid = ? AND juan_seq = ? AND bucket = ? "
            "AND span_start <= ? AND span_end > ? "
            "ORDER BY (span_end - span_start) ASC LIMIT 1",
            (textid, juan_seq, bucket, m_off, m_off),
        ).fetchone()
        return row["label"] if row else None


def _passes_voice_filter(hit: Hit, voices: set[str] | None) -> bool:
    """True iff the hit qualifies under the voice filter set.

    None means no filter. Otherwise the hit qualifies if some
    fully-containing voice range's name is in ``voices`` — so a hit
    nested inside two ranges qualifies under either name.
    """
    if voices is None:
        return True
    return any(name in voices for name in hit.voice_stack)


def _decode_segments(blob) -> list[Segment]:
    raw = json.loads(bytes(blob).decode("utf-8"))
    return [Segment(s[0], s[1], s[2], s[3], bool(s[4])) for s in raw]


def _find_all(text: str, needle: str) -> list[int]:
    out: list[int] = []
    start = 0
    while True:
        i = text.find(needle, start)
        if i < 0:
            return out
        out.append(i)
        start = i + 1
