"""Reader for MODS bibliography records."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from ..ir import (
    BibliographyBundle,
    BibliographyContributor,
    BibliographyGenre,
    BibliographyNote,
    BibliographyTitle,
)
from .concept import normalize_uuid


MODS_NS = "http://www.loc.gov/mods/v3"


def _q(local: str) -> str:
    return f"{{{MODS_NS}}}{local}"


def read_bibliography(xml_path: Path) -> BibliographyBundle:
    """Parse one MODS XML file into a BibliographyBundle."""
    if not xml_path.exists():
        raise FileNotFoundError(xml_path)

    parser = etree.XMLParser(recover=True, remove_blank_text=False)
    tree = etree.parse(str(xml_path), parser)
    root = tree.getroot()
    if etree.QName(root.tag).localname != "mods":
        raise ValueError(f"{xml_path.name}: expected MODS <mods> root")

    uuid = normalize_uuid(root.get("ID") or xml_path.stem)
    notes = _parse_notes(root)
    citation_label = _first_note(notes, "bibliographic-reference")
    ref_usage = _first_note(notes, "ref-usage")

    return BibliographyBundle(
        uuid=uuid,
        citation_label=citation_label,
        ref_usage=ref_usage,
        resource_type=_child_text(root, "typeOfResource"),
        genres=_parse_genres(root),
        titles=_parse_titles(root),
        contributors=_parse_contributors(root),
        origin=_parse_origin(root),
        notes=[
            note for note in notes
            if note.type not in ("bibliographic-reference", "ref-usage")
        ],
        source={
            "format": "MODS",
            "version": root.get("version"),
        },
    )


def _text(el) -> str:
    if el is None:
        return ""
    return " ".join("".join(el.itertext()).split())


def _child_text(parent, local: str) -> str | None:
    child = parent.find(_q(local))
    text = _text(child)
    return text or None


def _parse_titles(root) -> list[BibliographyTitle]:
    titles: list[BibliographyTitle] = []
    for title_info in root.findall(_q("titleInfo")):
        title = _child_text(title_info, "title")
        if not title:
            continue
        titles.append(BibliographyTitle(
            title=title,
            subtitle=_child_text(title_info, "subTitle"),
            type=_attr(title_info, "type"),
            lang=_attr(title_info, "lang"),
            script=_attr(title_info, "script"),
            transliteration=_attr(title_info, "transliteration"),
        ))
    return titles


def _parse_contributors(root) -> list[BibliographyContributor]:
    contributors: list[BibliographyContributor] = []
    for name in root.findall(_q("name")):
        variants: dict[tuple[str | None, str | None], dict] = {}
        for index, part in enumerate(name.findall(_q("namePart"))):
            part_type = (part.get("type") or "").strip()
            key = (
                _attr(part, "lang"),
                _attr(part, "script"),
            )
            variant = variants.setdefault(key, {
                "lang": key[0],
                "script": key[1],
                "_index": index,
            })
            transliteration = _attr(part, "transliteration")
            if transliteration and "transliteration" not in variant:
                variant["transliteration"] = transliteration
            if part_type == "given":
                variant["given"] = _text(part) or None
            elif part_type == "family":
                variant["family"] = _text(part) or None
        roles = [
            _text(role_term)
            for role_term in name.findall(f"{_q('role')}/{_q('roleTerm')}")
            if _text(role_term)
        ]
        names = [
            _drop_empty({
                k: v for k, v in variant.items()
                if k != "_index"
            })
            for variant in sorted(
                variants.values(), key=lambda v: int(v.get("_index") or 0),
            )
        ]
        primary = _primary_name(names)
        contributors.append(BibliographyContributor(
            type=_attr(name, "type"),
            roles=roles,
            given=primary.get("given"),
            family=primary.get("family"),
            lang=primary.get("lang"),
            script=primary.get("script"),
            names=names,
        ))
    return contributors


def _primary_name(names: list[dict]) -> dict:
    for name in names:
        if name.get("script") == "Latn":
            return name
    return names[0] if names else {}


def _parse_origin(root) -> dict:
    origin_info = root.find(_q("originInfo"))
    if origin_info is None:
        return {}
    out: dict = {}
    place = origin_info.find(f"{_q('place')}/{_q('placeTerm')}")
    if place is not None and _text(place):
        out["place"] = _text(place)
    for local, key in [
        ("publisher", "publisher"),
        ("dateIssued", "date_issued"),
        ("edition", "edition"),
        ("issuance", "issuance"),
    ]:
        child = origin_info.find(_q(local))
        text = _text(child)
        if text:
            out[key] = text
        if local == "dateIssued" and child is not None and child.get("encoding"):
            out["date_encoding"] = child.get("encoding")
    return out


def _parse_genres(root) -> list[BibliographyGenre]:
    genres: list[BibliographyGenre] = []
    for genre in root.findall(_q("genre")):
        text = _text(genre)
        if text:
            genres.append(BibliographyGenre(
                value=text,
                authority=genre.get("authority"),
            ))
    return genres


def _parse_notes(root) -> list[BibliographyNote]:
    notes: list[BibliographyNote] = []
    for note in root.findall(_q("note")):
        text = _text(note)
        if text:
            notes.append(BibliographyNote(
                type=note.get("type"),
                text=text,
            ))
    return notes


def _first_note(notes: list[BibliographyNote], note_type: str) -> str | None:
    for note in notes:
        if note.type == note_type:
            return note.text
    return None


def _attr(el, name: str) -> str | None:
    value = el.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _drop_empty(data: dict) -> dict:
    return {k: v for k, v in data.items() if v not in (None, "")}
