"""TLS / HXWD reader.

Parses the three TLS source files for one text into a :class:`Bundle`:
- ``tls-texts/<text-id>.xml`` — TEI body, divs/heads/segs/c-punctuation/pb.
- ``tls-data/swl/<text-id>-ann.xml`` — semantic-word-level annotations.
- ``tls-data/doc/<text-id>-ann.xml`` — document-level annotations.

The reader builds *section-local* text and markers per top-level ``<div>``
(offsets reset at section start). Buckets (front/body/back) and bucket-global
offsets are computed downstream.

Alongside the canonical Bundle, the reader also collects a ``source_info``
dict that captures everything the bundle drops but a future XML exporter will
need to round-trip back to TEI: full ``<teiHeader>`` tree, div/head/seg/pb
attributes, annotation provenance (swl vs doc), and per-annotation source
trees. ``source_info`` is written as a sidecar (``<text-id>.source.yaml``)
that is *not* part of the bundle hash chain — see write/bundle.py.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

from lxml import etree

from ..charset import is_allowed_body_char
from ..classify import _split_section_at
from ..ir import Annotation, Bundle, Juan, Marker, Section


TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"
TLS_NS = "http://hxwd.org/ns/1.0"
CB_NS = "http://www.cbeta.org/ns/1.0"

_NS = {"tei": TEI_NS, "xml": XML_NS, "tls": TLS_NS}

# Namespace URI -> short prefix used in source_info dumps. The TEI default
# namespace renders without a prefix; everything else gets a known prefix or
# falls back to Clark notation.
_NS_PREFIXES = {
    TEI_NS: "",
    XML_NS: "xml",
    TLS_NS: "tls",
    CB_NS: "cb",
}

_XML_ELEMENT_MARKER_EXCLUDED_NAMES = {
    "pb", "lb", "head", "seg", "tls:head", "tls:seg",
}


def _q(local: str, ns: str = TEI_NS) -> str:
    return f"{{{ns}}}{local}"


def _xmlid(el) -> str:
    return el.get(_q("id", XML_NS), "")


def _dedup_id(mid: str, seen_ids: dict[str, int]) -> tuple[str, dict]:
    """Return (canonical_id, extras) for a marker.

    First occurrence of ``mid`` is returned unchanged with empty extras.
    Subsequent occurrences get a ``_dup{n}`` suffix and an extras entry
    flagging the problem so the output YAML records the source defect.
    """
    if not mid:
        return mid, {}
    if mid not in seen_ids:
        seen_ids[mid] = 1
        return mid, {}
    seen_ids[mid] += 1
    return f"{mid}_dup{seen_ids[mid]}", {
        "_xml_error": "duplicate-id",
        "_xml_original_id": mid,
    }


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x4E00  <= cp <= 0x9FFF   or  # CJK Unified Ideographs
        0x3400  <= cp <= 0x4DBF   or  # CJK Ext A
        0x20000 <= cp <= 0x2A6DF  or  # CJK Ext B
        0x2A700 <= cp <= 0x2EBEF  or  # CJK Ext C–F
        0xF900  <= cp <= 0xFAFF       # CJK Compatibility
    )


def _cjk_only(s: str) -> str:
    """Strip non-CJK characters from a TLS head string used as a TOC label.

    TLS heads occasionally pick up whitespace, punctuation, or other debris
    from mixed-content ``<seg>`` bodies; the TOC label invariant is that it
    contain only CJK ideographs.
    """
    return "".join(ch for ch in s if _is_cjk(ch))


def _qname_to_str(qname: str) -> str:
    """Convert ``{namespace}local`` to ``prefix:local`` (or just ``local`` for
    the TEI default namespace). Falls back to Clark notation for unknown
    namespaces so nothing is lost."""
    if not qname.startswith("{"):
        return qname
    ns, local = qname[1:].split("}", 1)
    prefix = _NS_PREFIXES.get(ns)
    if prefix is None:
        return f"{{{ns}}}{local}"
    if prefix == "":
        return local
    return f"{prefix}:{local}"


def _attrs_to_dict(attrib) -> dict:
    """Return a plain dict of an element's attributes with namespace-prefixed
    keys (``xml:id``, ``tls:foo``). Preserves source order."""
    out: dict = {}
    for k, v in attrib.items():
        out[_qname_to_str(k)] = v
    return out


def normalize_xml_element_names(raw) -> set[str]:
    """Return normalized element names configured for ``xml-element`` markers."""
    if raw is None:
        return set()
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, (list, tuple, set)):
        return set()
    names = {str(item).strip() for item in raw if str(item).strip()}
    return names - _XML_ELEMENT_MARKER_EXCLUDED_NAMES


def _xml_element_name(el) -> str:
    return _qname_to_str(el.tag)


def _xml_element_is_registered(el, xml_elements: set[str]) -> bool:
    if not xml_elements:
        return False
    name = _xml_element_name(el)
    local = etree.QName(el).localname
    return name in xml_elements or local in xml_elements


def _xml_element_marker(el, offset: int, role: str, *,
                        seen_ids: dict[str, int]) -> Marker:
    raw_id = _xmlid(el)
    mid, id_extras = _dedup_id(
        raw_id if role == "open" else f"{raw_id}_end" if raw_id else "",
        seen_ids,
    )
    extras = {"name": _xml_element_name(el), "role": role}
    attrs = _attrs_to_dict(el.attrib)
    if attrs:
        extras["attrs"] = attrs
    extras.update(id_extras)
    return Marker(type="xml-element", offset=offset, id=mid, extras=extras)


def _to_tree(elem) -> dict:
    """Recursively convert an lxml element to a serializable dict tree.

    Shape::

        {tag: <prefix:local>, attrs: {...}, text: "...", tail: "...",
         children: [...]}

    Empty fields are omitted to keep the YAML output compact. Whitespace-only
    ``text`` and ``tail`` fragments (XML pretty-printing artifacts) are
    dropped — they don't affect the bundle, and the exporter will re-emit
    its own formatting whitespace. Comments and processing instructions are
    skipped (they don't appear in TLS sources).
    """
    node: dict = {"tag": _qname_to_str(elem.tag)}
    attrs = _attrs_to_dict(elem.attrib)
    if attrs:
        node["attrs"] = attrs
    if elem.text and elem.text.strip():
        node["text"] = elem.text
    children: list[dict] = []
    for child in elem.iterchildren():
        if isinstance(child.tag, str):  # skip comments / PIs
            children.append(_to_tree(child))
    if children:
        node["children"] = children
    if elem.tail and elem.tail.strip():
        node["tail"] = elem.tail
    return node


def read_tls(text_xml: Path, swl_ann: Path | None, doc_ann: Path | None,
             text_id: str, *,
             source_xml: Path | None = None,
             source_swl: Path | None = None,
             source_doc: Path | None = None,
             xml_elements=None) -> Bundle:
    """Read one TLS text and return its Bundle.

    ``swl_ann`` and ``doc_ann`` are optional; when present they are concatenated
    in that order to preserve provenance ordering.

    The edition short id (e.g. ``T``, ``tls``) is derived from the second
    component of the marker ids, which follow ``<text-id>_<edition>_<location>``
    in both the Kanripo and TLS corpora.
    """
    (sections, divs_info, markers_info, tei_info, parse_errors,
     flavor) = _parse_text(text_xml, text_id, xml_elements=xml_elements)
    if parse_errors:
        print(f"warning: {text_id}: {len(parse_errors)} XML error(s) in "
              f"{text_xml.name}; continuing with recovery",
              file=sys.stderr)

    annotations: list[Annotation] = []
    annotations_info: dict = {}
    ann_files_info: dict = {}
    for path, role in ((swl_ann, "swl"), (doc_ann, "doc")):
        if path is None or not path.exists():
            continue
        try:
            anns, ann_info, envelope = _parse_annotations(path, role)
            annotations.extend(anns)
            annotations_info.update(ann_info)
            ann_files_info[role] = envelope
        except Exception as exc:  # noqa: BLE001
            print(f"warning: {text_id}: skipping {role} ann ({path.name}): {exc}",
                  file=sys.stderr)

    # Normalize short juan labels in marker ids to JUAN_LABEL_WIDTH digits so
    # downstream identifiers conform to spec. No-op when source labels are
    # already 3+ digits (KR6q0053 etc.); rewrites e.g. KR1a0171's `_01-` to
    # `_001-`. Run before edition derivation and juan splitting so they see
    # canonical labels. The exporter therefore emits canonical ids — a known
    # round-trip divergence for sources that used short labels.
    _normalize_juan_label_width(
        sections, divs_info, markers_info, annotations, annotations_info,
        text_id,
    )

    metadata = _parse_metadata(text_xml, text_id)
    krp_id = metadata["identifiers"]["krp"]
    edition_short = _derive_edition_short(sections, text_id)

    source_info: dict = {
        "text_id": krp_id,
        "format": "tls-cbeta" if flavor == "cbeta" else "tls",
        "format_version": 1,
        "source_files": _source_files(
            source_xml or text_xml,
            source_swl or swl_ann,
            source_doc or doc_ann,
        ),
        "tei": tei_info,
        "divs": divs_info,
        "markers": markers_info,
        "ann_files": ann_files_info,
        "annotations": annotations_info,
    }
    if parse_errors:
        source_info["parse_errors"] = parse_errors

    # CBETA juan grouping is computed *after* marker-id normalization so the
    # split Section copies returned in the groups carry canonical ids.
    juan_groups = (
        _split_sections_into_cbeta_juans(sections) if flavor == "cbeta" else None
    )
    juans = _build_juans(
        sections, annotations, text_id, flavor=flavor, juan_groups=juan_groups,
    )
    source = {"repository": "tls-texts", "path": f"data/tls/{text_id}.xml"}
    metadata.setdefault("source", source)
    return Bundle(
        text_id=krp_id,
        juans=juans,
        metadata=metadata,
        edition_short=edition_short,
        source=source,
        source_info=source_info,
    )


def _build_juans(
    sections: list[Section], annotations: list[Annotation], text_id: str,
    *, flavor: str = "classic",
    juan_groups: list[tuple[str, list[Section]]] | None = None,
) -> list[Juan]:
    """Group ``sections`` into per-juan :class:`Juan` objects and partition
    ``annotations`` by which juan contains their seg.

    When ``juan_groups`` is provided (CBETA flavor), it is used verbatim;
    otherwise classic boundary-by-marker-id splitting runs over ``sections``.
    """
    groups = juan_groups if juan_groups is not None else (
        _split_sections_into_juans(sections, text_id)
    )
    if not groups:
        return [Juan(seq=1, sections=[], annotations=list(annotations))]

    labels = [lbl for lbl, _ in groups]
    if all(lbl.isdigit() for lbl in labels):
        seqs = [int(lbl) for lbl in labels]
    else:
        seqs = list(range(1, len(labels) + 1))

    seg_to_idx: dict[str, int] = {}
    for idx, (_, secs) in enumerate(groups):
        for sec in secs:
            for m in sec.markers:
                if m.type in ("tls:seg", "tls:head") and m.id:
                    seg_to_idx[m.id] = idx

    buckets: list[list[Annotation]] = [[] for _ in groups]
    for ann in annotations:
        idx = seg_to_idx.get(ann.seg_id)
        if idx is not None:
            buckets[idx].append(ann)

    juans: list[Juan] = []
    for i, (label, secs) in enumerate(groups):
        seq = seqs[i]
        metadata: dict = {}
        if flavor == "cbeta":
            metadata["flavor"] = "cbeta"
            # Force pre-juan content (the ``_000`` group) into the front
            # bucket regardless of head-text heuristics. Subsequent juans
            # follow the default classifier.
            if label == "000":
                secs = [_with_section_bucket(s, "front") for s in secs]
            # Capture juan-level metadata from the first cbeta:juan-start
            # marker found in this group, if any.
            for sec in secs:
                jstart = next(
                    (m for m in sec.markers if m.type == "cbeta:juan-start"),
                    None,
                )
                if jstart is not None:
                    if jstart.extras.get("jhead"):
                        metadata["juan_label"] = jstart.extras["jhead"]
                    metadata["juan_marker_id"] = jstart.id
                    break
        juans.append(Juan(
            seq=seq, sections=secs, annotations=buckets[i], metadata=metadata,
        ))
    return juans


def _with_section_bucket(section: Section, bucket: str) -> Section:
    return Section(
        head_text=section.head_text,
        head_marker_id=section.head_marker_id,
        text=section.text,
        markers=list(section.markers),
        bucket=bucket,
    )


_LABEL_BEARING_MARKERS = ("page-break", "tls:head", "tls:seg")

# Canonical width for the juan-label component of a marker id (the digits
# between the second underscore and the first hyphen of the location). Some
# TLS sources (e.g. KR1a0171) use 1- or 2-digit labels; we pad on import so
# downstream identifiers conform to spec. Re-export will reflect the padded
# form — a deliberate, documented round-trip divergence.
JUAN_LABEL_WIDTH = 3


def _normalize_marker_id(mid: str, text_id: str,
                         width: int = JUAN_LABEL_WIDTH) -> str:
    """Pad a short juan label inside ``mid`` to ``width`` digits.

    Returns the original string unchanged if it doesn't match the
    ``<text-id>_<edition>_<digits>-<rest>`` shape, if the label is already
    at least ``width`` digits, or if the label isn't all-digits.
    """
    if not mid:
        return mid
    prefix = f"{text_id}_"
    if not mid.startswith(prefix):
        return mid
    rest = mid[len(prefix):]
    parts = rest.split("_", 1)
    if len(parts) < 2:
        return mid
    edition, location = parts
    label, sep, tail = location.partition("-")
    if not sep or not label.isdigit() or len(label) >= width:
        return mid
    return f"{prefix}{edition}_{label.zfill(width)}-{tail}"


def _normalize_juan_label_width(
    sections: list[Section], divs_info: dict, markers_info: dict,
    annotations: list[Annotation], annotations_info: dict, text_id: str,
    width: int = JUAN_LABEL_WIDTH,
) -> None:
    """Pad short juan labels (1-2 digits) in every marker id to ``width``.

    Mutates ``sections``, ``divs_info``, ``markers_info``, ``annotations``,
    and ``annotations_info`` in place. No-op for sources whose marker ids
    already use the canonical 3-digit form.
    """
    def fix(s: str) -> str:
        return _normalize_marker_id(s, text_id, width)

    for sec in sections:
        sec.head_marker_id = fix(sec.head_marker_id)
        for m in sec.markers:
            if m.id:
                m.id = fix(m.id)

    for d in (divs_info, markers_info):
        for old_key in list(d.keys()):
            new_key = fix(old_key)
            if new_key != old_key:
                d[new_key] = d.pop(old_key)

    for ann in annotations:
        ann.seg_id = fix(ann.seg_id)

    # Annotation entries carry a seg_id pointing back to the body's <seg>;
    # rewrite that too. The dict's own keys are annotation @xml:ids which
    # don't follow the juan-label pattern, so they're left alone (the
    # normalizer is a no-op when the pattern doesn't match).
    for entry in annotations_info.values():
        if isinstance(entry, dict) and "seg_id" in entry:
            entry["seg_id"] = fix(entry["seg_id"])


def _juan_label_from_marker_id(mid: str, text_id: str,
                               expected_edition: str | None = None) -> str | None:
    """Extract the juan label from a TLS marker id.

    Marker ids have the form ``<text-id>_<edition>_<location>`` and the juan
    label is the part of ``<location>`` before the first ``-`` (e.g.
    ``KR6q0053_T_001-0495a.4-h`` → ``"001"``). Returns ``None`` if ``mid``
    doesn't match the expected shape.

    When ``expected_edition`` is given, marker ids from any other edition
    return ``None`` — used to lock juan detection to the base edition so
    interleaved markers from variant editions can't synthesise spurious
    juan boundaries.
    """
    if not mid:
        return None
    prefix = f"{text_id}_"
    if not mid.startswith(prefix):
        return None
    rest = mid[len(prefix):]
    parts = rest.split("_", 1)
    if len(parts) < 2:
        return None
    edition, location = parts
    if expected_edition is not None and edition != expected_edition:
        return None
    label, _, _ = location.partition("-")
    return label or None


def _base_edition_from_segs(sections: list[Section],
                            text_id: str) -> str | None:
    """Edition of the first ``tls:seg`` marker in document order.

    This pins juan detection to the source's main edition. Returns ``None``
    if no parseable seg id is found, in which case the splitter falls back
    to its un-filtered behaviour.
    """
    prefix = f"{text_id}_"
    for sec in sections:
        for m in sec.markers:
            if m.type != "tls:seg":
                continue
            mid = m.id or ""
            if not mid.startswith(prefix):
                continue
            edition, sep, _ = mid[len(prefix):].partition("_")
            if sep and edition:
                return edition
    return None


def _split_sections_into_juans(
    sections: list[Section], text_id: str,
) -> list[tuple[str, list[Section]]]:
    """Group ``sections`` into juans by walking id-bearing markers in order.

    Only markers whose edition matches the base edition (the edition of the
    first ``tls:seg`` in document order) participate in boundary detection;
    markers from variant editions are ignored even if they encode a different
    juan. Sections whose markers all share one juan label go in whole;
    sections that straddle a juan boundary are split via
    :func:`_split_section_at` so each piece lands in the correct juan.
    Sections with no id-bearing markers inherit the previous juan label (or
    default to ``"001"`` if none has been seen yet).
    """
    base_edition = _base_edition_from_segs(sections, text_id)
    groups: list[tuple[str, list[Section]]] = []
    running_label: str | None = None

    def append(label: str, sec: Section) -> None:
        if groups and groups[-1][0] == label:
            groups[-1][1].append(sec)
        else:
            groups.append((label, [sec]))

    for sec in sections:
        # Boundaries are *internal* label changes in this section. The
        # section's entry label comes from its first id-bearing marker (or
        # is inherited if there are none) — never from the running label,
        # so a between-section transition doesn't synthesise an offset-0
        # boundary.
        first_label: str | None = None
        last_seen: str | None = None
        boundaries: list[tuple[int, int, str]] = []  # (offset, orig_idx, label)
        marker_pos = {id(m): i for i, m in enumerate(sec.markers)}
        for m in sorted(sec.markers, key=lambda x: x.offset):
            if m.type not in _LABEL_BEARING_MARKERS:
                continue
            label = _juan_label_from_marker_id(m.id, text_id, base_edition)
            if label is None:
                continue
            if first_label is None:
                first_label = label
                last_seen = label
            elif label != last_seen:
                boundaries.append((m.offset, marker_pos[id(m)], label))
                last_seen = label

        entry_label = first_label or running_label or "001"
        if not boundaries:
            append(entry_label, sec)
            running_label = entry_label
            continue

        cursor_label = entry_label
        head = sec
        accumulated = 0
        front_count = 0
        for offset, orig_idx, new_label in boundaries:
            # Pass split_marker_index so markers before the boundary marker
            # (e.g. trailing punctuation) stay in the front (current juan).
            front, head = _split_section_at(
                head, offset - accumulated, orig_idx - front_count,
            )
            append(cursor_label, front)
            accumulated += len(front.text)
            front_count += len(front.markers)
            cursor_label = new_label
        append(cursor_label, head)
        running_label = cursor_label

    return groups


def _split_sections_into_cbeta_juans(
    sections: list[Section],
) -> list[tuple[str, list[Section]]]:
    """Split ``sections`` at every ``cbeta:juan-start`` marker.

    Pre-juan content (whatever appears before the first juan-start) is
    grouped under label ``"000"``. Each subsequent group is keyed by the
    ``juan_n`` extras of the boundary marker. Splitting is done by marker
    *index* (not offset) so a juan-start at offset 0 still pulls in the
    juan-start marker itself, while leaving any same-offset markers (e.g. a
    pre-juan ``cbeta:mulu``) in the previous group.
    """
    groups: list[tuple[str, list[Section]]] = []
    current_label = "000"

    def append(label: str, sec: Section) -> None:
        if not sec.text and not sec.markers:
            return
        if groups and groups[-1][0] == label:
            groups[-1][1].append(sec)
        else:
            groups.append((label, [sec]))

    for sec in sections:
        boundaries = [
            (i, m.extras.get("juan_n", "001"))
            for i, m in enumerate(sec.markers)
            if m.type == "cbeta:juan-start"
        ]
        if not boundaries:
            append(current_label, sec)
            continue

        head = sec
        consumed_markers = 0
        cursor_label = current_label
        for marker_idx, new_label in boundaries:
            local_idx = marker_idx - consumed_markers
            front, head = _split_section_at_marker_index(head, local_idx)
            append(cursor_label, front)
            consumed_markers = marker_idx
            cursor_label = new_label
        append(cursor_label, head)
        current_label = cursor_label

    return groups


def _split_section_at_marker_index(
    section: Section, idx: int,
) -> tuple[Section, Section]:
    """Split ``section`` so ``markers[:idx]`` go to the front and
    ``markers[idx:]`` to the back. Text is split at ``markers[idx].offset``.

    Marker-index splitting (vs. offset-based ``_split_section_at``) lets us
    preserve the *order* of markers at the same offset — necessary when a
    pre-juan ``cbeta:mulu`` and its trailing ``cbeta:juan-start`` both sit
    at offset 0 of a section: only the juan-start (and everything after)
    must go to the new juan.
    """
    if idx <= 0:
        empty = Section(
            head_text=section.head_text,
            head_marker_id=section.head_marker_id,
            text="",
            markers=[],
        )
        return empty, section
    if idx >= len(section.markers):
        empty = Section(
            head_text=section.head_text,
            head_marker_id=section.head_marker_id,
            text="",
            markers=[],
        )
        return section, empty

    split_offset = section.markers[idx].offset
    front_text = section.text[:split_offset]
    back_text = section.text[split_offset:]
    front_markers = list(section.markers[:idx])
    back_markers = [
        Marker(type=m.type, offset=m.offset - split_offset,
               content=m.content, id=m.id, extras=dict(m.extras))
        for m in section.markers[idx:]
    ]
    front = Section(
        head_text=section.head_text,
        head_marker_id=section.head_marker_id,
        text=front_text,
        markers=front_markers,
    )
    back = Section(
        head_text=section.head_text,
        head_marker_id=section.head_marker_id,
        text=back_text,
        markers=back_markers,
    )
    return front, back


def _source_files(text_xml: Path, swl_ann: Path | None,
                  doc_ann: Path | None) -> dict:
    out: dict = {"text": str(text_xml)}
    if swl_ann is not None and swl_ann.exists():
        out["swl_ann"] = str(swl_ann)
    if doc_ann is not None and doc_ann.exists():
        out["doc_ann"] = str(doc_ann)
    return out


def _derive_edition_short(sections: list, text_id: str) -> str:
    """Pull the edition id (middle component) from any marker id.

    Marker ids have the form ``<text-id>_<edition>_<location>`` (e.g.
    ``KR6q0053_T_001-0495a.4-h``, ``KR1f0001_tls_001-1a.3-h``). We look at
    each section's markers in order and return the first edition id we can
    parse; fall back to ``"T"`` if nothing matches.
    """
    prefix = f"{text_id}_"
    for sec in sections:
        for m in sec.markers:
            mid = getattr(m, "id", "") or ""
            if not mid.startswith(prefix):
                continue
            rest = mid[len(prefix):]
            edition, _, _ = rest.partition("_")
            if edition:
                return edition
    return "T"


def _parse_text(
    path: Path, text_id: str, *, xml_elements=None,
) -> tuple[list[Section], dict, dict, dict, list[dict], str]:
    """Parse the text XML.

    Returns ``(sections, divs_info, markers_info, tei_info, parse_errors,
    flavor)``:
    - ``sections``: ordered list of Sections used by the bundle pipeline.
    - ``divs_info``: source attrs per top-level div, keyed by head_marker_id.
    - ``markers_info``: source attrs per id-bearing marker (pb, head outer,
      seg), keyed by the marker's id. Markers without stable ids
      (paragraph-break, punctuation) are not included; nothing extra needs
      to be carried for them.
    - ``tei_info``: ``{root_attrs, header}`` capturing the ``<TEI>`` element's
      attributes and the full ``<teiHeader>`` tree.
    - ``parse_errors``: list of ``{level, message, line}`` dicts from the
      parser's error log (non-empty only when the source had XML defects
      such as empty ``xml:id`` values or duplicate ids).
    - ``flavor``: ``"classic"`` (top-level divs only) or ``"cbeta"``
      (explicit ``<juan fun="open"/>`` boundaries).

    For CBETA flavor, the caller is expected to derive juan groups from the
    returned ``sections`` via :func:`_split_sections_into_cbeta_juans` —
    *after* :func:`_normalize_juan_label_width` has run, so the split
    Section copies don't fall out of sync with the canonical marker ids.
    """
    parser = etree.XMLParser(recover=True)
    tree = etree.parse(str(path), parser)
    parse_errors = [
        {"level": e.level_name, "message": e.message, "line": e.line}
        for e in parser.error_log
    ]
    root = tree.getroot()
    body = tree.find(f".//{_q('body')}")
    if body is None:
        raise ValueError(f"no <body> element in {path}")

    flavor = "cbeta" if body.find(f".//{_q('juan')}") is not None else "classic"

    seen_ids: dict[str, int] = {}
    xml_element_names = normalize_xml_element_names(xml_elements)
    if flavor == "cbeta":
        edition = _scan_edition_from_body(body, text_id) or "CBETA"
        sections, divs_info, markers_info = _parse_body_cbeta(
            body, seen_ids, text_id, edition, xml_element_names,
        )
    else:
        sections, divs_info, markers_info = _parse_body_classic(
            body, seen_ids, xml_element_names,
        )

    tei_info: dict = {}
    if etree.QName(root).localname == "TEI":
        root_attrs = _attrs_to_dict(root.attrib)
        if root_attrs:
            tei_info["root_attrs"] = root_attrs
    header_el = tree.find(f".//{_q('teiHeader')}")
    if header_el is not None:
        tei_info["header"] = _to_tree(header_el)

    return sections, divs_info, markers_info, tei_info, parse_errors, flavor


def _parse_body_classic(
    body, seen_ids: dict[str, int], xml_elements: set[str] | None = None,
) -> tuple[
    list[Section], dict, dict,
]:
    """Walk a classic-TLS ``<body>``: each top-level ``<div>`` becomes one
    Section, with juan boundaries derived later from marker ids."""
    sections: list[Section] = []
    divs_info: dict = {}
    markers_info: dict = {}
    for div in body.iterfind(_q("div")):
        section, juan_entry, marker_entries, nested_div_entries = (
            _section_from_div(div, seen_ids, xml_elements or set())
        )
        sections.append(section)
        if section.head_marker_id:
            divs_info[section.head_marker_id] = juan_entry
        for mid, info in marker_entries.items():
            markers_info[mid] = info
        # Nested divs contribute their own divs_info entries, keyed by each
        # nested div's head id (matches the id on the surrounding
        # tls:div-start / tls:div-end markers).
        for nested_id, nested_entry in nested_div_entries.items():
            divs_info[nested_id] = nested_entry
    return sections, divs_info, markers_info


def _scan_edition_from_body(body, text_id: str) -> str | None:
    """Find the edition short id from the first ``<seg xml:id=...>`` in body.

    Returns ``None`` if no parseable seg id is found. Used to seed CBETA
    parsing before the Section list (which `_derive_edition_short` would
    otherwise key off) has been built.
    """
    prefix = f"{text_id}_"
    for seg in body.iter(_q("seg")):
        sid = _xmlid(seg)
        if not sid.startswith(prefix):
            continue
        edition, sep, _ = sid[len(prefix):].partition("_")
        if sep and edition:
            return edition
    return None


def _normalize_juan_n(raw: str) -> str:
    """Pad a numeric juan ``n`` to JUAN_LABEL_WIDTH digits; pass non-numeric
    values through unchanged. Empty string falls back to ``"001"``."""
    if not raw:
        return "001"
    if raw.isdigit() and len(raw) < JUAN_LABEL_WIDTH:
        return raw.zfill(JUAN_LABEL_WIDTH)
    return raw


def _parse_body_cbeta(
    body, seen_ids: dict[str, int], text_id: str, edition: str,
    xml_elements: set[str] | None = None,
) -> tuple[list[Section], dict, dict]:
    """Walk a CBETA-flavor ``<body>``.

    Top-level non-div children (``<milestone>``, ``<pb>``, ``<lb>``) appearing
    before the first div are stashed as leading markers and re-emitted at
    offset 0 of the first opened section so they aren't lost. Each ``<div>``
    becomes one Section, walked permissively to recognise CBETA-specific
    elements (``<juan>``, ``<mulu>``, ``<byline>``, ``<dialog>/<sp>``, …) on
    top of the classic vocabulary.
    """
    sections: list[Section] = []
    divs_info: dict = {}
    markers_info: dict = {}
    state: dict = {
        "juan_label": "000",   # current juan label; updated on <juan fun=open>
        "mulu_indexes": {},     # juan_label -> next 1-based mulu index
        "text_id": text_id,
        "edition": edition,
        "xml_elements": xml_elements or set(),
    }
    leading_markers: list[Marker] = []
    leading_markers_info: dict = {}

    for child in body.iterchildren():
        if not isinstance(child.tag, str):
            continue
        tag = etree.QName(child).localname
        if tag == "div":
            section, div_entry, marker_entries, nested_entries = (
                _section_from_div_cbeta(
                    child, seen_ids, state,
                    leading_markers or None,
                )
            )
            # Body-level non-div markers (pb, lb) accumulated since the last
            # div are consumed by this section at offset 0, then cleared so
            # the next section starts fresh.
            for mid, info in leading_markers_info.items():
                markers_info[mid] = info
            leading_markers = []
            leading_markers_info = {}
            sections.append(section)
            if section.head_marker_id:
                divs_info[section.head_marker_id] = div_entry
            for mid, info in marker_entries.items():
                markers_info[mid] = info
            for nested_id, nested_entry in nested_entries.items():
                divs_info[nested_id] = nested_entry
        elif tag == "pb":
            raw_id = _xmlid(child)
            mid, id_extras = _dedup_id(raw_id, seen_ids)
            leading_markers.append(Marker(
                type="page-break", offset=0,
                content="", id=mid, extras=id_extras,
            ))
            attrs = _attrs_minus(child, ("xml:id",))
            if mid and attrs:
                leading_markers_info[mid] = {"type": "page-break", "attrs": attrs}
        elif tag == "lb":
            _emit_lb(child, leading_markers, lambda: 0, seen_ids)
        # <milestone> and other top-level non-div children are ignored —
        # they're whitespace/layout artifacts in CBETA sources.

    return sections, divs_info, markers_info


def _section_from_div_cbeta(
    div, seen_ids: dict[str, int], state: dict,
    leading_markers: list[Marker] | None,
) -> tuple[Section, dict, dict, dict]:
    """Walk a CBETA-flavor ``<div>``, producing a Section plus div_entry,
    markers_info, and nested_divs_info. ``state`` carries the rolling juan
    label updated by ``<juan fun="open">`` markers. ``leading_markers``, if
    given, are prepended to this section's markers (used to attach pre-div
    body-level pb's to the first section opened)."""
    text_buf: list[str] = []
    markers: list[Marker] = []
    if leading_markers:
        markers.extend(leading_markers)
    markers_info: dict = {}
    nested_divs_info: dict = {}

    div_entry: dict = {}
    div_attrs = _attrs_to_dict(div.attrib)
    if div_attrs:
        div_entry["div_attrs"] = div_attrs

    head_state = {"text": "", "id": ""}

    def offset() -> int:
        return sum(len(p) for p in text_buf)

    _walk_cbeta_div_children(
        div, text_buf, markers, markers_info, nested_divs_info,
        offset, div_entry, head_state, seen_ids, state, is_outermost=True,
    )

    section = Section(
        head_text=head_state["text"],
        head_marker_id=head_state["id"],
        text="".join(text_buf),
        markers=markers,
    )
    return section, div_entry, markers_info, nested_divs_info


def _emit_lb(child, markers: list[Marker], offset_fn,
             seen_ids: dict[str, int]) -> None:
    """Emit a ``line-break`` marker for a ``<lb/>`` element. The id is
    synthesized from ``ed`` + ``n`` (the only identifying attrs on TLS lb's,
    which never carry xml:id). Empty when both are missing."""
    ed = child.get("ed", "")
    n = child.get("n", "")
    raw_id = f"{ed}_{n}" if (ed or n) else ""
    lb_id, id_extras = _dedup_id(raw_id, seen_ids)
    markers.append(Marker(
        type="line-break", offset=offset_fn(),
        content="", id=lb_id, extras=id_extras,
    ))


def _walk_cbeta_div_children(
    div, text_buf: list[str], markers: list[Marker],
    markers_info: dict, nested_divs_info: dict,
    offset, div_entry: dict, head_state: dict,
    seen_ids: dict[str, int], state: dict, is_outermost: bool,
    depth: int = 1,
):
    """Permissive walker for CBETA-flavor div content.

    Handles classic elements (``<head>``, ``<p>``, nested ``<div>``, ``<pb>``)
    plus CBETA-specific ones (``<mulu>``, ``<juan>``, ``<byline>``,
    ``<dialog>``/``<sp>``, …). Unknown container elements are descended into;
    bare ``<seg>`` and ``<pb>`` that turn up at any depth are emitted via the
    usual machinery.

    Maintains a run-state for div-level ``<seg>`` siblings (rare in CBETA
    proper but common inside ``<dialog>``/``<sp>`` recursions). The state is
    closed at every non-seg sibling, so it never spans a structural boundary
    like ``<p>`` (which has its own per-paragraph run state).
    """
    text_id = state["text_id"]
    edition = state["edition"]
    div_run_state = _new_seg_run_state(seen_ids)
    for child in div.iterchildren():
        if not isinstance(child.tag, str):
            continue
        tag = etree.QName(child).localname
        registered_xml_element = _xml_element_is_registered(
            child, state.get("xml_elements", set()),
        )
        if registered_xml_element:
            markers.append(_xml_element_marker(
                child, offset(), "open", seen_ids=seen_ids,
            ))
        # Close any open div-level seg run before non-seg/non-pb/non-lb
        # siblings. <pb> and <lb> alone do not break a run; segs continue
        # or extend it.
        if tag not in ("seg", "pb", "lb"):
            _close_seg_run(div_run_state, markers, offset)
        if tag == "pb":
            raw_id = _xmlid(child)
            mid, id_extras = _dedup_id(raw_id, seen_ids)
            markers.append(Marker(type="page-break", offset=offset(),
                                  content="", id=mid, extras=id_extras))
            attrs = _attrs_minus(child, ("xml:id",))
            if mid and attrs:
                markers_info[mid] = {"type": "page-break", "attrs": attrs}
        elif tag == "head":
            inner_seg = child.find(_q("seg"))
            raw_id = _xmlid(inner_seg) if inner_seg is not None else _xmlid(child)
            seg_id, id_extras = _dedup_id(raw_id, seen_ids)
            markers.append(Marker(type="tls:head", offset=offset(),
                                  content="", id=seg_id, extras=id_extras))
            head_text_start = offset()
            if inner_seg is not None:
                head_sink = _make_sink(text_buf, markers, offset,
                                       markers_info, seen_ids, in_head=True,
                                       xml_elements=state.get("xml_elements"))
                _walk_seg_inline(inner_seg, head_sink)
            head_text_str = "".join(text_buf)[head_text_start:]
            if "head_attrs" not in div_entry:
                head_attrs = _attrs_to_dict(child.attrib)
                if head_attrs:
                    div_entry["head_attrs"] = head_attrs
                if inner_seg is not None:
                    inner_extras = _attrs_minus(inner_seg, ("xml:id",))
                    if inner_extras:
                        div_entry["head_inner_seg_attrs"] = inner_extras
            if is_outermost and not head_state["text"]:
                head_state["text"] = _cjk_only(head_text_str)
                head_state["id"] = seg_id
        elif tag == "mulu":
            mulu_text = (child.text or "").strip()
            if not mulu_text:
                # Empty <mulu type="卷" n="N"/> inside <juan>: not a TOC entry
                # on its own; the surrounding <juan fun="open"> handles it.
                continue
            juan_label = state["juan_label"]
            idx_map = state["mulu_indexes"]
            idx_map[juan_label] = idx_map.get(juan_label, 0) + 1
            mid = f"{text_id}_{edition}_{juan_label}-mulu-{idx_map[juan_label]}"
            extras: dict = {}
            mulu_type = child.get("type")
            if mulu_type:
                extras["mulu_type"] = mulu_type
            level = child.get("level")
            if level:
                extras["level"] = level
            markers.append(Marker(
                type="cbeta:mulu", offset=offset(),
                content=unicodedata.normalize("NFC", mulu_text),
                id=mid, extras=extras,
            ))
            attrs = _attrs_to_dict(child.attrib)
            if attrs:
                markers_info[mid] = {"type": "cbeta:mulu", "attrs": attrs}
        elif tag == "juan":
            fun = child.get("fun", "")
            n_raw = child.get("n", "")
            n = _normalize_juan_n(n_raw)
            jhead_el = child.find(_q("jhead"))
            jhead_text = ""
            if jhead_el is not None and jhead_el.text:
                jhead_text = unicodedata.normalize("NFC", jhead_el.text.strip())
            if fun == "open":
                state["juan_label"] = n
                mid = f"{text_id}_{edition}_{n}-juan-start"
                extras: dict = {"juan_n": n}
                if jhead_text:
                    extras["jhead"] = jhead_text
                markers.append(Marker(
                    type="cbeta:juan-start", offset=offset(),
                    content=jhead_text, id=mid, extras=extras,
                ))
                attrs = _attrs_to_dict(child.attrib)
                if attrs:
                    markers_info[mid] = {"type": "cbeta:juan-start", "attrs": attrs}
            elif fun == "close":
                mid = f"{text_id}_{edition}_{n}-juan-end"
                extras = {"juan_n": n}
                markers.append(Marker(
                    type="cbeta:juan-end", offset=offset(),
                    content=jhead_text, id=mid, extras=extras,
                ))
                attrs = _attrs_to_dict(child.attrib)
                if attrs:
                    markers_info[mid] = {"type": "cbeta:juan-end", "attrs": attrs}
        elif tag == "p":
            p_xmlid = _xmlid(child)
            if not registered_xml_element:
                p_open_id, p_open_extras = _dedup_id(p_xmlid, seen_ids)
                open_extras = dict(p_open_extras)
                open_extras["role"] = "open"
                markers.append(Marker(type="paragraph-break", offset=offset(),
                                      content="", id=p_open_id, extras=open_extras))
            p_attrs = _attrs_to_dict(child.attrib)
            if p_attrs:
                div_entry.setdefault("p_attrs", []).append(p_attrs)
            _walk_cbeta_inline_children(
                child, text_buf, markers, markers_info, offset, seen_ids,
                state.get("xml_elements", set()),
            )
            p_close_raw = f"{p_xmlid}_end" if p_xmlid else ""
            if not registered_xml_element:
                p_close_id, p_close_extras = _dedup_id(p_close_raw, seen_ids)
                close_extras = dict(p_close_extras)
                close_extras["role"] = "close"
                markers.append(Marker(type="paragraph-break", offset=offset(),
                                      content="", id=p_close_id, extras=close_extras))
        elif tag == "seg":
            _emit_seg_with_run(child, div_run_state, text_buf, markers,
                               offset, markers_info, seen_ids,
                               xml_elements=state.get("xml_elements", set()))
        elif tag == "div":
            nested_id = _div_marker_id(child)
            nested_entry: dict = {}
            nested_div_attrs = _attrs_to_dict(child.attrib)
            if nested_div_attrs:
                nested_entry["div_attrs"] = nested_div_attrs
            nested_level = depth + 1
            nested_label = _div_head_text(child)
            start_extras: dict = {"level": nested_level}
            if nested_label:
                start_extras["head_text"] = nested_label
            markers.append(Marker(type="tls:div-start", offset=offset(),
                                  content="", id=nested_id,
                                  extras=start_extras))
            _walk_cbeta_div_children(
                child, text_buf, markers, markers_info, nested_divs_info,
                offset, nested_entry, head_state, seen_ids, state,
                is_outermost=False, depth=nested_level,
            )
            markers.append(Marker(type="tls:div-end", offset=offset(),
                                  content="", id=nested_id))
            if nested_id:
                nested_divs_info[nested_id] = nested_entry
        elif tag in ("byline", "dialog", "sp"):
            # CBETA wraps content (segs, sometimes nested <p>) inside these.
            # Walk through them transparently — they're containers only.
            _walk_cbeta_div_children(
                child, text_buf, markers, markers_info, nested_divs_info,
                offset, div_entry, head_state, seen_ids, state,
                is_outermost=False, depth=depth,
            )
        elif tag == "lb":
            _emit_lb(child, markers, offset, seen_ids)
        # docNumber, anchor, note, g and other stray elements at div
        # level: ignored. (Inline <g>/<note> are handled by _emit_seg from
        # within seg children.)
        if registered_xml_element:
            markers.append(_xml_element_marker(
                child, offset(), "close", seen_ids=seen_ids,
            ))
    # Close any open div-level run at end of container.
    _close_seg_run(div_run_state, markers, offset)


def _walk_cbeta_inline_children(
    parent, text_buf: list[str], markers: list[Marker],
    markers_info: dict, offset, seen_ids: dict[str, int],
    xml_elements: set[str] | None = None,
):
    """Walk children of a ``<p>``-like element, emitting ``<seg>`` content
    and ``<pb>`` markers; everything else is ignored at this level.

    Maintains the typed-seg run-folding state machine for this paragraph:
    consecutive ``<seg type=T>`` siblings fold into one
    ``tls:seg-start`` / ``tls:seg-end`` range; ``<pb>`` does not break the
    run; an inline ``<note>`` does.
    """
    run_state = _new_seg_run_state(seen_ids)
    for child in parent.iterchildren():
        if not isinstance(child.tag, str):
            continue
        tag = etree.QName(child).localname
        registered_xml_element = _xml_element_is_registered(
            child, xml_elements or set(),
        )
        if registered_xml_element:
            markers.append(_xml_element_marker(
                child, offset(), "open", seen_ids=seen_ids,
            ))
        if tag == "seg":
            _emit_seg_with_run(child, run_state, text_buf, markers,
                               offset, markers_info, seen_ids,
                               xml_elements=xml_elements)
        elif tag == "pb":
            raw_id = _xmlid(child)
            mid, id_extras = _dedup_id(raw_id, seen_ids)
            markers.append(Marker(type="page-break", offset=offset(),
                                  content="", id=mid, extras=id_extras))
            attrs = _attrs_minus(child, ("xml:id",))
            if mid and attrs:
                markers_info[mid] = {"type": "page-break", "attrs": attrs}
        elif tag == "note" and child.get("place") == "inline":
            _close_seg_run(run_state, markers, offset)
            note_sink = _make_sink(text_buf, markers, offset,
                                   markers_info, seen_ids)
            _emit_inline_note(child, note_sink)
        elif tag == "lb":
            _emit_lb(child, markers, offset, seen_ids)
        # <anchor>, etc.: ignored.
        if registered_xml_element:
            markers.append(_xml_element_marker(
                child, offset(), "close", seen_ids=seen_ids,
            ))
    _close_seg_run(run_state, markers, offset)


def _section_from_div(
    div, seen_ids: dict[str, int] | None = None,
    xml_elements: set[str] | None = None,
) -> tuple[Section, dict, dict, dict]:
    """Walk a top-level <div>, producing a Section with section-local offsets,
    plus a juan_div_entry, markers_info, and nested_divs_info for round-trip.

    Nested ``<div>`` elements (e.g. KR1a0171's chapter sections under each
    juan div) are walked recursively. Their content is emitted into the same
    Section, bracketed by paired ``tls:div-start`` / ``tls:div-end`` markers
    so the exporter can rebuild the source hierarchy. The id on those markers
    is the nested div's head xml:id, which is also the key under which the
    nested div's attrs land in ``nested_divs_info``.
    """
    if seen_ids is None:
        seen_ids = {}
    xml_elements = xml_elements or set()
    text_buf: list[str] = []
    markers: list[Marker] = []
    markers_info: dict = {}
    nested_divs_info: dict = {}

    juan_div_entry: dict = {}
    juan_div_attrs = _attrs_to_dict(div.attrib)
    if juan_div_attrs:
        juan_div_entry["div_attrs"] = juan_div_attrs

    head_state = {"text": "", "id": ""}

    def offset() -> int:
        return sum(len(p) for p in text_buf)

    _walk_div_children(
        div, text_buf, markers, markers_info, nested_divs_info,
        offset, juan_div_entry, head_state, seen_ids, is_outermost=True,
        xml_elements=xml_elements,
    )

    section = Section(
        head_text=head_state["text"],
        head_marker_id=head_state["id"],
        text="".join(text_buf),
        markers=markers,
    )
    return section, juan_div_entry, markers_info, nested_divs_info


def _walk_div_children(div, text_buf: list[str], markers: list[Marker],
                       markers_info: dict, nested_divs_info: dict,
                       offset, div_entry: dict, head_state: dict,
                       seen_ids: dict[str, int], is_outermost: bool,
                       depth: int = 1,
                       xml_elements: set[str] | None = None):
    """Walk one ``<div>``'s children, appending text and markers in document
    order. ``div_entry`` collects attrs (head_attrs / head_inner_seg_attrs /
    p_attrs) for *this* div. ``head_state`` tracks the section-level head
    fields, set only by the outermost div's first ``<head>``. ``depth`` is
    1 for the section's outermost div and increments for each nested
    ``<div>`` recursion; nested ``tls:div-start`` markers carry it as
    ``extras['level']`` so the TOC builder can place them.
    """
    for child in div.iterchildren():
        if not isinstance(child.tag, str):  # skip comments / PIs
            continue
        tag = etree.QName(child).localname
        registered_xml_element = _xml_element_is_registered(
            child, xml_elements or set(),
        )
        if registered_xml_element:
            markers.append(_xml_element_marker(
                child, offset(), "open", seen_ids=seen_ids,
            ))
        if tag == "pb":
            raw_id = _xmlid(child)
            mid, id_extras = _dedup_id(raw_id, seen_ids)
            markers.append(Marker(type="page-break", offset=offset(),
                                  content="", id=mid, extras=id_extras))
            attrs = _attrs_minus(child, ("xml:id",))
            if mid and attrs:
                markers_info[mid] = {"type": "page-break", "attrs": attrs}
        elif tag == "lb":
            _emit_lb(child, markers, offset, seen_ids)
        elif tag == "head":
            inner_seg = child.find(_q("seg"))
            raw_id = _xmlid(inner_seg) if inner_seg is not None else _xmlid(child)
            seg_id, id_extras = _dedup_id(raw_id, seen_ids)
            markers.append(Marker(type="tls:head", offset=offset(),
                                  content="", id=seg_id, extras=id_extras))
            head_text_start = offset()
            if inner_seg is not None:
                head_sink = _make_sink(text_buf, markers, offset,
                                       markers_info, seen_ids, in_head=True,
                                       xml_elements=xml_elements)
                _walk_seg_inline(inner_seg, head_sink)
            head_text_str = "".join(text_buf)[head_text_start:]
            if "head_attrs" not in div_entry:
                head_attrs = _attrs_to_dict(child.attrib)
                if head_attrs:
                    div_entry["head_attrs"] = head_attrs
                if inner_seg is not None:
                    inner_extras = _attrs_minus(inner_seg, ("xml:id",))
                    if inner_extras:
                        div_entry["head_inner_seg_attrs"] = inner_extras
            if is_outermost and not head_state["text"]:
                head_state["text"] = _cjk_only(head_text_str)
                head_state["id"] = seg_id
        elif tag == "p":
            p_xmlid = _xmlid(child)
            if not registered_xml_element:
                p_open_id, p_open_extras = _dedup_id(p_xmlid, seen_ids)
                open_extras = dict(p_open_extras)
                open_extras["role"] = "open"
                markers.append(Marker(type="paragraph-break", offset=offset(),
                                      content="", id=p_open_id, extras=open_extras))
            p_attrs = _attrs_to_dict(child.attrib)
            if p_attrs:
                div_entry.setdefault("p_attrs", []).append(p_attrs)
            run_state = _new_seg_run_state(seen_ids)
            for seg in child.iterchildren():
                if not isinstance(seg.tag, str):
                    continue
                seg_tag = etree.QName(seg).localname
                if seg_tag == "seg":
                    _emit_seg_with_run(seg, run_state, text_buf, markers,
                                       offset, markers_info, seen_ids,
                                       xml_elements=xml_elements)
                elif seg_tag == "pb":
                    # <pb> does NOT break a typed-seg run.
                    raw_id = _xmlid(seg)
                    mid, id_extras = _dedup_id(raw_id, seen_ids)
                    markers.append(Marker(type="page-break", offset=offset(),
                                          content="", id=mid, extras=id_extras))
                    attrs = _attrs_minus(seg, ("xml:id",))
                    if mid and attrs:
                        markers_info[mid] = {"type": "page-break", "attrs": attrs}
                elif seg_tag == "note" and seg.get("place") == "inline":
                    # Inline-note at p-level: break run, emit brackets.
                    _close_seg_run(run_state, markers, offset)
                    note_sink = _make_sink(text_buf, markers, offset,
                                           markers_info, seen_ids,
                                           xml_elements=xml_elements)
                    _emit_inline_note(seg, note_sink)
                elif seg_tag == "lb":
                    # <lb> does NOT break a typed-seg run (same as pb).
                    _emit_lb(seg, markers, offset, seen_ids)
                # other inline tags ignored for now
            _close_seg_run(run_state, markers, offset)
            p_close_raw = f"{p_xmlid}_end" if p_xmlid else ""
            if not registered_xml_element:
                p_close_id, p_close_extras = _dedup_id(p_close_raw, seen_ids)
                close_extras = dict(p_close_extras)
                close_extras["role"] = "close"
                markers.append(Marker(type="paragraph-break", offset=offset(),
                                      content="", id=p_close_id, extras=close_extras))
        elif tag == "div":
            nested_id = _div_marker_id(child)
            nested_entry: dict = {}
            nested_div_attrs = _attrs_to_dict(child.attrib)
            if nested_div_attrs:
                nested_entry["div_attrs"] = nested_div_attrs
            nested_level = depth + 1
            nested_label = _div_head_text(child)
            start_extras: dict = {"level": nested_level}
            if nested_label:
                start_extras["head_text"] = nested_label
            markers.append(Marker(type="tls:div-start", offset=offset(),
                                  content="", id=nested_id,
                                  extras=start_extras))
            _walk_div_children(
                child, text_buf, markers, markers_info, nested_divs_info,
                offset, nested_entry, head_state, seen_ids,
                is_outermost=False, depth=nested_level,
                xml_elements=xml_elements,
            )
            markers.append(Marker(type="tls:div-end", offset=offset(),
                                  content="", id=nested_id))
            if nested_id:
                nested_divs_info[nested_id] = nested_entry
        # Non-element nodes (text/tail) between siblings are ignored —
        # whitespace-only in TLS sources.
        if registered_xml_element:
            markers.append(_xml_element_marker(
                child, offset(), "close", seen_ids=seen_ids,
            ))


def _div_head_text(div) -> str:
    """Return a CJK-only TOC label for this div, drawn from its first
    ``<head>`` child's inner ``<seg>``.

    Inline notes are excluded — the TOC label is for navigation, not
    commentary. Punctuation and whitespace are stripped because labels
    must satisfy the same CJK-only invariant as the bundle's body text.
    """
    head = div.find(_q("head"))
    if head is None:
        return ""
    inner_seg = head.find(_q("seg"))
    if inner_seg is None:
        return ""
    parts: list[str] = []
    if inner_seg.text:
        parts.append(inner_seg.text)
    for child in inner_seg.iterchildren():
        if not isinstance(child.tag, str):
            continue
        ctag = etree.QName(child).localname
        if ctag == "note" and child.get("place") == "inline":
            # Skip inline-note text; the TOC label is navigation, not gloss.
            if child.tail:
                parts.append(child.tail)
            continue
        if child.text:
            parts.append(child.text)
        if child.tail:
            parts.append(child.tail)
    return _cjk_only(unicodedata.normalize("NFC", "".join(parts)))


def _div_marker_id(div) -> str:
    """Return the canonical id for this div's start/end markers.

    Prefers the div's own ``xml:id`` so the source identifier survives in the
    bundle. Falls back to the inner ``<seg>``'s xml:id, then the head's own
    xml:id, then the empty string. The fallback chain preserves backwards-
    compatible keying for divs that lack their own xml:id.
    """
    own = _xmlid(div)
    if own:
        return own
    head = div.find(_q("head"))
    if head is None:
        return ""
    seg = head.find(_q("seg"))
    if seg is not None:
        sid = _xmlid(seg)
        if sid:
            return sid
    return _xmlid(head)


def _attrs_minus(elem, drop: tuple[str, ...]) -> dict:
    """Return the element's attrs as a prefix-keyed dict, omitting any keys
    in ``drop``. Used to capture *extra* attrs beyond what the bundle marker
    already carries (typically ``xml:id``)."""
    attrs = _attrs_to_dict(elem.attrib)
    for k in drop:
        attrs.pop(k, None)
    return attrs


def _make_sink(text_buf: list[str], markers: list[Marker], offset_fn,
               markers_info: dict, seen_ids: dict[str, int],
               *, in_head: bool = False,
               xml_elements: set[str] | None = None) -> dict:
    return {
        "text_buf": text_buf,
        "markers": markers,
        "offset": offset_fn,
        "markers_info": markers_info,
        "seen_ids": seen_ids,
        "xml_elements": xml_elements or set(),
        "in_head": in_head,
        # When True, _append_text_filtered emits markers as usual but
        # suppresses text appends. Used inside heads' inline notes so the
        # note's text doesn't bleed into the head's TOC label slice.
        "suppress_text": False,
    }


def _append_text_filtered(text: str, sink: dict) -> None:
    """Append NFC-normalized text to ``sink['text_buf']``, enforcing the
    body-text CJK+PUA invariant.

    CJK ideographs and BKK PUA codepoints flow to text. Ideographic space
    (U+3000) becomes an ``indent`` marker. Other ASCII whitespace is
    dropped. Anything else (ASCII parens, stray punctuation residue) is
    captured as a ``punctuation`` marker so the source byte survives even
    though it can't sit in body text.
    """
    if not text:
        return
    suppress = sink.get("suppress_text", False)
    for ch in unicodedata.normalize("NFC", text):
        if is_allowed_body_char(ch):
            if not suppress:
                sink["text_buf"].append(ch)
        elif ch == "\u3000":
            sink["markers"].append(Marker(
                type="indent", offset=sink["offset"](),
                content=ch, id="",
            ))
        elif ch.isspace():
            continue
        else:
            sink["markers"].append(Marker(
                type="punctuation", offset=sink["offset"](),
                content=ch, id="",
            ))


def _emit_inline_note(note, sink: dict) -> None:
    """Emit a ``<note place="inline">`` as bracket markers around its content.

    The opening ``tls:note-start`` carries the source ``(`` as content (a
    presentation hint for renderers; ``body.text`` stays CJK-only). The
    closing ``tls:note-end`` carries ``)``. The note's text content flows
    through the regular filter — except inside heads, where the sink's
    ``suppress_text`` flag is engaged so the note doesn't bleed into the
    head's TOC label slice.
    """
    raw_id = _xmlid(note)
    note_id, id_extras = _dedup_id(raw_id, sink["seen_ids"])
    extras = dict(id_extras)
    note_attrs = _attrs_minus(note, ("xml:id",))
    if note_attrs:
        extras["note_attrs"] = note_attrs
    sink["markers"].append(Marker(
        type="tls:note-start", offset=sink["offset"](),
        content="(", id=note_id, extras=extras,
    ))

    saved_suppress = sink["suppress_text"]
    if sink["in_head"]:
        sink["suppress_text"] = True
    try:
        _walk_seg_inline(note, sink)
    finally:
        sink["suppress_text"] = saved_suppress

    end_raw = f"{raw_id}_end" if raw_id else ""
    end_id, end_extras_dup = _dedup_id(end_raw, sink["seen_ids"])
    end_extras = dict(end_extras_dup)
    if note_id:
        end_extras["note_ref"] = note_id
    sink["markers"].append(Marker(
        type="tls:note-end", offset=sink["offset"](),
        content=")", id=end_id, extras=end_extras,
    ))


def _walk_seg_inline(seg, sink: dict) -> None:
    """Walk a ``<seg>``'s mixed content, emitting text and markers via the
    sink. Shared by body and head paths so a single place adds new shapes
    (inline notes, future inline elements) for both contexts.
    """
    if seg is None:
        return
    _append_text_filtered(seg.text or "", sink)
    for child in seg.iterchildren():
        if not isinstance(child.tag, str):
            continue
        tag = etree.QName(child).localname
        registered_xml_element = _xml_element_is_registered(
            child, sink.get("xml_elements", set()),
        )
        if registered_xml_element:
            sink["markers"].append(_xml_element_marker(
                child, sink["offset"](), "open", seen_ids=sink["seen_ids"],
            ))
        if tag == "c":
            sink["markers"].append(Marker(
                type="punctuation", offset=sink["offset"](),
                content=child.get("n", ""), id="",
            ))
        elif tag == "pb":
            raw_pb_id = _xmlid(child)
            mid, pb_id_extras = _dedup_id(raw_pb_id, sink["seen_ids"])
            sink["markers"].append(Marker(
                type="page-break", offset=sink["offset"](),
                content="", id=mid, extras=pb_id_extras,
            ))
            pb_attrs = _attrs_minus(child, ("xml:id",))
            if mid and pb_attrs:
                sink["markers_info"][mid] = {"type": "page-break", "attrs": pb_attrs}
        elif tag == "lb":
            _emit_lb(child, sink["markers"], sink["offset"], sink["seen_ids"])
        elif tag == "note" and child.get("place") == "inline":
            _emit_inline_note(child, sink)
        else:
            # Unknown inline element: keep its text as plain content.
            # Non-inline notes, CBETA <g>, <date>, etc. land here.
            _append_text_filtered(child.text or "", sink)
        if registered_xml_element:
            sink["markers"].append(_xml_element_marker(
                child, sink["offset"](), "close", seen_ids=sink["seen_ids"],
            ))
        _append_text_filtered(child.tail or "", sink)


def _emit_seg(seg, text_buf: list[str], markers: list[Marker], offset_fn,
              markers_info: dict, seen_ids: dict[str, int],
              *, xml_elements: set[str] | None = None):
    raw_id = _xmlid(seg)
    seg_id, id_extras = _dedup_id(raw_id, seen_ids)
    _emit_seg_body(seg, seg_id, id_extras, text_buf, markers, offset_fn,
                   markers_info, seen_ids, xml_elements=xml_elements)


def _emit_seg_body(seg, seg_id: str, id_extras: dict, text_buf: list[str],
                   markers: list[Marker], offset_fn, markers_info: dict,
                   seen_ids: dict[str, int],
                   *, xml_elements: set[str] | None = None):
    """Emit the per-seg ``tls:seg`` point marker plus the seg's content,
    given a precomputed deduped ``seg_id``. Split out so the run-folding
    state machine can dedup the seg id once (for use on
    ``tls:seg-start``) and reuse it here."""
    markers.append(Marker(type="tls:seg", offset=offset_fn(),
                          content="", id=seg_id, extras=id_extras))
    attrs = _attrs_minus(seg, ("xml:id",))
    if seg_id and attrs:
        markers_info[seg_id] = {"type": "tls:seg", "attrs": attrs}
    sink = _make_sink(
        text_buf, markers, offset_fn, markers_info, seen_ids,
        xml_elements=xml_elements,
    )
    _walk_seg_inline(seg, sink)


def _new_seg_run_state(seen_ids: dict[str, int]) -> dict:
    """Per-``<p>`` state for the typed-seg run-folding state machine.

    Lives on the ``<p>`` loop's stack frame; reset between paragraphs.
    A *run* is a maximal sequence of consecutive ``<seg type=T>`` siblings
    with the same ``T`` value, bracketed in the marker stream by
    ``tls:seg-start`` / ``tls:seg-end``. Untyped segs and inline ``<note>``
    children break runs; ``<pb>`` does not.
    """
    return {
        "run_type": None,         # str | None — current run's seg/@type
        "run_start_marker": None,  # Marker | None — for member_ids updates
        "run_member_ids": [],      # list[str] — deduped seg ids in run
        "seen_ids": seen_ids,
    }


def _close_seg_run(state: dict, markers: list[Marker], offset_fn) -> None:
    """Close the current typed-seg run if open, emitting ``tls:seg-end``.

    The end marker's id is the last member's id with ``_end`` suffix
    (routed through ``_dedup_id`` so a literal ``_end`` collision still
    gets a ``_dup{n}`` suffix). No-op if no run is open.
    """
    if state["run_type"] is None:
        return
    last_id = state["run_member_ids"][-1] if state["run_member_ids"] else ""
    end_raw = f"{last_id}_end" if last_id else ""
    end_id, end_extras = _dedup_id(end_raw, state["seen_ids"])
    end_extras = dict(end_extras)
    end_extras["seg_type"] = state["run_type"]
    markers.append(Marker(
        type="tls:seg-end", offset=offset_fn(),
        content="", id=end_id, extras=end_extras,
    ))
    state["run_type"] = None
    state["run_start_marker"] = None
    state["run_member_ids"] = []


def _emit_seg_with_run(seg, state: dict, text_buf: list[str],
                       markers: list[Marker], offset_fn,
                       markers_info: dict, seen_ids: dict[str, int],
                       *, xml_elements: set[str] | None = None) -> None:
    """Emit one ``<seg>`` while maintaining typed-seg run-folding state.

    Same emission shape as ``_emit_seg`` (``tls:seg`` point marker +
    content) plus run bookkeeping: opens a new run on the first typed seg,
    folds consecutive same-type segs into one run, closes the previous
    run when ``type`` changes or an untyped seg appears.
    """
    raw_id = _xmlid(seg)
    seg_id, id_extras = _dedup_id(raw_id, seen_ids)
    seg_type = seg.get("type") or None

    if seg_type is None:
        # Untyped seg breaks the current run.
        _close_seg_run(state, markers, offset_fn)
    elif state["run_type"] == seg_type:
        # Continue the run; record this seg as a member.
        state["run_member_ids"].append(seg_id)
        if state["run_start_marker"] is not None:
            state["run_start_marker"].extras["member_ids"] = list(
                state["run_member_ids"]
            )
    else:
        # Different type (or first typed seg in this <p>): close, then open.
        _close_seg_run(state, markers, offset_fn)
        start_marker = Marker(
            type="tls:seg-start", offset=offset_fn(),
            content="", id=seg_id,
            extras={"seg_type": seg_type, "member_ids": [seg_id]},
        )
        markers.append(start_marker)
        state["run_type"] = seg_type
        state["run_start_marker"] = start_marker
        state["run_member_ids"] = [seg_id]

    _emit_seg_body(seg, seg_id, id_extras, text_buf, markers, offset_fn,
                   markers_info, seen_ids, xml_elements=xml_elements)


def _parse_annotations(path: Path,
                       provenance: str) -> tuple[list[Annotation], dict, dict]:
    """Parse a tls:ann file, returning ``(annotations, info, envelope)``.

    ``info`` is keyed by annotation @xml:id; each entry carries the full
    ``<tls:ann>`` element as a tree plus its provenance (``swl`` or ``doc``)
    so the future XML exporter can rebuild the source verbatim.

    ``envelope`` carries the file-level wrapper that the exporter needs to
    recreate the surrounding document: ``tei_root_attrs``, ``tei_header``
    (tree), ``body_div_head`` (the literal ``<head>Annotations</head>`` text),
    ``p_attrs`` (the wrapper ``<p>``'s attributes), and ``seg_lines`` (the
    text of each ``<line>`` element wrapping a seg's annotations).
    """
    ann_parser = etree.XMLParser(recover=True)
    tree = etree.parse(str(path), ann_parser)
    out: list[Annotation] = []
    info: dict = {}
    envelope: dict = {}

    root = tree.getroot()
    if etree.QName(root).localname == "TEI":
        root_attrs = _attrs_to_dict(root.attrib)
        if root_attrs:
            envelope["tei_root_attrs"] = root_attrs
    header_el = tree.find(f".//{_q('teiHeader')}")
    if header_el is not None:
        envelope["tei_header"] = _to_tree(header_el)
    body = tree.find(f".//{_q('body')}")
    if body is not None:
        body_div = body.find(_q("div"))
        if body_div is not None:
            head = body_div.find(_q("head"))
            if head is not None and head.text:
                envelope["body_div_head"] = head.text
            p = body_div.find(_q("p"))
            if p is not None:
                p_attrs = _attrs_to_dict(p.attrib)
                if p_attrs:
                    envelope["p_attrs"] = p_attrs

    seg_lines: dict = {}
    for seg in tree.iter(_q("seg")):
        seg_id = _xmlid(seg)
        if not seg_id:
            continue
        line = seg.find(_q("line"))
        if line is not None and line.text is not None:
            seg_lines[seg_id] = line.text
    if seg_lines:
        envelope["seg_lines"] = seg_lines

    for ann in tree.iter(_q("ann", TLS_NS)):
        seg = ann.getparent()
        # Walk up until we find a <seg> ancestor.
        while seg is not None and etree.QName(seg).localname != "seg":
            seg = seg.getparent()
        seg_id = _xmlid(seg) if seg is not None else ""
        srcline = ann.find(f".//{_q('srcline', TLS_NS)}")
        pos_raw = srcline.get("pos") if srcline is not None else None
        pos = _parse_pos(pos_raw)

        out.append(Annotation(
            seg_id=seg_id,
            pos=pos,
            payload=_annotation_payload(ann),
            provenance=provenance,
        ))
        ann_id = _xmlid(ann)
        if ann_id:
            info[ann_id] = {
                "provenance": provenance,
                "seg_id": seg_id,
                "tree": _to_tree(ann),
            }
    return out, info, envelope


def _parse_pos(raw: str | None) -> int | None:
    if raw is None or raw == "" or raw == "undefined":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _annotation_payload(ann) -> dict:
    """Extract the rich payload for the .ann.yaml file."""
    payload: dict = {
        "id": _xmlid(ann),
        "concept": ann.get("concept", ""),
        "concept_id": ann.get("concept-id", ""),
    }

    srcline = ann.find(f".//{_q('srcline', TLS_NS)}")
    line = ann.find(f".//{_q('line', TLS_NS)}")
    translation: dict = {}
    if line is not None:
        if line.text:
            translation["text"] = line.text
        if line.get("title"):
            translation["title"] = line.get("title")
        if line.get("src"):
            translation["src"] = line.get("src")
    if translation:
        payload["translation"] = translation

    form = ann.find(_q("form"))
    if form is not None:
        form_dict = {}
        if form.get("orig") is not None:
            form_dict["orig"] = form.get("orig", "")
        orth_el = form.find(_q("orth"))
        if orth_el is not None and orth_el.text:
            form_dict["orth"] = orth_el.text
        orth_els = form.findall(_q("orth"))
        if len(orth_els) > 1:
            form_dict["orths"] = [o.text for o in orth_els if o.text]
        pron_el = form.find(_q("pron"))
        if pron_el is not None and pron_el.text:
            form_dict["pron"] = pron_el.text
        if form_dict:
            payload["form"] = form_dict

    sense = ann.find(_q("sense"))
    if sense is not None:
        sense_dict: dict = {}
        corresp = sense.get("corresp")
        if corresp:
            sense_dict["id"] = corresp.lstrip("#")
        gram = sense.find(_q("gramGrp"))
        if gram is not None:
            pos_el = gram.find(_q("pos"))
            if pos_el is not None and pos_el.text:
                sense_dict["pos"] = pos_el.text.strip()
            syn = gram.find(_q("syn-func", TLS_NS))
            if syn is not None and syn.text:
                sense_dict["syn_func"] = syn.text.strip()
            sem = gram.find(_q("sem-feat", TLS_NS))
            if sem is not None and sem.text:
                sense_dict["sem_feat"] = sem.text.strip()
            usg = gram.find(_q("usg"))
            if usg is not None and usg.text:
                sense_dict["usage"] = {usg.get("type", "value"): usg.text.strip()}
        defn = sense.find(_q("def"))
        if defn is not None and defn.text:
            sense_dict["def"] = defn.text
        if sense_dict:
            payload["sense"] = sense_dict

    md = ann.find(_q("metadata", TLS_NS))
    if md is not None:
        md_dict: dict = {}
        resp = md.get("resp")
        if resp:
            md_dict["resp"] = resp.lstrip("#")
        created = md.get("created")
        if created:
            md_dict["created"] = created
        if md_dict:
            payload["metadata"] = md_dict

    return payload


_CATREF_RE = re.compile(r"#([\w-]+)")


def _parse_metadata(text_xml: Path, text_id: str) -> dict:
    tree = etree.parse(str(text_xml), etree.XMLParser(recover=True))
    md: dict = {}

    title_el = tree.find(f".//{_q('teiHeader')}//{_q('titleStmt')}/{_q('title')}")
    if title_el is not None and title_el.text:
        md["title"] = title_el.text.strip()

    root = tree.getroot()
    tei_xml_id = root.get(_q("id", XML_NS), "")

    identifiers: dict = {}
    for idno in tree.iter(_q("idno")):
        id_type = idno.get("type", "").strip()
        id_val = (idno.text or "").strip()
        if id_type and id_val:
            key = "krp" if id_type.lower() == "kanripo" else id_type.lower()
            identifiers[key] = id_val

    identifiers.setdefault("krp", text_id)

    if tei_xml_id and tei_xml_id != identifiers.get("krp"):
        identifiers["tei_id"] = tei_xml_id

    md["identifiers"] = identifiers

    # textClass catRefs become tags.
    tags: dict = {}
    for catref in tree.iter(_q("catRef")):
        scheme = catref.get("scheme", "")
        target = catref.get("target", "")
        m_scheme = _CATREF_RE.match(scheme)
        m_target = _CATREF_RE.match(target)
        if m_scheme and m_target:
            key = m_scheme.group(1).replace("-", "-")
            tags.setdefault(key, []).append(m_target.group(1))
    if tags:
        # Flatten single-value entries.
        flat: dict = {}
        for k, v in tags.items():
            if len(v) == 1:
                flat[k] = v[0]
            else:
                flat[k] = v
        md["tags"] = flat

    return md
