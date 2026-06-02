"""Writer for bibliography Markdown notes."""

from __future__ import annotations

from pathlib import Path

from ..ir import BibliographyBundle, BibliographyContributor
from .concept import knowledge_note_path
from .yaml_writer import dump


def bibliography_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/bibliography/<first-hex>/<uuid>.md``."""
    return knowledge_note_path(out_root, "bibliography", uuid_value)


def write_bibliography(entry: BibliographyBundle, out_root: Path) -> Path:
    """Write one bibliography note and return the Markdown path."""
    out_path = bibliography_note_path(out_root, entry.uuid)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_bibliography(entry), encoding="utf-8")
    return out_path


def render_bibliography(entry: BibliographyBundle) -> str:
    lines = ["---"]
    lines.extend(dump(_frontmatter(entry)).rstrip().splitlines())
    lines.append("---")
    lines.append("")
    lines.append(f"# {entry.citation_label or _display_title(entry) or entry.uuid}")

    if entry.titles:
        lines.append("")
        lines.append("## Title")
        for title in entry.titles:
            text = f"**{title.title}**"
            if title.subtitle:
                text += f": {title.subtitle}"
            lines.append(text)

    if entry.contributors:
        lines.append("")
        lines.append("## Contributors")
        for contributor in entry.contributors:
            lines.append(f"- {_render_contributor(contributor)}")

    publication = _render_publication(entry)
    if publication:
        lines.append("")
        lines.append("## Publication")
        lines.append(publication)

    display_notes = [n.text for n in entry.notes if n.text]
    if display_notes:
        lines.append("")
        lines.append("## Notes")
        lines.extend(display_notes)

    return "\n".join(lines).rstrip() + "\n"


def _frontmatter(entry: BibliographyBundle) -> dict:
    data: dict = {
        "uuid": entry.uuid,
        "type": "bibliography",
    }
    if entry.citation_label:
        data["citation_label"] = entry.citation_label
    if entry.ref_usage:
        data["ref_usage"] = entry.ref_usage
    if entry.resource_type:
        data["resource_type"] = entry.resource_type
    if entry.genres:
        data["genres"] = [
            _drop_none({"value": g.value, "authority": g.authority})
            for g in entry.genres
        ]
    if entry.titles:
        data["titles"] = [
            _drop_none({
                "title": t.title,
                "subtitle": t.subtitle,
                "type": t.type,
                "lang": t.lang,
                "script": t.script,
                "transliteration": t.transliteration,
            })
            for t in entry.titles
        ]
    if entry.contributors:
        data["contributors"] = [
            _drop_none({
                "type": c.type,
                "roles": c.roles or None,
                "given": c.given,
                "family": c.family,
                "lang": c.lang,
                "script": c.script,
                "names": c.names or None,
            })
            for c in entry.contributors
        ]
    if entry.origin:
        data["origin"] = entry.origin
    if entry.notes:
        data["notes"] = [
            _drop_none({"type": n.type, "text": n.text})
            for n in entry.notes
        ]
    if entry.source:
        data["source"] = _drop_none(entry.source)
    return data


def _drop_none(data: dict) -> dict:
    return {k: v for k, v in data.items() if v is not None}


def _display_title(entry: BibliographyBundle) -> str | None:
    return entry.titles[0].title if entry.titles else None


def _render_contributor(contributor: BibliographyContributor) -> str:
    rendered_names = []
    for name_variant in contributor.names:
        rendered = _render_name_variant(name_variant)
        if rendered and rendered not in rendered_names:
            rendered_names.append(rendered)
    name = " / ".join(rendered_names)
    if not name:
        name = " ".join(
            part for part in [contributor.given, contributor.family] if part
        ) or "Unknown"
    if contributor.roles:
        return f"{name}, {', '.join(contributor.roles)}"
    return name


def _render_name_variant(name_variant: dict) -> str:
    given = name_variant.get("given")
    family = name_variant.get("family")
    if name_variant.get("script") == "Hant":
        return "".join(part for part in [family, given] if part)
    return " ".join(part for part in [given, family] if part)


def _render_publication(entry: BibliographyBundle) -> str:
    origin = entry.origin
    parts: list[str] = []
    place = origin.get("place")
    publisher = origin.get("publisher")
    date = origin.get("date_issued")
    edition = origin.get("edition")

    place_publisher = ": ".join(p for p in [place, publisher] if p)
    if place_publisher:
        parts.append(place_publisher)
    if date:
        parts.append(str(date))
    sentence = ", ".join(parts)
    if edition:
        if sentence:
            sentence += f". {edition}."
        else:
            sentence = f"{edition}."
    elif sentence:
        sentence += "."
    return sentence
