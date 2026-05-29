"""TLS-shaped translation reader.

Parses one TEI ``type="transl"`` file (e.g.
``tls-data/translations/KR1h0004-en.xml``) into a
:class:`~bkk.importer.ir.TranslationBundle`.

The reader is independent of :mod:`bkk.importer.read.tls`: TLS source-text
files carry divs, juans, and annotations; translations carry only a flat
list of ``<seg>`` elements addressed by ``corresp`` to source marker ids.
Sharing code between the two would force concept conflation.

Per the migration recipe in ``bunkankun.md`` (lines 602-621):
- Each ``<seg>`` becomes a translation segment; empty segs are dropped.
- ``<teiHeader>`` is lifted into a metadata dict for the YAML manifest.
- ``corresp`` values are stripped of their leading ``#``; the writer
  later strips the ``<text-id>_<edition>_`` prefix as well, since the
  source pin in the bundle's manifest makes those implicit.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from lxml import etree

from ..ir import TranslationBundle, TranslationSegment


TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _q(local: str, ns: str = TEI_NS) -> str:
    return f"{{{ns}}}{local}"


# Marker id shape (per memory project_marker_id_format):
#   <text-id>_<edition>_<location>
# location starts with the juan label (digits) followed by "-" and more.
_MARKER_RE = re.compile(r"^(?P<text>[^_]+)_(?P<edition>[^_]+)_(?P<loc>.+)$")
_LOC_JUAN_RE = re.compile(r"^(?P<juan>\d+)(?:-.*)?$")


def _parse_marker_location(marker_id: str) -> tuple[str, str] | None:
    """Split a marker id into ``(location, juan_label)``.

    Returns ``None`` when the id doesn't fit the canonical shape.
    The location strips the leading ``<text-id>_<edition>_`` prefix;
    juan_label is the leading digit run of the location.
    """
    m = _MARKER_RE.match(marker_id)
    if not m:
        return None
    loc = m.group("loc")
    j = _LOC_JUAN_RE.match(loc)
    if not j:
        return None
    return loc, j.group("juan")


def read_translation(xml_path: Path, *, language_hint: str | None = None,
                     bundle_id_hint: str | None = None) -> TranslationBundle:
    """Parse a TLS-shaped translation file into a TranslationBundle.

    ``language_hint`` and ``bundle_id_hint`` come from the filename and
    take precedence over what the file itself declares (TLS samples have
    been observed to carry mislabelled per-seg ``xml:lang`` even in
    non-English translations — see KR1h0004-fr).
    """
    if not xml_path.exists():
        raise FileNotFoundError(xml_path)

    parser = etree.XMLParser(recover=True)
    tree = etree.parse(str(xml_path), parser)
    root = tree.getroot()

    type_attr = (root.get("type") or "").strip()
    if type_attr != "transl":
        raise ValueError(
            f"{xml_path.name}: TEI/@type is {type_attr!r}, expected 'transl'"
        )

    bundle_id = bundle_id_hint or xml_path.stem
    metadata, source_text_id, declared_lang, source_info = _parse_header(
        root, xml_path,
    )
    language = language_hint or declared_lang or ""
    if not language:
        print(
            f"warning: {xml_path.name}: cannot determine target language "
            f"(no filename hint and no <lang xml:lang=...> in header)",
            file=sys.stderr,
        )

    segments = _parse_segments(root, xml_path)

    return TranslationBundle(
        bundle_id=bundle_id,
        source_text_id=source_text_id,
        language=language,
        metadata=metadata,
        segments=segments,
        source_info=source_info,
    )


def _parse_header(root, xml_path: Path) -> tuple[dict, str, str, dict]:
    """Return (metadata, source_text_id, declared_language, source_info)."""
    md: dict = {}

    title_el = root.find(
        f".//{_q('teiHeader')}//{_q('titleStmt')}/{_q('title')}"
    )
    if title_el is not None and (title_el.text or "").strip():
        md["title"] = title_el.text.strip()

    responsibility: list[dict] = []
    for editor in root.findall(
        f".//{_q('teiHeader')}//{_q('titleStmt')}/{_q('editor')}"
    ):
        name = (editor.text or "").strip()
        if not name:
            continue
        role = (editor.get("role") or "editor").strip()
        responsibility.append({"role": role, "name": name})

    publication: dict = {}
    pub_stmt = root.find(f".//{_q('teiHeader')}//{_q('publicationStmt')}")
    if pub_stmt is not None:
        notes: list[str] = []
        for child in pub_stmt:
            tag = etree.QName(child.tag).localname
            text = "".join(child.itertext()).strip()
            if not text:
                continue
            if tag in ("p", "ab"):
                notes.append(text)
            elif tag == "availability":
                publication.setdefault("availability", text)
                status = (child.get("status") or "").strip()
                if status:
                    publication.setdefault("availability_status", status)
        if notes:
            publication["note"] = notes[0] if len(notes) == 1 else notes
    if publication:
        md["publication"] = publication

    license_val = (publication.get("availability") if publication else None)
    md["license"] = license_val or "unknown — review required"

    source_text_id = ""
    original_title = ""
    declared_lang = ""
    source_desc = root.find(f".//{_q('teiHeader')}//{_q('sourceDesc')}")
    if source_desc is not None:
        for bibl in source_desc.iter(_q("bibl")):
            corresp = (bibl.get("corresp") or "").strip()
            if corresp.startswith("#"):
                source_text_id = corresp[1:]
                title_in_bibl = bibl.find(_q("title"))
                if title_in_bibl is not None and (title_in_bibl.text or "").strip():
                    original_title = title_in_bibl.text.strip()
            else:
                # Publication bibl: extract year for the date field.
                bibl_text = "".join(bibl.itertext()).strip()
                year_m = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", bibl_text)
                if year_m:
                    md["date"] = year_m.group(1)
        lang_el = source_desc.find(f".//{_q('lang')}")
        if lang_el is not None:
            xml_lang = lang_el.get(_q("lang", XML_NS)) or ""
            if xml_lang:
                declared_lang = xml_lang.strip()

    if original_title:
        md["original_title"] = original_title

    creation = root.find(f".//{_q('teiHeader')}//{_q('creation')}")
    if creation is not None:
        # TLS creation lines look like:
        #   <creation [resp="#CH"]>Initially created: <date>…</date> by CH</creation>
        # Extract the "by <name>" tail; fall back to @resp's value. The role is
        # always ``creator`` (the resp attribute is a marker, not a role name).
        tail = (creation.text or "")
        for e in creation:
            tail += (e.tail or "")
        m = re.search(r"\bby\s+(.+?)\s*$", tail.strip())
        name = ""
        if m:
            name = m.group(1).strip()
        else:
            resp = (creation.get("resp") or "").strip().lstrip("#")
            if resp:
                name = resp
        if name:
            entry = {"role": "creator", "name": name}
            if not any(
                r.get("role") == "creator" and r.get("name") == name
                for r in responsibility
            ):
                responsibility.append(entry)

    if responsibility:
        md["responsibility"] = responsibility

    md = _ordered_metadata(md)

    source_info: dict = {
        "source_files": [{"role": "translation", "path": xml_path.name}],
    }
    tei_header = root.find(_q("teiHeader"))
    if tei_header is not None:
        source_info["teiHeader_xml"] = etree.tostring(
            tei_header, encoding="unicode",
        )

    return md, source_text_id, declared_lang, source_info


def _ordered_metadata(md: dict) -> dict:
    """Return md with keys in the canonical YAML-header order."""
    order = (
        "title", "original_title", "language", "responsibility",
        "publication", "license", "date",
    )
    out: dict = {}
    for k in order:
        if k in md:
            out[k] = md[k]
    for k, v in md.items():
        if k not in out:
            out[k] = v
    return out


def _parse_segments(root, xml_path: Path) -> list[TranslationSegment]:
    """Walk every <seg> in the body, in document order."""
    body = root.find(f".//{_q('text')}/{_q('body')}")
    if body is None:
        return []

    segments: list[TranslationSegment] = []
    unparseable = 0
    for seg in body.iter(_q("seg")):
        text = "".join(seg.itertext()).strip()
        if not text:
            continue

        raw_corresp = (seg.get("corresp") or "").strip()
        if not raw_corresp:
            continue
        ids = [tok.lstrip("#") for tok in raw_corresp.split() if tok]
        if not ids:
            continue

        parsed = _parse_marker_location(ids[0])
        if parsed is None:
            unparseable += 1
            juan_label = "_unknown"
            corresp_locs = ids
        else:
            corresp_locs = []
            for full in ids:
                p = _parse_marker_location(full)
                corresp_locs.append(p[0] if p else full)
            juan_label = parsed[1]

        segments.append(TranslationSegment(
            corresp=corresp_locs,
            text=text,
            juan_label=juan_label,
            lang=(seg.get(_q("lang", XML_NS)) or None),
            resp=((seg.get("resp") or "").lstrip("#") or None),
            modified=(seg.get("modified") or None),
        ))

    if unparseable:
        print(
            f"warning: {xml_path.name}: {unparseable} seg(s) with "
            f"unparseable corresp; bucketed under juan '_unknown'",
            file=sys.stderr,
        )

    return segments
