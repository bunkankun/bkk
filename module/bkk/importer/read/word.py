"""Reader for TLS word super-entry records."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from ..ir import (
    WordBibliographyRef,
    WordBundle,
    WordEntry,
    WordForm,
    WordGrammarLink,
    WordPronunciation,
    WordSense,
    WordUsage,
)
from ._provenance import lift_source
from .concept import normalize_uuid


TEI_NS = "http://www.tei-c.org/ns/1.0"
TLS_NS = "http://hxwd.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _q(local: str, ns: str = TEI_NS) -> str:
    return f"{{{ns}}}{local}"


def read_word(xml_path: Path) -> WordBundle:
    """Parse one TEI ``superEntry`` word XML file."""
    if not xml_path.exists():
        raise FileNotFoundError(xml_path)

    parser = etree.XMLParser(recover=True, remove_blank_text=False)
    tree = etree.parse(str(xml_path), parser)
    root = tree.getroot()
    if root.tag != _q("superEntry"):
        found = root.find(f".//{_q('superEntry')}")
        if found is None:
            raise ValueError(f"{xml_path} does not contain a superEntry")
        root = found

    uuid = normalize_uuid(root.get(f"{{{XML_NS}}}id") or "")
    if not uuid:
        raise ValueError(f"{xml_path} superEntry is missing xml:id")

    entries = [
        _parse_entry(entry)
        for entry in root.findall(_q("entry"))
    ]
    forms = _dedupe_forms([
        *[_parse_form(form) for form in root.findall(_q("form"))],
        *[entry.form for entry in entries if entry.form is not None],
    ])

    return WordBundle(
        uuid=uuid,
        orth=_child_text(root, "form", "orth"),
        n=_attr(root, "n"),
        forms=forms,
        entries=entries,
        metadata={"source_file": xml_path.name},
    )


def _parse_entry(entry) -> WordEntry:
    uuid = normalize_uuid(entry.get(f"{{{XML_NS}}}id") or "")
    if not uuid:
        raise ValueError("word entry is missing xml:id")

    form_el = entry.find(_q("form"))
    form = _parse_form(form_el) if form_el is not None else None
    def_el = entry.find(_q("def"))
    return WordEntry(
        uuid=uuid,
        concept=_attr(entry, f"{{{TLS_NS}}}concept"),
        concept_uuid=normalize_uuid(_attr(entry, f"{{{TLS_NS}}}concept-id") or ""),
        n=_attr(entry, "n"),
        form=form,
        definition=_direct_child_text(entry, "def"),
        bibliography=_parse_bibliography(form_el),
        senses=[
            _parse_sense(sense)
            for sense in entry.findall(_q("sense"))
        ],
        source=lift_source(entry, def_el),
    )


def _parse_form(form) -> WordForm:
    graph_uuid = normalize_uuid(_attr(form, "corresp") or "")
    return WordForm(
        orth=_child_text(form, "orth"),
        graph_uuid=graph_uuid or None,
        pronunciations=[
            WordPronunciation(
                lang=_attr(pron, f"{{{XML_NS}}}lang") or "",
                value=_text(pron) or "",
                resp=_attr(pron, "resp"),
            )
            for pron in form.findall(_q("pron"))
            if _text(pron)
        ],
    )


def _parse_bibliography(form) -> list[WordBibliographyRef]:
    if form is None:
        return []
    refs: list[WordBibliographyRef] = []
    for bibl in form.findall(f"{_q('listBibl')}/{_q('bibl')}"):
        ref = bibl.find(_q("ref"))
        scope = bibl.find(_q("biblScope"))
        refs.append(WordBibliographyRef(
            uuid=normalize_uuid(_attr(ref, "target") or "") or None,
            label=_text(ref),
            title=_child_text(bibl, "title"),
            scope_unit=_attr(scope, "unit") if scope is not None else None,
            scope=_text(scope),
            notes=[
                text for text in (
                    _text(p) for p in bibl.findall(f"{_q('note')}/{_q('p')}")
                )
                if text
            ],
        ))
    return refs


def _parse_sense(sense) -> WordSense:
    uuid = normalize_uuid(sense.get(f"{{{XML_NS}}}id") or "")
    if not uuid:
        raise ValueError("word sense is missing xml:id")

    gram = sense.find(_q("gramGrp"))
    definition = sense.find(_q("def"))

    return WordSense(
        uuid=uuid,
        n=_attr(sense, "n"),
        pos=_child_text(gram, "pos") if gram is not None else None,
        syntactic_functions=_grammar_links(
            gram, "syn-func", "syntactic-function",
        ),
        semantic_features=_grammar_links(
            gram, "sem-feat", "semantic-feature",
        ),
        usages=[
            WordUsage(type=_attr(usg, "type"), value=_text(usg) or "")
            for usg in gram.findall(_q("usg")) if _text(usg)
        ] if gram is not None else [],
        definition=_text(definition),
        source=lift_source(sense, definition),
    )


def _grammar_links(gram, local: str, link_type: str) -> list[WordGrammarLink]:
    if gram is None:
        return []
    links: list[WordGrammarLink] = []
    for el in gram.findall(_q(local, TLS_NS)):
        label = _text(el)
        if not label:
            continue
        links.append(WordGrammarLink(
            type=link_type,
            uuid=normalize_uuid(_attr(el, "corresp") or "") or None,
            label=label,
        ))
    return links


def _dedupe_forms(forms: list[WordForm]) -> list[WordForm]:
    seen: set[tuple] = set()
    deduped: list[WordForm] = []
    for form in forms:
        key = (
            form.orth,
            form.graph_uuid,
            tuple((p.lang, p.value, p.resp) for p in form.pronunciations),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(form)
    return deduped


def _direct_child_text(parent, local: str) -> str | None:
    child = parent.find(_q(local))
    return _text(child)


def _child_text(parent, *path: str) -> str | None:
    if parent is None:
        return None
    expr = "/".join(_q(local) for local in path)
    return _text(parent.find(expr))


def _attr(el, name: str) -> str | None:
    if el is None:
        return None
    value = el.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _text(el) -> str | None:
    if el is None:
        return None
    text = " ".join("".join(el.itertext()).split())
    return text or None
