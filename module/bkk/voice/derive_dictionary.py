"""Derive dictionary voice markers for lemma-repeat placeholders.

Dictionary-like KRP texts often print an entry lemma in the main column and
the explanation in a smaller annotation column. There, repeated lemma
characters may be abbreviated with U+4E28 (``丨``). This deriver identifies
small note spans that contain those placeholders and annotates each span with
the lemma needed by the substitution pass.

The source has no explicit word separators, so this is a narrow heuristic
rather than a general dictionary parser. It is intentionally opt-in through
``bkk voice add --source dictionary``.
"""

from __future__ import annotations

from bkk.importer.charset import is_allowed_body_char


PLACEHOLDER = "丨"

_HEAD_CUES = (
    "唐韻", "廣韻", "集韻", "韻會", "正韻", "玉篇", "類篇",
)

_LABELS = (
    "補藻", "補注", "補音", "補義", "補遺", "補正", "韻藻",
    "補", "藻", "增",
)

_SOURCE_CUES = tuple(sorted({
    "後漢書", "舊唐書", "春秋左傳", "春秋", "漢書", "唐書", "宋史",
    "遼史", "史記", "魏志", "魏書", "周書", "梁書", "南史", "北史",
    "隋書", "齊書", "宋書", "晉書", "周禮", "儀禮", "禮記", "爾雅",
    "論語", "孟子", "管子", "莊子", "列子", "韓非子", "淮南子",
    "抱朴子", "文心雕龍", "潛夫論", "參同契", "易林", "法言",
    "通考", "國䇿", "戰國策", "新序", "山海經", "本草", "楚辭",
    "書", "詩",
}, key=len, reverse=True))

_MAX_LEMMA_LEN = 4
_LEMMA_HINT_WINDOW = 32
_LEMMA_BOUNDARY_CHARS = frozenset("也注又曰云矣耳焉者兮乎哉乃則")


def derive_dictionary_voice_markers(
    text: str,
    markers: list[dict],
) -> list[dict]:
    """Return dictionary voice markers carrying lemma metadata.

    The emitted spans start at a detected source/citation cue and end before
    the next detected lemma in the same line. Spans without ``丨`` are skipped.
    """
    if not text or PLACEHOLDER not in text:
        return []

    line_offsets = _line_offsets(len(text), markers)
    segments = _head_segments(text, line_offsets)
    out: list[dict] = []
    counter = 0

    for start, end, head in segments:
        if end <= start or PLACEHOLDER not in text[start:end]:
            continue
        segment = text[start:end]
        candidates = _head_gloss_candidates(segment, head)
        candidates.extend(_lemma_candidates(segment, head))
        candidates.sort(key=lambda c: (c["lemma_start"], c["source_start"]))
        for i, candidate in enumerate(candidates):
            next_lemma_start = (
                candidates[i + 1]["lemma_start"]
                if i + 1 < len(candidates) else len(segment)
            )
            span_start = candidate["source_start"]
            span_end = min(candidate.get("span_end", len(segment)), next_lemma_start)
            if span_end <= span_start:
                continue
            span = segment[span_start:span_end]
            if PLACEHOLDER not in span:
                continue
            counter += 1
            out.append({
                "type": "voice",
                "offset": start + span_start,
                "length": span_end - span_start,
                "name": "dict",
                "id": f"dn{counter}",
                "source": "dictionary",
                "lemma": candidate["lemma"],
                "lemma_offset": start + candidate["lemma_start"],
                "lemma_length": len(candidate["lemma"]),
            })
    return out


def _line_offsets(text_len: int, markers: list[dict]) -> list[int]:
    offsets = {
        m.get("offset")
        for m in markers
        if isinstance(m, dict)
        and m.get("type") == "line-break"
        and isinstance(m.get("offset"), int)
        and 0 <= m.get("offset") <= text_len
    }
    offsets.add(0)
    return sorted(offsets)


def _head_segments(text: str, line_offsets: list[int]) -> list[tuple[int, int, str]]:
    segments: list[tuple[int, int, str]] = []
    current_head: str | None = None
    current_start: int | None = None
    for index, start in enumerate(line_offsets):
        end = line_offsets[index + 1] if index + 1 < len(line_offsets) else len(text)
        head = _head_from_line(text[start:end])
        if head is None:
            continue
        if current_head is not None and current_start is not None and start > current_start:
            segments.append((current_start, start, current_head))
        current_head = head
        current_start = start
    if current_head is not None and current_start is not None and current_start < len(text):
        segments.append((current_start, len(text), current_head))
    return segments


def _head_from_line(line: str) -> str | None:
    found: list[tuple[int, str]] = []
    for cue in _HEAD_CUES:
        pos = line.find(cue)
        if 1 <= pos <= 8 and "切" not in line[:pos]:
            found.append((pos, cue))
    for pos, _cue in sorted(found):
        if not _has_label_prefix(line[:pos]):
            head = line[pos - 1]
            if _is_lemma_char(head):
                return head
    if line and _is_lemma_char(line[0]):
        cut_pos = line.find("切", 2, 9)
        if cut_pos >= 2 and all(_is_lemma_char(ch) for ch in line[1:cut_pos]):
            return line[0]
    return None


def _head_gloss_candidates(line: str, head: str) -> list[dict]:
    if not line.startswith(head):
        return []
    cut_pos = line.find("切", 2, 9)
    if cut_pos < 0:
        return []
    label_start = _first_label_start(line[cut_pos + 1:])
    if label_start is None:
        return []
    label_start += cut_pos + 1
    source_start = cut_pos + 1
    if PLACEHOLDER not in line[source_start:label_start]:
        return []
    return [{
        "lemma_start": 0,
        "source_start": source_start,
        "span_end": label_start,
        "lemma": head,
    }]


def _lemma_candidates(line: str, head: str) -> list[dict]:
    valid_source_starts = [
        source_start
        for source_start, _cue in _source_cue_positions(line)
        if source_start > 0 and line[source_start - 1] == head
    ]
    raw: list[dict] = []
    for source_start in valid_source_starts:
        lemma_end = source_start
        later_valid = [pos for pos in valid_source_starts if pos > source_start]
        window_end = min(later_valid) if later_valid else len(line)
        if PLACEHOLDER not in line[source_start:window_end]:
            continue
        lemma_start = _choose_lemma_start(line, lemma_end, source_start, window_end)
        if lemma_start is None:
            continue
        lemma = line[lemma_start:lemma_end]
        if not lemma or PLACEHOLDER in lemma or _has_label_prefix(lemma):
            continue
        raw.append({
            "lemma_start": lemma_start,
            "source_start": source_start,
            "lemma": lemma,
        })

    # Multiple source cues can start inside one title, e.g. 後漢書 also
    # contains 漢書 and 書. Keep the earliest/longest effective candidate for
    # each source start.
    dedup: dict[int, dict] = {}
    for candidate in raw:
        existing = dedup.get(candidate["source_start"])
        if existing is None or len(candidate["lemma"]) > len(existing["lemma"]):
            dedup[candidate["source_start"]] = candidate
    return sorted(dedup.values(), key=lambda c: (c["lemma_start"], c["source_start"]))


def _source_cue_positions(line: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for cue in _SOURCE_CUES:
        start = 0
        while True:
            pos = line.find(cue, start)
            if pos < 0:
                break
            found.append((pos, cue))
            start = pos + 1
    found.sort(key=lambda p: (p[0], -len(p[1])))
    return found


def _next_source_start(line: str, after: int) -> int | None:
    starts = [pos for pos, _cue in _source_cue_positions(line) if pos >= after]
    return min(starts) if starts else None


def _choose_lemma_start(
    line: str,
    lemma_end: int,
    source_start: int,
    window_end: int,
) -> int | None:
    min_start = max(0, lemma_end - _MAX_LEMMA_LEN)
    label_start = _label_trim_start(line[:lemma_end])
    if label_start is not None and label_start >= min_start:
        return label_start

    boundary_start = _boundary_trim_start(line[:lemma_end])
    if boundary_start is not None:
        return boundary_start

    available = lemma_end - min_start
    if available <= 0:
        return None

    span = line[source_start:min(window_end, source_start + _LEMMA_HINT_WINDOW)]
    placeholder_count = span.count(PLACEHOLDER)
    max_run = _max_placeholder_run(span)
    if placeholder_count <= 0:
        return None

    lengths = list(range(1, min(_MAX_LEMMA_LEN, available) + 1))
    viable = [
        length for length in lengths
        if length >= max_run and placeholder_count % length == 0
    ]
    if viable:
        length = 2 if 2 in viable else min(viable)
    else:
        length = min(max(max_run, 1), available)
    return lemma_end - length


def _max_placeholder_run(text: str) -> int:
    best = cur = 0
    for ch in text:
        if ch == PLACEHOLDER:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _label_trim_start(prefix: str) -> int | None:
    best: int | None = None
    for label in _LABELS:
        pos = prefix.rfind(label)
        if pos >= 0:
            end = pos + len(label)
            if best is None or end > best:
                best = end
    return best


def _boundary_trim_start(prefix: str) -> int | None:
    floor = max(0, len(prefix) - _MAX_LEMMA_LEN)
    for pos in range(len(prefix) - 1, floor - 1, -1):
        if prefix[pos] in _LEMMA_BOUNDARY_CHARS and pos + 1 < len(prefix):
            return pos + 1
    return None


def _first_label_start(text: str) -> int | None:
    positions = [pos for label in _LABELS if (pos := text.find(label)) >= 0]
    return min(positions) if positions else None


def _has_label_prefix(text: str) -> bool:
    return any(text.startswith(label) for label in _LABELS)


def _is_lemma_char(ch: str) -> bool:
    return ch != PLACEHOLDER and is_allowed_body_char(ch)
