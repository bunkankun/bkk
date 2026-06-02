"""In-memory abstract shape produced by readers and consumed by writers.

Readers (e.g. read/tls.py) produce a :class:`Bundle` from source files.
Writers (write/bundle.py) consume a :class:`Bundle` and emit YAML files
in the BKK archival format.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Marker:
    type: str
    offset: int
    content: str = ""
    id: str = ""
    extras: dict = field(default_factory=dict)


@dataclass
class Section:
    """One top-level <div> from the source. Not yet bucketed into front/body."""
    head_text: str
    head_marker_id: str
    text: str
    markers: list[Marker]
    bucket: str | None = None  # explicit "front"/"body"/"back" override


@dataclass
class Annotation:
    """One annotation entry, with seg_id+pos still un-resolved into an offset."""
    seg_id: str
    pos: int | None
    payload: dict
    source_role: str = "tls:ann"
    provenance: str | None = None


@dataclass
class Juan:
    seq: int
    sections: list[Section]
    annotations: list[Annotation] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class Bundle:
    text_id: str
    juans: list[Juan]
    metadata: dict = field(default_factory=dict)
    edition_short: str = "T"
    source: dict = field(default_factory=dict)
    source_info: dict | None = None
    pua_map: dict | None = None  # PUA-map.yaml payload (master only); None to skip
    witnesses: list[str] = field(default_factory=list)  # short ids of compared editions


# ---------- Translations ---------------------------------------------------
#
# Translation bundles are a separate species: Markdown body with a YAML
# header, addressable in their own right (see bunkankun.md §"Translations").
# They share none of Bundle's juan/edition/marker model, so they get their
# own IR shapes.


@dataclass
class TranslationSegment:
    """One <seg> from a TLS-shaped translation file.

    ``corresp`` carries one or more *location-only* marker ids (the
    ``<text-id>_<edition>_`` prefix is stripped on read, since the source
    pin in the bundle's YAML header makes those implicit).

    ``juan_label`` is parsed from the first corresp's location component
    (``001-2a.3`` -> ``001``); used by the writer to split per-juan.
    """
    corresp: list[str]
    text: str
    juan_label: str
    lang: str | None = None
    resp: str | None = None
    modified: str | None = None


@dataclass
class TranslationBundle:
    """One TLS-shaped translation file, parsed into the BKK translation IR."""
    bundle_id: str            # file stem, e.g. "KR1h0004-en" or "KR1h0004-en-588d9aad"
    source_text_id: str       # KR id of the translated source (from sourceDesc/bibl)
    language: str             # BCP-47 tag (en, fr, ...)
    metadata: dict            # title, responsibility[], publication, license, date, ...
    segments: list[TranslationSegment]
    source_info: dict | None = None  # raw teiHeader for round-trip; sidecar


# ---------- Concepts -------------------------------------------------------
#
# Concept records are standalone Markdown notes sourced from TLS concept TEI.
# They are not text bundles and do not participate in the juan/edition model.


@dataclass
class ConceptSection:
    type: str
    paragraphs: list[str]


@dataclass
class ConceptRelation:
    type: str
    refs: list[tuple[str, str]]


@dataclass
class ConceptBibliographyEntry:
    ref_uuid: str | None
    ref_label: str | None
    title: str | None
    scope_unit: str | None
    scope: str | None
    notes: list[str] = field(default_factory=list)


@dataclass
class ConceptBundle:
    uuid: str                  # normalized without leading "uuid-"
    concept: str
    labels: list[str] = field(default_factory=list)
    translations: dict[str, str] = field(default_factory=dict)
    definition: list[str] = field(default_factory=list)
    notes: list[ConceptSection] = field(default_factory=list)
    relations: list[ConceptRelation] = field(default_factory=list)
    bibliography: list[ConceptBibliographyEntry] = field(default_factory=list)
    words: list[str] = field(default_factory=list)


# ---------- Bibliography ---------------------------------------------------


@dataclass
class BibliographyTitle:
    title: str
    subtitle: str | None = None
    type: str | None = None
    lang: str | None = None
    script: str | None = None
    transliteration: str | None = None


@dataclass
class BibliographyContributor:
    type: str | None = None
    roles: list[str] = field(default_factory=list)
    given: str | None = None
    family: str | None = None
    lang: str | None = None
    script: str | None = None
    names: list[dict] = field(default_factory=list)


@dataclass
class BibliographyGenre:
    value: str
    authority: str | None = None


@dataclass
class BibliographyNote:
    type: str | None
    text: str


@dataclass
class BibliographyBundle:
    uuid: str
    citation_label: str | None = None
    ref_usage: str | None = None
    resource_type: str | None = None
    genres: list[BibliographyGenre] = field(default_factory=list)
    titles: list[BibliographyTitle] = field(default_factory=list)
    contributors: list[BibliographyContributor] = field(default_factory=list)
    origin: dict = field(default_factory=dict)
    notes: list[BibliographyNote] = field(default_factory=list)
    source: dict = field(default_factory=dict)


# ---------- Graphs ---------------------------------------------------------


@dataclass
class GraphBundle:
    uuid: str
    graphs: dict = field(default_factory=dict)
    gloss: str | None = None
    xiaoyun: dict = field(default_factory=dict)
    fanqie: dict = field(default_factory=dict)
    ids: dict = field(default_factory=dict)
    locations: dict = field(default_factory=dict)
    notes: dict = field(default_factory=dict)
    pronunciation: dict = field(default_factory=dict)


# ---------- Syntactic functions -------------------------------------------


@dataclass
class SyntacticFunctionRelation:
    type: str
    refs: list[tuple[str, str]]


@dataclass
class SyntacticFunctionBundle:
    uuid: str
    code: str
    descriptions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    relations: list[SyntacticFunctionRelation] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


# ---------- Semantic features ---------------------------------------------


@dataclass
class SemanticFeatureRelation:
    type: str
    target_type: str
    refs: list[dict] = field(default_factory=list)


@dataclass
class SemanticFeatureBundle:
    uuid: str
    code: str
    descriptions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    relations: list[SemanticFeatureRelation] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


# ---------- Words ---------------------------------------------------------


@dataclass
class WordPronunciation:
    lang: str
    value: str
    resp: str | None = None


@dataclass
class WordForm:
    orth: str | None = None
    graph_uuid: str | None = None
    pronunciations: list[WordPronunciation] = field(default_factory=list)


@dataclass
class WordBibliographyRef:
    uuid: str | None
    label: str | None
    title: str | None = None
    scope_unit: str | None = None
    scope: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class WordGrammarLink:
    type: str
    uuid: str | None
    label: str


@dataclass
class WordUsage:
    type: str | None
    value: str


@dataclass
class WordSense:
    uuid: str
    n: str | None = None
    pos: str | None = None
    syntactic_functions: list[WordGrammarLink] = field(default_factory=list)
    semantic_features: list[WordGrammarLink] = field(default_factory=list)
    usages: list[WordUsage] = field(default_factory=list)
    definition: str | None = None
    provenance: dict = field(default_factory=dict)


@dataclass
class WordEntry:
    uuid: str
    concept: str | None = None
    concept_uuid: str | None = None
    n: str | None = None
    form: WordForm | None = None
    definition: str | None = None
    bibliography: list[WordBibliographyRef] = field(default_factory=list)
    senses: list[WordSense] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)


@dataclass
class WordBundle:
    uuid: str
    orth: str | None = None
    n: str | None = None
    forms: list[WordForm] = field(default_factory=list)
    entries: list[WordEntry] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
