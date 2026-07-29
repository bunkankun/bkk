"""Derive ``voice`` markers from typed TLS segment runs.

The TLS importer folds consecutive ``<seg type="...">`` siblings into
``tls:seg-start`` / ``tls:seg-end`` marker pairs. Some TLS editions use
``type="root"`` and ``type="comm"`` to identify the primary text and its
commentary directly. This deriver turns those explicit run spans into BKK
``voice`` range markers.
"""

from __future__ import annotations

from .derive import VoiceDerivationProblem


TLS_SEG_VOICE_MAP: dict[str, str] = {
    "root": "root",
    "comm": "commentary",
}


def derive_voice_markers_from_tls_segments(
    text_len: int,
    markers: list[dict],
) -> list[dict]:
    """Return ``voice`` markers derived from typed TLS segment runs.

    Only ``tls:seg-start`` markers whose ``seg_type`` is in
    :data:`TLS_SEG_VOICE_MAP` participate. The next matching ``tls:seg-end``
    closes the range; if the end marker has no ``seg_type``, it is accepted as
    closing the active typed run. A zero-length run is ignored.
    """
    out, problems = derive_voice_markers_from_tls_segments_best_effort(
        text_len, markers,
    )
    if problems:
        raise problems[0]
    return out


def derive_voice_markers_from_tls_segments_best_effort(
    text_len: int,
    markers: list[dict],
) -> tuple[list[dict], list[VoiceDerivationProblem]]:
    """Return recoverable TLS segment voices and localized problems."""
    events: list[tuple[int, int, str, dict]] = []
    problems: list[VoiceDerivationProblem] = []
    for index, marker in enumerate(markers):
        if not isinstance(marker, dict):
            continue
        mtype = marker.get("type")
        if mtype not in {"tls:seg-start", "tls:seg-end"}:
            continue
        seg_type = marker.get("seg_type")
        if mtype == "tls:seg-start" and seg_type not in TLS_SEG_VOICE_MAP:
            continue
        offset = marker.get("offset")
        if not isinstance(offset, int):
            problems.append(VoiceDerivationProblem(
                "tls-seg-offset",
                f"{mtype} marker missing integer offset: {marker}",
                offset=0,
            ))
            continue
        if offset < 0 or offset > text_len:
            problems.append(VoiceDerivationProblem(
                "tls-seg-offset",
                f"{mtype} marker offset {offset} outside text length {text_len}",
                offset=offset,
            ))
            continue
        events.append((offset, index, mtype, marker))

    if not events:
        return [], problems

    events.sort(key=lambda event: (event[0], event[1]))
    active: tuple[int, str, str] | None = None
    spans: list[tuple[int, int, str]] = []
    for offset, _index, mtype, marker in events:
        if mtype == "tls:seg-start":
            seg_type = marker.get("seg_type")
            voice_name = TLS_SEG_VOICE_MAP.get(seg_type)
            if voice_name is None:
                continue
            if active is not None:
                start, active_seg_type, _active_name = active
                problems.append(VoiceDerivationProblem(
                    "tls-seg-unclosed",
                    f"tls:seg-start {active_seg_type!r} at offset {start} "
                    f"was not closed before offset {offset}",
                    offset=start,
                    length=max(0, offset - start),
                ))
            active = (offset, seg_type, voice_name)
            continue

        if active is None:
            seg_type = marker.get("seg_type")
            if seg_type not in TLS_SEG_VOICE_MAP:
                continue
            problems.append(VoiceDerivationProblem(
                "tls-seg-stray-end",
                f"unexpected tls:seg-end {seg_type!r} at offset {offset} "
                "with no matching tls:seg-start",
                offset=offset,
            ))
            continue

        start, active_seg_type, active_name = active
        seg_type = marker.get("seg_type")
        if seg_type is not None and seg_type != active_seg_type:
            problems.append(VoiceDerivationProblem(
                "tls-seg-mismatch",
                f"tls:seg-start {active_seg_type!r} at offset {start} "
                f"closed by tls:seg-end {seg_type!r} at offset {offset}",
                offset=start,
                length=max(0, offset - start),
            ))
            active = None
            continue
        if offset > start:
            spans.append((start, offset, active_name))
        active = None

    if active is not None:
        start, seg_type, _voice_name = active
        problems.append(VoiceDerivationProblem(
            "tls-seg-unclosed",
            f"tls:seg-start {seg_type!r} at offset {start} has no matching "
            "tls:seg-end",
            offset=start,
            length=max(0, text_len - start),
        ))

    counters: dict[str, int] = {}
    out: list[dict] = []
    last_root_id: str | None = None
    for start, end, name in spans:
        counters[name] = counters.get(name, 0) + 1
        marker_id = f"{_id_prefix(name)}{counters[name]}"
        marker: dict = {
            "type": "voice",
            "offset": start,
            "length": end - start,
            "name": name,
            "id": marker_id,
        }
        if name == "commentary" and last_root_id is not None:
            marker["responds-to"] = last_root_id
        out.append(marker)
        if name == "root":
            last_root_id = marker_id

    return out, problems


def _id_prefix(name: str) -> str:
    if name == "commentary":
        return "c"
    return name[:1] if name else "v"
