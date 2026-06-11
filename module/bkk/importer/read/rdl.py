"""Reader for ``tls-data/notes/rdl/rdl.xml`` rhetorical-device attestations.

``rdl.xml`` is a single global TLS document whose ``<tls:span type='rdl'>``
elements anchor one rhetorical-device record (defined in
``tls-data/core/rhetorical-devices.xml``) to one (or two, for stretched
spans) text-bundle marker(s). Each span becomes one :class:`Annotation`
suitable for the existing TLS annotation pipeline.

The whole file is parsed once and cached by path, since :func:`read_tls`
calls into this module per-text in bulk imports.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from lxml import etree

from ..ir import Annotation


TLS_NS = "http://hxwd.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _q(local: str, ns: str = TLS_NS) -> str:
    return f"{{{ns}}}{local}"


def read_rdl_annotations(rdl_path: Path, text_id: str) -> list[Annotation]:
    """Return rdl spans that resolve to ``text_id`` as :class:`Annotation`."""
    if not rdl_path.exists():
        return []
    by_text = _load_rdl(str(rdl_path))
    return list(by_text.get(text_id, ()))


@lru_cache(maxsize=4)
def _load_rdl(path_str: str) -> dict[str, tuple[Annotation, ...]]:
    """Parse the whole rdl.xml once, returning a dict keyed by text id."""
    parser = etree.XMLParser(recover=True, remove_blank_text=False)
    tree = etree.parse(path_str, parser)
    root = tree.getroot()

    bucket: dict[str, list[Annotation]] = {}
    for span in root.iter(_q("span")):
        if (span.get("type") or "").strip() != "rdl":
            continue
        ann = _span_to_annotation(span)
        if ann is None:
            continue
        text_id = _text_id_from_marker(ann.marker_id)
        if not text_id:
            continue
        bucket.setdefault(text_id, []).append(ann)
    return {tid: tuple(anns) for tid, anns in bucket.items()}


def _span_to_annotation(span) -> Annotation | None:
    """Convert one ``<tls:span type='rdl'>`` to an :class:`Annotation`."""
    span_id = (span.get(f"{{{XML_NS}}}id") or "").strip()
    rhet_dev = (span.get("rhet-dev") or "").strip()
    rhet_dev_id_raw = (span.get("rhet-dev-id") or "").strip()
    rhet_dev_id = _strip_uuid_prefix(rhet_dev_id_raw)

    role_to_srcline: dict[str, tuple[str, str, str]] = {}
    for text in span.findall(_q("text")):
        role = (text.get("role") or "").strip() or "span"
        srcline = text.find(_q("srcline"))
        if srcline is None:
            continue
        target = (srcline.get("target") or "").strip().lstrip("#")
        title = (srcline.get("title") or "").strip()
        content = "".join(srcline.itertext()).strip()
        if not target:
            continue
        role_to_srcline[role] = (target, title, content)

    primary = (
        role_to_srcline.get("span")
        or role_to_srcline.get("span-start")
    )
    if primary is None:
        return None
    marker_id, title, text_content = primary
    length = len(text_content) if text_content else 1

    end_marker_id: str | None = None
    end_length: int | None = None
    end_text: str | None = None
    end_title: str | None = None
    end_srcline = role_to_srcline.get("span-end")
    if end_srcline is not None and "span" not in role_to_srcline:
        end_marker_id, end_title, end_text = end_srcline
        end_length = len(end_text) if end_text else 1

    note_el = span.find(_q("note"))
    note_text = (
        " ".join("".join(note_el.itertext()).split())
        if note_el is not None else None
    )

    payload: dict = {
        "kind": "rhetorical-device-attestation",
        "rhet_dev": rhet_dev,
        "rhet_dev_id": rhet_dev_id,
    }
    if span_id:
        payload["id"] = span_id
    if note_text:
        payload["note"] = note_text
    src: dict = {}
    if title:
        src["title"] = title
    if text_content:
        src["text"] = text_content
    if end_marker_id:
        if end_title:
            src["end_title"] = end_title
        if end_text:
            src["end_text"] = end_text
    if src:
        payload["source"] = src

    return Annotation(
        marker_id=marker_id,
        offset=0,
        length=length,
        payload=payload,
        source_role="tls:span/rdl",
        provenance="rdl",
        end_marker_id=end_marker_id,
        end_length=end_length,
    )


def _text_id_from_marker(marker_id: str) -> str | None:
    """Extract the Kanripo text id (segment before the first ``_``)."""
    if not marker_id or marker_id.startswith("uuid-"):
        return None
    text_id = marker_id.split("_", 1)[0]
    if not text_id or text_id == marker_id:
        return None
    return text_id


def _strip_uuid_prefix(value: str) -> str:
    value = (value or "").strip().lstrip("#")
    if value.startswith("uuid-"):
        return value[len("uuid-"):]
    return value
