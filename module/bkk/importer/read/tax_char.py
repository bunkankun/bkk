"""Reader for TLS taxchar records."""

from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from ..ir import TaxCharBundle, TaxCharPronunciation, TaxCharSense
from ._provenance import lift_source
from .concept import normalize_uuid


TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _q(local: str, ns: str = TEI_NS) -> str:
    return f"{{{ns}}}{local}"


# The pron line is shaped as
#   [<head char>] <pinyin> [<head char>] (OC: <oc> MC: <mc>) <fanqie> <tone>
#   廣韻：【<guangyun def> 】
# The head char may appear before or after the pinyin, or not at all; the
# guangyun block may have stray whitespace before 】. We anchor on the
# ``(OC: … MC: …)`` block (which is always present) and parse around it.
_PRON_OC_RE = re.compile(
    r"\(\s*OC:\s*(?P<oc>[^\s)]+)\s+MC:\s*(?P<mc>[^\s)]+)\s*\)",
    re.DOTALL,
)
_PRON_SUFFIX_RE = re.compile(
    r"^\s*(?P<fanqie>\S+)\s+(?P<tone>\S)\s*廣韻：\s*【\s*(?P<def>.*?)\s*】",
    re.DOTALL,
)
# Alternate suffix shape: "反切： 陟離； 聲調： 平； 廣韻：【…】".
_PRON_SUFFIX_ALT_RE = re.compile(
    r"\s*反切：\s*(?P<fanqie>\S*?)；?\s*聲調：\s*(?P<tone>\S?)；?\s*廣韻：\s*【\s*(?P<def>.*?)\s*】",
    re.DOTALL,
)


def read_tax_chars(xml_path: Path) -> list[TaxCharBundle]:
    """Parse every ``<div type="taxchar">`` in a TEI source file."""
    if not xml_path.exists():
        raise FileNotFoundError(xml_path)

    parser = etree.XMLParser(recover=True, remove_blank_text=False)
    tree = etree.parse(str(xml_path), parser)
    root = tree.getroot()

    records: list[TaxCharBundle] = []
    for div in root.findall(f".//{_q('div')}[@type='taxchar']"):
        records.append(_parse_taxchar(div, xml_path))
    return records


def _parse_taxchar(div, xml_path: Path) -> TaxCharBundle:
    raw_id = div.get(f"{{{XML_NS}}}id") or ""
    uuid = normalize_uuid(raw_id)
    # A handful of taxchar.xml records spell xml:id as ``uuidXXXX…`` with no
    # dash separator; ``normalize_uuid`` only strips the canonical ``uuid-``
    # form, so absorb the variant here.
    if uuid.startswith("uuid") and len(uuid) == 4 + 36:
        uuid = uuid[4:]
    if not uuid:
        raise ValueError("taxchar is missing xml:id")

    heads = [_text(h) for h in div.findall(_q("head")) if _text(h)]

    pronunciations: list[TaxCharPronunciation] = []
    unattributed: list[TaxCharSense] = []

    top_list = div.find(_q("list"))
    if top_list is not None:
        for item in top_list.findall(_q("item")):
            if (item.get("type") or "").strip() == "pron":
                pronunciations.append(_parse_pron_item(item))
            else:
                sense = _parse_sense_item(item)
                if sense is not None:
                    unattributed.append(sense)

    metadata: dict = {"source_file": xml_path.name}
    metadata.update(lift_source(div))

    return TaxCharBundle(
        uuid=uuid,
        heads=heads,
        pronunciations=pronunciations,
        unattributed_senses=unattributed,
        metadata=metadata,
    )


def _parse_pron_item(item) -> TaxCharPronunciation:
    raw = _own_text_before_list(item)
    parsed = _parse_pron_string(raw)
    senses: list[TaxCharSense] = []
    nested = item.find(_q("list"))
    if nested is not None:
        for child in nested.findall(_q("item")):
            sense = _parse_sense_item(child)
            if sense is not None:
                senses.append(sense)
    return TaxCharPronunciation(
        reading=parsed.get("reading"),
        old_chinese=parsed.get("old_chinese"),
        middle_chinese=parsed.get("middle_chinese"),
        fanqie=parsed.get("fanqie"),
        tone=parsed.get("tone"),
        guangyun=parsed.get("guangyun"),
        raw=raw if not parsed else None,
        senses=senses,
    )


def _parse_pron_string(raw: str) -> dict:
    if not raw:
        return {}
    oc_match = _PRON_OC_RE.search(raw)
    if oc_match is not None:
        prefix = raw[: oc_match.start()].strip()
        suffix = raw[oc_match.end():]
    else:
        # No "(OC: ... MC: ...)" block — some records use only the bare
        # "反切： … 聲調： … 廣韻：【…】" shape after the reading.
        alt = _PRON_SUFFIX_ALT_RE.search(raw)
        if alt is None:
            return {}
        prefix = raw[: alt.start()].strip()
        suffix = raw[alt.start():]

    reading = next(
        (tok for tok in prefix.split() if _is_pinyin_token(tok)),
        None,
    )
    if not reading:
        return {}

    out: dict = {"reading": reading}
    if oc_match is not None:
        out["old_chinese"] = oc_match.group("oc")
        out["middle_chinese"] = oc_match.group("mc")
    suffix_match = _PRON_SUFFIX_RE.match(suffix) or _PRON_SUFFIX_ALT_RE.match(suffix)
    if suffix_match:
        out["fanqie"] = suffix_match.group("fanqie")
        out["tone"] = suffix_match.group("tone")
        out["guangyun"] = " ".join(suffix_match.group("def").split())
    return out


def _is_pinyin_token(token: str) -> bool:
    """True if ``token`` is non-empty and contains no CJK ideograph."""
    return bool(token) and all(ord(c) < 0x3400 for c in token)


def _parse_sense_item(item) -> TaxCharSense | None:
    gloss = _gloss_before_ref(item)
    ref = item.find(_q("ref"))
    concept_uuid: str | None = None
    concept_label: str | None = None
    if ref is not None:
        target = normalize_uuid(ref.get("target") or "")
        concept_uuid = target or None
        label = _text(ref)
        concept_label = label or None

    children: list[TaxCharSense] = []
    nested = item.find(_q("list"))
    if nested is not None:
        for child in nested.findall(_q("item")):
            child_sense = _parse_sense_item(child)
            if child_sense is not None:
                children.append(child_sense)

    if not gloss and concept_uuid is None and concept_label is None and not children:
        return None
    return TaxCharSense(
        gloss=gloss,
        concept_uuid=concept_uuid,
        concept_label=concept_label,
        children=children,
    )


def _own_text_before_list(item) -> str:
    """Concatenate text + child text/tails up to (but not including) the first inner <list>."""
    parts: list[str] = []
    if item.text:
        parts.append(item.text)
    for child in item:
        if etree.QName(child.tag).localname == "list":
            break
        inner = _text(child)
        if inner:
            parts.append(inner)
        if child.tail:
            parts.append(child.tail)
    return " ".join("".join(parts).split())


def _gloss_before_ref(item) -> str:
    """Get the leading gloss text before any <ref>/<list>, stripping one trailing '>'."""
    parts: list[str] = []
    if item.text:
        parts.append(item.text)
    for child in item:
        local = etree.QName(child.tag).localname
        if local in ("ref", "list"):
            break
        inner = _text(child)
        if inner:
            parts.append(inner)
        if child.tail:
            parts.append(child.tail)
    text = " ".join("".join(parts).split())
    return text.rstrip("> ").rstrip()


def _text(el) -> str | None:
    if el is None:
        return None
    text = " ".join("".join(el.itertext()).split())
    return text or None
