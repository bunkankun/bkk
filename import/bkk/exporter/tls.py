"""TLS exporter: BKK bundle + sidecar → text/swl/doc TEI/XML files.

The recipe entry point dispatches by format from :mod:`bkk.exporter.cli`.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from ..importer.ir import Bundle, Section
from .read_bundle import read_bundle
from .recipe import Recipe
from .xml_tree import (
    TEI_NS,
    TLS_NS,
    XML_NS,
    _expand_attr,
    tree_to_element,
)


_TEI_NSMAP = {None: TEI_NS, "tls": TLS_NS}


def _q(local: str, ns: str = TEI_NS) -> str:
    return f"{{{ns}}}{local}"


def _set_attrs(el: etree._Element, attrs: dict) -> None:
    for k, v in attrs.items():
        el.set(_expand_attr(k), v)


def _append_text(el: etree._Element, text: str) -> None:
    """Append ``text`` to an element's text-content stream.

    XML's serialization model puts trailing text either in ``element.text``
    (when the element has no children) or in the last child's ``tail``. This
    helper picks the right slot.
    """
    if not text:
        return
    if len(el) == 0:
        el.text = (el.text or "") + text
    else:
        last = el[-1]
        last.tail = (last.tail or "") + text


def _ed_attrs_for(marker_id: str, markers_info: dict) -> dict:
    return (markers_info.get(marker_id) or {}).get("attrs", {})


def _build_div(section: Section, divs_info: dict, markers_info: dict
               ) -> etree._Element:
    """Emit one ``<div>`` from a Section + sidecar div/marker entries.

    Walks ``section.markers`` in order, splicing text from ``section.text``
    between markers. Same-offset clusters are handled with a one-marker
    lookahead so page-breaks at seg boundaries land between segs (in ``<p>``)
    rather than inside the previous seg.
    """
    div_info = divs_info.get(section.head_marker_id, {})
    div = etree.Element(_q("div"))
    _set_attrs(div, div_info.get("div_attrs", {}))

    text = section.text
    markers = section.markers
    last_offset = 0
    current_p: etree._Element | None = None
    current_seg: etree._Element | None = None
    p_index = 0
    p_attrs_list = div_info.get("p_attrs", []) or []

    i = 0
    while i < len(markers):
        m = markers[i]

        # Splice any text gap from the last marker to this one. The gap goes
        # into whichever container is currently open (seg > p > div).
        gap = text[last_offset:m.offset]
        if gap:
            target = current_seg if current_seg is not None else (
                current_p if current_p is not None else div
            )
            _append_text(target, gap)
        last_offset = m.offset

        if m.type == "page-break":
            # If a non-pb marker shares this offset *after* the pb, the
            # previous seg has logically ended; close it so the pb is a
            # sibling of <seg>, not a child.
            j = i + 1
            close_seg = False
            while j < len(markers) and markers[j].offset == m.offset:
                if markers[j].type != "page-break":
                    close_seg = True
                    break
                j += 1
            if close_seg:
                current_seg = None
            target = current_seg if current_seg is not None else (
                current_p if current_p is not None else div
            )
            pb = etree.SubElement(target, _q("pb"))
            if m.id:
                pb.set(_q("id", XML_NS), m.id)
            _set_attrs(pb, _ed_attrs_for(m.id, markers_info))
            i += 1
            continue

        if m.type == "tls:head":
            head_attrs = div_info.get("head_attrs", {})
            head_el = etree.SubElement(div, _q("head"))
            _set_attrs(head_el, head_attrs)
            seg_el = etree.SubElement(head_el, _q("seg"))
            if m.id:
                seg_el.set(_q("id", XML_NS), m.id)
            _set_attrs(seg_el, div_info.get("head_inner_seg_attrs", {}))
            seg_el.text = section.head_text
            last_offset = m.offset + len(section.head_text)
            i += 1
            continue

        if m.type == "paragraph-break":
            if current_p is None:
                current_p = etree.SubElement(div, _q("p"))
                if p_index < len(p_attrs_list):
                    _set_attrs(current_p, p_attrs_list[p_index])
                p_index += 1
                current_seg = None
            else:
                current_p = None
                current_seg = None
            i += 1
            continue

        if m.type == "tls:seg":
            parent = current_p if current_p is not None else div
            current_seg = etree.SubElement(parent, _q("seg"))
            if m.id:
                current_seg.set(_q("id", XML_NS), m.id)
            _set_attrs(current_seg, _ed_attrs_for(m.id, markers_info))
            i += 1
            continue

        if m.type == "punctuation":
            # Source XML places <c/> inside <seg>, alongside seg text and
            # other inline children. Don't close the current seg — leave it
            # open so subsequent text and punctuation continue inside it.
            parent = current_seg if current_seg is not None else (
                current_p if current_p is not None else div
            )
            c = etree.SubElement(parent, _q("c"))
            if m.content:
                c.set("n", m.content)
            i += 1
            continue

        # Unknown marker type: skip without crashing.
        i += 1

    # Trailing text after the last marker.
    gap = text[last_offset:]
    if gap:
        target = current_seg if current_seg is not None else (
            current_p if current_p is not None else div
        )
        _append_text(target, gap)

    return div


def build_text_xml(bundle: Bundle) -> bytes:
    """Build the text TEI XML bytes for ``bundle`` using its sidecar."""
    info = bundle.source_info or {}
    tei_info = info.get("tei", {}) or {}
    divs_info = info.get("divs", {}) or {}
    markers_info = info.get("markers", {}) or {}

    root = etree.Element(_q("TEI"), nsmap=_TEI_NSMAP)
    _set_attrs(root, tei_info.get("root_attrs", {}))

    header = tei_info.get("header")
    if header is not None:
        root.append(tree_to_element(header))

    text_el = etree.SubElement(root, _q("text"))
    body_el = etree.SubElement(text_el, _q("body"))

    for juan in bundle.juans:
        for section in juan.sections:
            body_el.append(_build_div(section, divs_info, markers_info))

    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", pretty_print=True,
    )


def build_ann_xml(bundle: Bundle, provenance: str) -> bytes | None:
    """Build the annotation TEI XML bytes for one provenance (``swl`` or
    ``doc``). Returns ``None`` if the bundle has no annotations for that
    provenance.
    """
    info = bundle.source_info or {}
    ann_files = (info.get("ann_files") or {})
    envelope = ann_files.get(provenance)
    annotations_info = info.get("annotations") or {}

    entries: list[tuple[str, dict]] = []
    for ann in annotations_info.values():
        if ann.get("provenance") != provenance:
            continue
        entries.append((ann["seg_id"], ann["tree"]))
    if not entries:
        return None

    if envelope is None:
        # No envelope captured but annotations exist — can't reconstruct the
        # outer file shape. Fail loudly so the user notices.
        raise ValueError(
            f"sidecar has {provenance} annotations but no ann_files envelope"
        )

    root = etree.Element(_q("TEI"), nsmap=_TEI_NSMAP)
    _set_attrs(root, envelope.get("tei_root_attrs", {}))

    header = envelope.get("tei_header")
    if header is not None:
        root.append(tree_to_element(header))

    text_el = etree.SubElement(root, _q("text"))
    body_el = etree.SubElement(text_el, _q("body"))
    div_el = etree.SubElement(body_el, _q("div"))
    head_el = etree.SubElement(div_el, _q("head"))
    head_el.text = envelope.get("body_div_head", "Annotations")

    p_el = etree.SubElement(div_el, _q("p"))
    _set_attrs(p_el, envelope.get("p_attrs", {}))

    seg_lines = envelope.get("seg_lines") or {}

    # Group annotations by seg_id, preserving first-seen order.
    grouped: dict[str, list[dict]] = {}
    for seg_id, tree in entries:
        grouped.setdefault(seg_id, []).append(tree)

    for seg_id, trees in grouped.items():
        seg_el = etree.SubElement(p_el, _q("seg"))
        if seg_id:
            seg_el.set(_q("id", XML_NS), seg_id)
        line_text = seg_lines.get(seg_id)
        if line_text is not None:
            line_el = etree.SubElement(seg_el, _q("line"))
            line_el.text = line_text
        for tree in trees:
            seg_el.append(tree_to_element(tree))

    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", pretty_print=True,
    )


def export_tls_from_recipe(recipe: Recipe) -> list[Path]:
    """Export a TLS bundle to TEI/XML files. Returns the relative paths
    written under ``recipe.output_dir``. Annotation files are placed in
    ``swl/`` and ``doc/`` subdirectories so collisions on the shared
    ``-ann.xml`` filename are avoided.
    """
    bundle = read_bundle(recipe.bundle)
    recipe.output_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []

    text_path = recipe.output_dir / f"{bundle.text_id}.xml"
    text_path.write_bytes(build_text_xml(bundle))
    written.append(Path(text_path.name))

    for provenance in ("swl", "doc"):
        xml_bytes = build_ann_xml(bundle, provenance)
        if xml_bytes is None:
            continue
        sub = recipe.output_dir / provenance
        sub.mkdir(parents=True, exist_ok=True)
        path = sub / f"{bundle.text_id}-ann.xml"
        path.write_bytes(xml_bytes)
        written.append(Path(provenance) / path.name)

    return written
