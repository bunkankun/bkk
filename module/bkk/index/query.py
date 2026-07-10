"""Query a ``.bkkx`` index and assemble variant-aware KWIC results."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from collections import Counter

from bkk.chars.canonicalize import canonicalize_query
from bkk.chars.refs import CanonicalizationContext

from .ir import Hit, IndexSummary, VariantOverlay
from .schema import SCHEMA_VERSION
from .witness import Segment, witness_to_master_span


_SQLITE_VAR_LIMIT = 500  # SQLite default ?-binding cap is 999; keep margin.


class RegexZeroLengthError(ValueError):
    """Raised when a regex match has no span and cannot produce a KWIC hit."""


CompoundOperator = Literal["near", "not"]


class Index:
    """Read-only handle on a ``.bkkx`` file."""

    def __init__(
        self,
        path: Path | str,
        *,
        canon_ctx: CanonicalizationContext | None = None,
    ):
        self._conn = sqlite3.connect(str(path))
        self._canon = canon_ctx
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

    def _prepare_query(self, query: str) -> str:
        """NFC-normalize then (if a canon ctx is set) apply step-5 substitution.

        The indexed corpus is built from canonicalized bundles, so any
        non-canonical codepoint in a raw query must be folded to its
        canonical form before trigram lookup, mirroring what the importer
        did at write time.
        """
        query = unicodedata.normalize("NFC", query)
        if self._canon is not None:
            query = canonicalize_query(query, self._canon)
        return query

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
        candidates: dict[tuple[str, int], set[int]] | None = None,
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

        ``candidates`` can be a pre-computed positions dict (as returned
        by :meth:`candidates_and_total`) to avoid re-running the
        candidate scan when the caller already paid for it.
        """
        query = self._prepare_query(query)
        if not query:
            return
        if candidates is None:
            candidates = self._candidate_positions(query)
        for (kind, src_id), positions in candidates.items():
            yield from self._verify_and_emit(
                query, kind, src_id, sorted(positions), context,
                witnesses, textid, voices,
            )

    def compound_candidates_and_total(
        self,
        left: str,
        operator: CompoundOperator,
        right: str,
        distance: int,
    ) -> tuple[dict[tuple[str, int], set[int]], int]:
        """Return left-term candidates filtered by proximity to the right term.

        ``distance`` is the unordered character gap between two spans in the
        same indexed source row. Adjacent or overlapping spans have gap 0.
        ``operator`` is ``"near"`` or ``"not"``.
        """
        left = self._prepare_query(left)
        right = self._prepare_query(right)
        if not left or not right:
            return {}, 0
        left_candidates = self._candidate_positions(left)
        right_candidates = self._candidate_positions(right)
        filtered = self._filter_compound_candidates(
            left, left_candidates, right, right_candidates, operator, distance,
        )
        return filtered, sum(len(v) for v in filtered.values())

    def summarise_candidates(
        self,
        query: str,
        *,
        candidates: dict[tuple[str, int], set[int]],
        textids: set[str] | None = None,
        witnesses: set[str] | None = None,
        master_only: bool = False,
        max_extensions: int = 20,
        include_extensions: bool = True,
    ) -> IndexSummary:
        """Summarise an already-prepared candidate map for ``query``."""
        query = self._prepare_query(query)
        if not query:
            return IndexSummary(total=0)
        return self._summarise_prepared(
            query,
            candidates,
            textids=textids,
            witnesses=witnesses,
            master_only=master_only,
            max_extensions=max_extensions,
            include_extensions=include_extensions,
        )

    def search_regex(
        self,
        pattern: str,
        flags: int = 0,
        context: int = 20,
        witnesses: set[str] | None = None,
        textid: str | None = None,
        voices: set[str] | None = None,
        anchor: str | None = None,
    ) -> Iterator[Hit]:
        """Yield :class:`Hit` for every regex match.

        ``anchor``, when present, is a literal substring known to be required
        by the regex. It narrows the source rows to scan via the existing
        candidate machinery; the regex is still evaluated against each whole
        source row so variable-length matches can start before or after the
        anchor occurrence.
        """
        compiled = re.compile(self._prepare_query(pattern), flags)
        source_keys: set[tuple[str, int]] | None = None
        if anchor:
            anchor = self._prepare_query(anchor)
            source_keys = set(self._candidate_positions(anchor))
            if not source_keys:
                return

        if source_keys is None:
            yield from self._scan_regex_all_sources(
                compiled, context, witnesses, textid, voices,
            )
            return

        for kind, src_id in sorted(source_keys):
            if kind == "bucket":
                yield from self._regex_bucket_hits(
                    compiled, src_id, context, textid, voices,
                )
            else:
                yield from self._regex_witness_hits(
                    compiled, src_id, context, witnesses, textid, voices,
                )

    def candidates_and_total(
        self, query: str,
    ) -> tuple[dict[tuple[str, int], set[int]], int]:
        """Return the raw candidate-positions dict and the summed total.

        ``total`` is exact for queries of length < 3 (positions are
        string-verified against bucket/witness texts in
        :meth:`_scan_all_sources`) and an upper bound for longer queries
        (trigram candidates are only string-verified inside
        :meth:`_verify_and_emit`). The dict is suitable to pass back into
        :meth:`search` or :meth:`summarise` to avoid a second scan.
        """
        query = self._prepare_query(query)
        if not query:
            return {}, 0
        cand = self._candidate_positions(query)
        total = sum(len(v) for v in cand.values())
        return cand, total

    def summarise(
        self,
        query: str,
        *,
        candidates: dict[tuple[str, int], set[int]] | None = None,
        textids: set[str] | None = None,
        witnesses: set[str] | None = None,
        master_only: bool = False,
        max_extensions: int = 20,
    ) -> IndexSummary:
        """Bird's-eye rollup over candidate positions — no Hits, no KWIC.

        Used by the search endpoint when a query exceeds the configured
        materialisation cap. Counts roll up via two small SQL joins
        (one per source kind) plus two trigram-extension aggregates;
        the heavy work is the single ``_candidate_positions`` call,
        which can be passed in via ``candidates`` when the caller has
        already computed it (e.g. through :meth:`candidates_and_total`).
        """
        query = self._prepare_query(query)
        if not query:
            return IndexSummary(total=0)
        if candidates is None:
            candidates = self._candidate_positions(query)
        return self._summarise_prepared(
            query,
            candidates,
            textids=textids,
            witnesses=witnesses,
            master_only=master_only,
            max_extensions=max_extensions,
            include_extensions=True,
        )

    def _summarise_prepared(
        self,
        query: str,
        candidates: dict[tuple[str, int], set[int]],
        *,
        textids: set[str] | None,
        witnesses: set[str] | None,
        master_only: bool,
        max_extensions: int,
        include_extensions: bool,
    ) -> IndexSummary:
        bucket_counts: dict[int, int] = {}
        witness_counts: dict[int, int] = {}
        for (kind, src_id), positions in candidates.items():
            if kind == "bucket":
                bucket_counts[src_id] = len(positions)
            else:
                witness_counts[src_id] = len(positions)

        by_textid: Counter[str] = Counter()
        by_witness_label: Counter[str] = Counter()

        for chunk in _chunked(list(bucket_counts), _SQLITE_VAR_LIMIT):
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT b.bucket_id, j.textid "
                f"FROM bucket b JOIN juan j ON b.juan_id = j.juan_id "
                f"WHERE b.bucket_id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                tid = row["textid"]
                if textids is not None and tid not in textids:
                    continue
                c = bucket_counts[row["bucket_id"]]
                by_textid[tid] += c
                by_witness_label["master"] += c

        if not master_only:
            for chunk in _chunked(list(witness_counts), _SQLITE_VAR_LIMIT):
                placeholders = ",".join("?" * len(chunk))
                rows = self._conn.execute(
                    f"SELECT w.witness_id, w.label, j.textid "
                    f"FROM witness w JOIN bucket b ON w.bucket_id = b.bucket_id "
                    f"JOIN juan j ON b.juan_id = j.juan_id "
                    f"WHERE w.witness_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    tid = row["textid"]
                    if textids is not None and tid not in textids:
                        continue
                    label = row["label"]
                    if witnesses is not None and label not in witnesses:
                        continue
                    c = witness_counts[row["witness_id"]]
                    by_textid[tid] += c
                    by_witness_label[label] += c

        if not include_extensions:
            return IndexSummary(
                total=sum(by_witness_label.values()),
                by_textid=dict(by_textid),
                by_witness_label=dict(by_witness_label),
            )

        # Trigram extensions: rather than scanning the trigram table with
        # ``gram LIKE '_xy'`` (unindexable, full-scan over billions of
        # rows on a merged corpus), we derive them by reading the 1-char
        # context immediately to the left/right of each match position
        # in the candidate sources. The set of distinct sources to read
        # is bounded by the candidate dict, not the position count, so
        # cost stays bounded even for very common queries.
        trigram_left, trigram_right = self._extensions_from_candidates(
            query, candidates, textids=textids, witnesses=witnesses,
            limit=max_extensions,
        )

        total = sum(by_witness_label.values())
        return IndexSummary(
            total=total,
            by_textid=dict(by_textid),
            by_witness_label=dict(by_witness_label),
            trigram_left=trigram_left,
            trigram_right=trigram_right,
        )

    def available_voices(self) -> list[str]:
        """Return the sorted set of distinct voice names present in the index."""
        return [
            r[0] for r in self._conn.execute(
                "SELECT DISTINCT name FROM voice_range ORDER BY name"
            )
        ]

    def _extensions_from_candidates(
        self,
        query: str,
        candidates: dict[tuple[str, int], set[int]],
        *,
        textids: set[str] | None,
        witnesses: set[str] | None,
        limit: int,
    ) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
        """Walk match positions in each candidate source and roll up
        the 1-char left and right context characters into top-N counts.

        ``limit`` caps each direction. Returned grams are
        ``left_char + query`` and ``query + right_char`` — i.e. the
        substring the user would refine to by picking that extension.
        """
        qlen = len(query)
        left_counts: Counter[str] = Counter()
        right_counts: Counter[str] = Counter()
        if qlen == 0:
            return [], []

        bucket_positions: dict[int, set[int]] = {}
        witness_positions: dict[int, set[int]] = {}
        for (kind, src_id), positions in candidates.items():
            if kind == "bucket":
                bucket_positions[src_id] = positions
            else:
                witness_positions[src_id] = positions

        for chunk in _chunked(list(bucket_positions), _SQLITE_VAR_LIMIT):
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT b.bucket_id, b.text, j.textid "
                f"FROM bucket b JOIN juan j ON b.juan_id = j.juan_id "
                f"WHERE b.bucket_id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                if textids is not None and row["textid"] not in textids:
                    continue
                text = row["text"]
                for pos in bucket_positions[row["bucket_id"]]:
                    if pos > 0:
                        left_counts[text[pos - 1] + query] += 1
                    if pos + qlen < len(text):
                        right_counts[query + text[pos + qlen]] += 1

        for chunk in _chunked(list(witness_positions), _SQLITE_VAR_LIMIT):
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT w.witness_id, w.text, w.label, j.textid "
                f"FROM witness w JOIN bucket b ON w.bucket_id = b.bucket_id "
                f"JOIN juan j ON b.juan_id = j.juan_id "
                f"WHERE w.witness_id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                if textids is not None and row["textid"] not in textids:
                    continue
                if witnesses is not None and row["label"] not in witnesses:
                    continue
                text = row["text"]
                for pos in witness_positions[row["witness_id"]]:
                    if pos > 0:
                        left_counts[text[pos - 1] + query] += 1
                    if pos + qlen < len(text):
                        right_counts[query + text[pos + qlen]] += 1

        return left_counts.most_common(limit), right_counts.most_common(limit)

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

    def _filter_compound_candidates(
        self,
        left: str,
        left_candidates: dict[tuple[str, int], set[int]],
        right: str,
        right_candidates: dict[tuple[str, int], set[int]],
        operator: CompoundOperator,
        distance: int,
    ) -> dict[tuple[str, int], set[int]]:
        out: dict[tuple[str, int], set[int]] = {}
        left_len = len(left)
        right_len = len(right)
        for key, left_positions in left_candidates.items():
            right_positions = right_candidates.get(key, set())
            if not left_positions:
                continue
            text = self._source_text(*key)
            verified_left = [
                pos for pos in sorted(left_positions)
                if text[pos:pos + left_len] == left
            ]
            if not verified_left:
                continue
            verified_right = [
                pos for pos in sorted(right_positions)
                if text[pos:pos + right_len] == right
            ]
            keep: set[int] = set()
            for pos in verified_left:
                near = _has_nearby_span(pos, left_len, verified_right, right_len, distance)
                if (operator == "near" and near) or (operator == "not" and not near):
                    keep.add(pos)
            if keep:
                out[key] = keep
        return out

    def _source_text(self, kind: str, src_id: int) -> str:
        if kind == "bucket":
            row = self._conn.execute(
                "SELECT text FROM bucket WHERE bucket_id = ?",
                (src_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT text FROM witness WHERE witness_id = ?",
                (src_id,),
            ).fetchone()
        return row["text"] if row is not None else ""

    # -- regex source scans ---------------------------------------------------

    def _scan_regex_all_sources(
        self,
        pattern: re.Pattern[str],
        context: int,
        witnesses: set[str] | None,
        textid: str | None,
        voices: set[str] | None,
    ) -> Iterator[Hit]:
        if textid is None:
            bucket_rows = self._conn.execute(
                "SELECT b.bucket_id "
                "FROM bucket b JOIN juan j ON b.juan_id = j.juan_id"
            ).fetchall()
            witness_rows = self._conn.execute(
                "SELECT w.witness_id "
                "FROM witness w JOIN bucket b ON w.bucket_id = b.bucket_id "
                "JOIN juan j ON b.juan_id = j.juan_id"
            ).fetchall()
        else:
            bucket_rows = self._conn.execute(
                "SELECT b.bucket_id "
                "FROM bucket b JOIN juan j ON b.juan_id = j.juan_id "
                "WHERE j.textid = ?",
                (textid,),
            ).fetchall()
            witness_rows = self._conn.execute(
                "SELECT w.witness_id "
                "FROM witness w JOIN bucket b ON w.bucket_id = b.bucket_id "
                "JOIN juan j ON b.juan_id = j.juan_id "
                "WHERE j.textid = ?",
                (textid,),
            ).fetchall()
        for row in bucket_rows:
            yield from self._regex_bucket_hits(
                pattern, row["bucket_id"], context, textid, voices,
            )
        for row in witness_rows:
            yield from self._regex_witness_hits(
                pattern, row["witness_id"], context, witnesses, textid, voices,
            )

    def _regex_bucket_hits(
        self,
        pattern: re.Pattern[str],
        src_id: int,
        context: int,
        textid: str | None,
        voices: set[str] | None,
    ) -> Iterator[Hit]:
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
        for match in pattern.finditer(row["text"]):
            pos, end = match.span()
            if end == pos:
                raise RegexZeroLengthError("regex produced a zero-length match")
            matched_text = row["text"][pos:end]
            hit = self._make_hit(
                row["textid"], row["seq"], row["kind"], row["bucket_id"],
                row["text"], pos, end - pos, "master", matched_text, context,
                witness_text=matched_text,
            )
            if _passes_voice_filter(hit, voices):
                yield hit

    def _regex_witness_hits(
        self,
        pattern: re.Pattern[str],
        src_id: int,
        context: int,
        witnesses: set[str] | None,
        textid: str | None,
        voices: set[str] | None,
    ) -> Iterator[Hit]:
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
        for match in pattern.finditer(wtext):
            pos, end = match.span()
            if end == pos:
                raise RegexZeroLengthError("regex produced a zero-length match")
            matched_text = wtext[pos:end]
            m_off, m_len = witness_to_master_span(segments, pos, end)
            if btext[m_off:m_off + m_len] == matched_text:
                continue
            w_lo = max(0, pos - context)
            w_hi = end + context
            ANCHOR_PAD = 6
            v_left_w_start = None
            v_right_w_end = None
            for seg in segments:
                if seg.is_variant and seg.w_start < end and seg.w_end > pos:
                    if v_left_w_start is None or seg.w_start < v_left_w_start:
                        v_left_w_start = seg.w_start
                    if v_right_w_end is None or seg.w_end > v_right_w_end:
                        v_right_w_end = seg.w_end
            if v_left_w_start is not None:
                w_lo = min(w_lo, max(0, v_left_w_start - ANCHOR_PAD))
            if v_right_w_end is not None:
                w_hi = max(w_hi, min(len(wtext), v_right_w_end + ANCHOR_PAD))
            witness_left = wtext[w_lo:pos]
            witness_right = wtext[end:w_hi]
            if v_left_w_start is not None:
                w_left_var_off = max(0, v_left_w_start - w_lo)
            else:
                w_left_var_off = 0
            if v_right_w_end is not None:
                w_right_var_end = max(0, min(len(witness_right), v_right_w_end - end))
            else:
                w_right_var_end = len(witness_right)
            hit = self._make_hit(
                row["textid"], row["seq"], row["kind"], row["bucket_id"],
                btext, m_off, m_len, label, matched_text, context,
                witness_text=matched_text,
                witness_left=witness_left,
                witness_right=witness_right,
                witness_left_variant_offset=w_left_var_off,
                witness_right_variant_end=w_right_var_end,
            )
            if _passes_voice_filter(hit, voices):
                yield hit

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
            w_lo = max(0, pos - context)
            w_hi = pos + len(query) + context
            # Extend the witness window outward across the variant boundary
            # into adjacent identity segments by ANCHOR_PAD chars, so the
            # witness KWIC always shows some master text framing the variant.
            # That gives the eye a shared anchor with the master line above
            # when the variant alone is wider than ``context``.
            ANCHOR_PAD = 6
            match_hi = pos + len(query)
            v_left_w_start = None
            v_right_w_end = None
            for seg in segments:
                if seg.is_variant and seg.w_start < match_hi and seg.w_end > pos:
                    if v_left_w_start is None or seg.w_start < v_left_w_start:
                        v_left_w_start = seg.w_start
                    if v_right_w_end is None or seg.w_end > v_right_w_end:
                        v_right_w_end = seg.w_end
            if v_left_w_start is not None:
                w_lo = min(w_lo, max(0, v_left_w_start - ANCHOR_PAD))
            if v_right_w_end is not None:
                w_hi = max(w_hi, min(len(wtext), v_right_w_end + ANCHOR_PAD))
            witness_left = wtext[w_lo:pos]
            witness_right = wtext[pos + len(query):w_hi]
            # Boundary of the variant-interior portion within witness_left /
            # witness_right, so callers can split anchor (master) from
            # interior (variant) and optionally collapse the interior.
            if v_left_w_start is not None:
                w_left_var_off = max(0, v_left_w_start - w_lo)
            else:
                w_left_var_off = 0
            if v_right_w_end is not None:
                w_right_var_end = max(
                    0, min(len(witness_right), v_right_w_end - (pos + len(query)))
                )
            else:
                w_right_var_end = len(witness_right)
            hit = self._make_hit(
                row["textid"], row["seq"], row["kind"], row["bucket_id"],
                btext, m_off, m_len, label, query, context,
                witness_text=wtext[pos:pos + len(query)],
                witness_left=witness_left,
                witness_right=witness_right,
                witness_left_variant_offset=w_left_var_off,
                witness_right_variant_end=w_right_var_end,
            )
            if _passes_voice_filter(hit, voices):
                yield hit

    def _make_hit(
        self, textid, juan_seq, bucket_kind, bucket_id, bucket_text,
        m_off, m_len, matched_via, query, context, *, witness_text=None,
        witness_left="", witness_right="",
        witness_left_variant_offset=0, witness_right_variant_end=0,
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
            witness_left=witness_left,
            witness_right=witness_right,
            witness_left_variant_offset=witness_left_variant_offset,
            witness_right_variant_end=witness_right_variant_end,
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


def _chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _find_all(text: str, needle: str) -> list[int]:
    out: list[int] = []
    start = 0
    while True:
        i = text.find(needle, start)
        if i < 0:
            return out
        out.append(i)
        start = i + 1


def _has_nearby_span(
    left_start: int,
    left_len: int,
    right_starts: list[int],
    right_len: int,
    distance: int,
) -> bool:
    left_end = left_start + left_len
    for right_start in right_starts:
        right_end = right_start + right_len
        if right_end < left_start:
            gap = left_start - right_end
        elif left_end < right_start:
            gap = right_start - left_end
        else:
            gap = 0
        if gap <= distance:
            return True
    return False
