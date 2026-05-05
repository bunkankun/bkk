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


class _DivCtx:
    """One open ``<div>`` during export — current paragraph/seg cursors and
    per-div ``<p>`` attribute replay state. The stack-bottom is the juan div;
    each ``tls:div-start`` pushes a fresh context for the nested div.
    """

    __slots__ = (
        "el", "p_attrs_list", "p_index", "head_attrs",
        "head_inner_seg_attrs", "current_p", "current_seg",
    )

    def __init__(self, el: etree._Element, div_info: dict):
        self.el = el
        self.p_attrs_list = div_info.get("p_attrs", []) or []
        self.p_index = 0
        self.head_attrs = div_info.get("head_attrs", {})
        self.head_inner_seg_attrs = div_info.get("head_inner_seg_attrs", {})
        self.current_p: etree._Element | None = None
        self.current_seg: etree._Element | None = None


def _next_text_consuming_offset(markers: list, start_idx: int,
                                text_len: int) -> int:
    """Offset of the next marker after ``markers[start_idx]`` whose presence
    closes the current ``<head>`` text region. The head's seg text extends up
    to (but not including) that offset. If no further marker exists, returns
    ``text_len`` so any trailing text falls into the head's seg.
    """
    for j in range(start_idx + 1, len(markers)):
        return markers[j].offset
    return text_len


def _build_div(section: Section, divs_info: dict, markers_info: dict
               ) -> etree._Element:
    """Emit one ``<div>`` from a Section + sidecar div/marker entries.

    Walks ``section.markers`` in order, splicing text from ``section.text``
    between markers. Nested ``<div>`` elements are reconstructed from
    ``tls:div-start`` / ``tls:div-end`` markers via a context stack so each
    nested div carries its own attrs, head, and ``<p>`` cursors.
    """
    juan_info = divs_info.get(section.head_marker_id, {})
    juan_div = etree.Element(_q("div"))
    _set_attrs(juan_div, juan_info.get("div_attrs", {}))

    stack: list[_DivCtx] = [_DivCtx(juan_div, juan_info)]

    text = section.text
    markers = section.markers
    last_offset = 0

    def active() -> _DivCtx:
        return stack[-1]

    def text_target(ctx: _DivCtx) -> etree._Element:
        return ctx.current_seg if ctx.current_seg is not None else (
            ctx.current_p if ctx.current_p is not None else ctx.el
        )

    i = 0
    while i < len(markers):
        m = markers[i]
        ctx = active()

        # Splice any text gap from the last marker to this one. The gap goes
        # into whichever container is currently open in the active div
        # (seg > p > div).
        gap = text[last_offset:m.offset]
        if gap:
            _append_text(text_target(ctx), gap)
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
                ctx.current_seg = None
            pb = etree.SubElement(text_target(ctx), _q("pb"))
            if m.id:
                pb.set(_q("id", XML_NS), m.id)
            _set_attrs(pb, _ed_attrs_for(m.id, markers_info))
            i += 1
            continue

        if m.type == "tls:head":
            head_el = etree.SubElement(ctx.el, _q("head"))
            _set_attrs(head_el, ctx.head_attrs)
            seg_el = etree.SubElement(head_el, _q("seg"))
            if m.id:
                seg_el.set(_q("id", XML_NS), m.id)
            _set_attrs(seg_el, ctx.head_inner_seg_attrs)
            # The head's text content occupies section.text from this
            # marker's offset up to the next marker (or end of text).
            end = _next_text_consuming_offset(markers, i, len(text))
            seg_el.text = text[m.offset:end] or None
            last_offset = end
            i += 1
            continue

        if m.type == "paragraph-break":
            if ctx.current_p is None:
                ctx.current_p = etree.SubElement(ctx.el, _q("p"))
                if ctx.p_index < len(ctx.p_attrs_list):
                    _set_attrs(ctx.current_p, ctx.p_attrs_list[ctx.p_index])
                ctx.p_index += 1
                ctx.current_seg = None
            else:
                ctx.current_p = None
                ctx.current_seg = None
            i += 1
            continue

        if m.type == "tls:seg":
            parent = ctx.current_p if ctx.current_p is not None else ctx.el
            ctx.current_seg = etree.SubElement(parent, _q("seg"))
            if m.id:
                ctx.current_seg.set(_q("id", XML_NS), m.id)
            _set_attrs(ctx.current_seg, _ed_attrs_for(m.id, markers_info))
            i += 1
            continue

        if m.type == "punctuation":
            # Source XML places <c/> inside <seg>, alongside seg text and
            # other inline children. Don't close the current seg — leave it
            # open so subsequent text and punctuation continue inside it.
            c = etree.SubElement(text_target(ctx), _q("c"))
            if m.content:
                c.set("n", m.content)
            i += 1
            continue

        if m.type == "tls:div-start":
            # Open a nested div as a child of the active div, push a fresh
            # context. Any open <p> in the parent is closed (nested divs
            # appear between paragraphs in TLS sources, not inside one).
            ctx.current_p = None
            ctx.current_seg = None
            nested_info = divs_info.get(m.id, {}) if m.id else {}
            nested_div = etree.SubElement(ctx.el, _q("div"))
            _set_attrs(nested_div, nested_info.get("div_attrs", {}))
            stack.append(_DivCtx(nested_div, nested_info))
            i += 1
            continue

        if m.type == "tls:div-end":
            # Close the nested div: pop its context. Defensive: never pop
            # the juan div from the stack base.
            if len(stack) > 1:
                stack.pop()
            i += 1
            continue

        # Unknown marker type: skip without crashing.
        i += 1

    # Trailing text after the last marker.
    gap = text[last_offset:]
    if gap:
        _append_text(text_target(active()), gap)

    return juan_div


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
