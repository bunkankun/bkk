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
import unicodedata
from pathlib import Path

from lxml import etree

from ..classify import _split_section_at
from ..ir import Annotation, Bundle, Juan, Marker, Section


TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"
TLS_NS = "http://hxwd.org/ns/1.0"

_NS = {"tei": TEI_NS, "xml": XML_NS, "tls": TLS_NS}

# Namespace URI -> short prefix used in source_info dumps. The TEI default
# namespace renders without a prefix; everything else gets a known prefix or
# falls back to Clark notation.
_NS_PREFIXES = {
    TEI_NS: "",
    XML_NS: "xml",
    TLS_NS: "tls",
}


def _q(local: str, ns: str = TEI_NS) -> str:
    return f"{{{ns}}}{local}"


def _xmlid(el) -> str:
    return el.get(_q("id", XML_NS), "")


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
             text_id: str) -> Bundle:
    """Read one TLS text and return its Bundle.

    ``swl_ann`` and ``doc_ann`` are optional; when present they are concatenated
    in that order to preserve provenance ordering.

    The edition short id (e.g. ``T``, ``tls``) is derived from the second
    component of the marker ids, which follow ``<text-id>_<edition>_<location>``
    in both the Kanripo and TLS corpora.
    """
    sections, divs_info, markers_info, tei_info = _parse_text(text_xml)

    annotations: list[Annotation] = []
    annotations_info: dict = {}
    ann_files_info: dict = {}
    for path, role in ((swl_ann, "swl"), (doc_ann, "doc")):
        if path is not None and path.exists():
            anns, ann_info, envelope = _parse_annotations(path, role)
            annotations.extend(anns)
            annotations_info.update(ann_info)
            ann_files_info[role] = envelope

    metadata = _parse_metadata(text_xml, text_id)
    edition_short = _derive_edition_short(sections, text_id)

    source_info = {
        "text_id": text_id,
        "format": "tls",
        "format_version": 1,
        "source_files": _source_files(text_xml, swl_ann, doc_ann),
        "tei": tei_info,
        "divs": divs_info,
        "markers": markers_info,
        "ann_files": ann_files_info,
        "annotations": annotations_info,
    }

    juans = _build_juans(sections, annotations, text_id)
    source = {"repository": "tls-texts", "path": f"data/tls/{text_id}.xml"}
    metadata.setdefault("source", source)
    return Bundle(
        text_id=text_id,
        juans=juans,
        metadata=metadata,
        edition_short=edition_short,
        source=source,
        source_info=source_info,
    )


def _build_juans(sections: list[Section], annotations: list[Annotation],
                 text_id: str) -> list[Juan]:
    """Group ``sections`` into per-juan :class:`Juan` objects and partition
    ``annotations`` by which juan contains their seg.
    """
    groups = _split_sections_into_juans(sections, text_id)
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

    return [
        Juan(seq=seqs[i], sections=secs, annotations=buckets[i])
        for i, (_, secs) in enumerate(groups)
    ]


_LABEL_BEARING_MARKERS = ("page-break", "tls:head", "tls:seg")


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
        boundaries: list[tuple[int, str]] = []
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
                boundaries.append((m.offset, label))
                last_seen = label

        entry_label = first_label or running_label or "001"
        if not boundaries:
            append(entry_label, sec)
            running_label = entry_label
            continue

        cursor_label = entry_label
        head = sec
        accumulated = 0
        for offset, new_label in boundaries:
            front, head = _split_section_at(head, offset - accumulated)
            append(cursor_label, front)
            accumulated += len(front.text)
            cursor_label = new_label
        append(cursor_label, head)
        running_label = cursor_label

    return groups


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


def _parse_text(path: Path) -> tuple[list[Section], dict, dict, dict]:
    """Parse the text XML.

    Returns ``(sections, divs_info, markers_info, tei_info)``:
    - ``sections``: ordered list of Sections used by the bundle pipeline.
    - ``divs_info``: source attrs per top-level div, keyed by head_marker_id.
    - ``markers_info``: source attrs per id-bearing marker (pb, head outer,
      seg), keyed by the marker's id. Markers without stable ids
      (paragraph-break, punctuation) are not included; nothing extra needs
      to be carried for them.
    - ``tei_info``: ``{root_attrs, header}`` capturing the ``<TEI>`` element's
      attributes and the full ``<teiHeader>`` tree.
    """
    tree = etree.parse(str(path))
    root = tree.getroot()
    body = tree.find(f".//{_q('body')}")
    if body is None:
        raise ValueError(f"no <body> element in {path}")

    sections: list[Section] = []
    divs_info: dict = {}
    markers_info: dict = {}
    for div in body.iterfind(_q("div")):
        section, div_entry, marker_entries = _section_from_div(div)
        sections.append(section)
        if section.head_marker_id:
            divs_info[section.head_marker_id] = div_entry
        for mid, info in marker_entries.items():
            markers_info[mid] = info

    tei_info: dict = {}
    if etree.QName(root).localname == "TEI":
        root_attrs = _attrs_to_dict(root.attrib)
        if root_attrs:
            tei_info["root_attrs"] = root_attrs
    header_el = tree.find(f".//{_q('teiHeader')}")
    if header_el is not None:
        tei_info["header"] = _to_tree(header_el)

    return sections, divs_info, markers_info, tei_info


def _section_from_div(div) -> tuple[Section, dict, dict]:
    """Walk a top-level <div>, producing a Section with section-local offsets,
    plus a div_info entry and a markers_info dict for source-attr round-trip.
    """
    text_buf: list[str] = []
    markers: list[Marker] = []
    head_text = ""
    head_marker_id = ""

    div_entry: dict = {}
    div_attrs = _attrs_to_dict(div.attrib)
    if div_attrs:
        div_entry["div_attrs"] = div_attrs

    markers_info: dict = {}

    def offset() -> int:
        return sum(len(p) for p in text_buf)

    for child in div.iterchildren():
        tag = etree.QName(child).localname
        if tag == "pb":
            mid = _xmlid(child)
            markers.append(Marker(type="page-break", offset=offset(),
                                  content="", id=mid))
            extras = _attrs_minus(child, ("xml:id",))
            if mid and extras:
                markers_info[mid] = {"type": "page-break", "attrs": extras}
        elif tag == "head":
            inner_seg = child.find(_q("seg"))
            seg_text = unicodedata.normalize("NFC", _seg_text(inner_seg))
            seg_id = _xmlid(inner_seg) if inner_seg is not None else _xmlid(child)
            markers.append(Marker(type="tls:head", offset=offset(),
                                  content="", id=seg_id))
            text_buf.append(seg_text)
            if not head_text:
                head_text = _cjk_only(seg_text)
                head_marker_id = seg_id
                head_attrs = _attrs_to_dict(child.attrib)
                if head_attrs:
                    div_entry["head_attrs"] = head_attrs
                if inner_seg is not None:
                    inner_extras = _attrs_minus(inner_seg, ("xml:id",))
                    if inner_extras:
                        div_entry["head_inner_seg_attrs"] = inner_extras
        elif tag == "p":
            markers.append(Marker(type="paragraph-break", offset=offset(),
                                  content="", id=""))
            p_attrs = _attrs_to_dict(child.attrib)
            if p_attrs:
                div_entry.setdefault("p_attrs", []).append(p_attrs)
            for seg in child.iterchildren():
                seg_tag = etree.QName(seg).localname
                if seg_tag == "seg":
                    _emit_seg(seg, text_buf, markers, offset, markers_info)
                elif seg_tag == "pb":
                    mid = _xmlid(seg)
                    markers.append(Marker(type="page-break", offset=offset(),
                                          content="", id=mid))
                    extras = _attrs_minus(seg, ("xml:id",))
                    if mid and extras:
                        markers_info[mid] = {"type": "page-break", "attrs": extras}
                # other inline tags ignored for now
            markers.append(Marker(type="paragraph-break", offset=offset(),
                                  content="", id=""))
        # Non-element nodes (text/tail) between top-level children are
        # ignored — they're whitespace only in TLS sources.

    section = Section(
        head_text=head_text,
        head_marker_id=head_marker_id,
        text="".join(text_buf),
        markers=markers,
    )
    return section, div_entry, markers_info


def _attrs_minus(elem, drop: tuple[str, ...]) -> dict:
    """Return the element's attrs as a prefix-keyed dict, omitting any keys
    in ``drop``. Used to capture *extra* attrs beyond what the bundle marker
    already carries (typically ``xml:id``)."""
    attrs = _attrs_to_dict(elem.attrib)
    for k in drop:
        attrs.pop(k, None)
    return attrs


def _emit_seg(seg, text_buf: list[str], markers: list[Marker], offset_fn,
              markers_info: dict):
    seg_id = _xmlid(seg)
    markers.append(Marker(type="tls:seg", offset=offset_fn(),
                          content="", id=seg_id))
    extras = _attrs_minus(seg, ("xml:id",))
    if seg_id and extras:
        markers_info[seg_id] = {"type": "tls:seg", "attrs": extras}
    # Walk the seg in mixed-content order. seg.text is the leading text;
    # each child's .text is its content, .tail is the text following it.
    # Whitespace-only seg.text (formatting between <seg>...<c/>) is dropped.
    if seg.text and seg.text.strip():
        text_buf.append(unicodedata.normalize("NFC", seg.text))
    for child in seg.iterchildren():
        tag = etree.QName(child).localname
        if tag == "c":
            markers.append(Marker(type="punctuation", offset=offset_fn(),
                                  content=child.get("n", ""), id=""))
        elif tag == "pb":
            mid = _xmlid(child)
            markers.append(Marker(type="page-break", offset=offset_fn(),
                                  content="", id=mid))
            pb_extras = _attrs_minus(child, ("xml:id",))
            if mid and pb_extras:
                markers_info[mid] = {"type": "page-break", "attrs": pb_extras}
        # else: skip unknown inline elements but keep their text content
        if child.tail:
            tail = child.tail
            # Strip whitespace-only tails (formatting artifacts in the XML).
            if tail.strip():
                text_buf.append(unicodedata.normalize("NFC", tail))


def _seg_text(seg) -> str:
    """Text content of a head's inner <seg>, joining mixed content."""
    if seg is None:
        return ""
    parts: list[str] = []
    if seg.text:
        parts.append(seg.text)
    for child in seg.iterchildren():
        tag = etree.QName(child).localname
        if tag == "c":
            # Heads typically have no <c>, but if so the punctuation goes
            # into the head's text content (heads aren't broken into
            # markers the same way bodies are).
            parts.append(child.get("n", ""))
        elif child.text:
            parts.append(child.text)
        if child.tail and child.tail.strip():
            parts.append(child.tail)
    return "".join(parts)


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
    tree = etree.parse(str(path))
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
    tree = etree.parse(str(text_xml))
    md: dict = {}

    title_el = tree.find(f".//{_q('teiHeader')}//{_q('titleStmt')}/{_q('title')}")
    if title_el is not None and title_el.text:
        md["title"] = title_el.text.strip()

    md["identifiers"] = {"krp": text_id}

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
