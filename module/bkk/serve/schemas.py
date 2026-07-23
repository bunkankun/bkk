"""Pydantic response models for the serve API.

Manifest and juan bodies are passed through as ``dict[str, Any]`` rather than
strictly modelled — the on-disk YAML carries project-defined extras that we
prefer to surface unchanged. Models with concrete fields are reserved for
shapes the server constructs itself (bundle summaries, search hits).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from .recipe_refs import normalize_recipe_refs


class EditionInfo(BaseModel):
    short: str
    label: str | None = None


class BundleSummary(BaseModel):
    textid: str = Field(..., description="bundle directory name")
    canonical_identifier: str | None = None
    title: str | None = None
    edition_short: str | None = Field(
        None, description="value of metadata.edition.short on the master manifest"
    )
    editions: list[EditionInfo] = Field(
        default_factory=list,
        description="documentary editions declared on the master manifest",
    )


class BundleListResponse(BaseModel):
    bundles: list[BundleSummary]
    total: int
    offset: int
    limit: int


class VariantOverlayOut(BaseModel):
    master_offset: int
    length: int
    content: str
    witness: str
    witness_form: str


class HitOut(BaseModel):
    textid: str
    title: str | None = None
    juan_seq: int
    bucket: str
    master_offset: int
    master_length: int
    matched_via: str
    matched_text: str
    left: str
    match: str
    right: str
    witness_left: str = Field(
        "",
        description="KWIC left-context from the witness text (empty for master hits); "
                    "useful when a long variant reading replaces a short master span",
    )
    witness_right: str = Field(
        "",
        description="KWIC right-context from the witness text (empty for master hits)",
    )
    witness_left_variant_offset: int = Field(
        0,
        description="index within witness_left at which the variant content "
                    "begins; chars before are master/identity (shared with the "
                    "master line), chars after are variant interior",
    )
    witness_right_variant_end: int = Field(
        0,
        description="index within witness_right at which the variant content "
                    "ends; chars before are variant interior, chars after are "
                    "master/identity",
    )
    overlays: list[VariantOverlayOut] = []
    toc_label: str | None = None
    voice: str = Field(
        "none",
        description="innermost voice name fully containing the hit "
                    "('root', 'commentary', …), or 'mixed' if it straddles "
                    "voice boundaries, or 'none' if no voice range covers it",
    )
    voice_stack: list[str] = Field(
        default_factory=list,
        description="outermost → innermost names of every voice range fully "
                    "containing the hit; empty for 'mixed'/'none'",
    )
    recipe: dict[str, Any] = Field(
        default_factory=dict,
        description="one-pin recipe pinning this hit (re-submittable to /recipes:fulfil)",
    )


class SearchFacetValue(BaseModel):
    value: str
    label: str | None = None
    count: int
    selected: bool = False
    excluded: bool = False


class SearchDateFacets(BaseModel):
    min: int | None = None
    max: int | None = None
    current_textid: str | None = None
    current_text_date: int | None = None
    before_count: int | None = None
    after_count: int | None = None


class SearchFacets(BaseModel):
    textid: list[SearchFacetValue] = Field(default_factory=list)
    category: list[SearchFacetValue] = Field(default_factory=list)
    witness: list[SearchFacetValue] = Field(default_factory=list)
    voice: list[SearchFacetValue] = Field(default_factory=list)
    left_char: list[SearchFacetValue] = Field(default_factory=list)
    right_char: list[SearchFacetValue] = Field(default_factory=list)
    left_bigram: list[SearchFacetValue] = Field(default_factory=list)
    right_bigram: list[SearchFacetValue] = Field(default_factory=list)
    around_binom: list[SearchFacetValue] = Field(default_factory=list)
    date: SearchDateFacets = Field(default_factory=SearchDateFacets)


class TrigramExtension(BaseModel):
    gram: str
    count: int


class SearchOverview(BaseModel):
    """Bird's-eye view served when a query exceeds ``max_search_hits``.

    ``hits`` is empty in overview mode; the UI uses this block (plus the
    SQL-aggregated facets) to help the user narrow the query before any
    KWIC lines are materialised.
    """

    approximate: bool = Field(
        False,
        description="True for queries of length ≥ 3, where the total is an "
                    "upper bound from trigram candidates rather than a "
                    "string-verified count.",
    )
    threshold: int = Field(..., description="value of max_search_hits in effect")
    trigram_left: list[TrigramExtension] = Field(default_factory=list)
    trigram_right: list[TrigramExtension] = Field(default_factory=list)
    kwic_filters_ignored: bool = Field(
        False,
        description="True when the request set a KWIC-based filter "
                    "(left_char, right_bigram, around_binom, …) that "
                    "cannot be honoured without materialising hits.",
    )


class SearchResponse(BaseModel):
    query: str
    query_mode: str = "literal"
    total: int
    offset: int
    limit: int
    sort: str
    facets: SearchFacets = Field(default_factory=SearchFacets)
    hits: list[HitOut]
    overview: SearchOverview | None = None


class BundleSearchResponse(BaseModel):
    """Substring search within one bundle's ``.bkkx``, in text order.

    A navigation aid (no facets, no overview). When ``capped`` is true,
    the query exceeded the configured ``max_search_hits`` cap and no
    hits were materialised — the caller should refine the query.
    """

    query: str
    total: int = Field(
        ...,
        description="number of hits after sort/limit; when ``capped``, the "
                    "candidate position count from the trigram scan",
    )
    capped: bool = Field(
        False,
        description="True when ``total`` exceeded ``max_search_hits`` and "
                    "``hits`` was returned empty rather than materialised",
    )
    hits: list[HitOut] = Field(default_factory=list)


class SearchTextidsResponse(BaseModel):
    query: str
    hit_count: int
    text_count: int
    textids: list[str]
    entries: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-text search-list rows with textid, hit_count, and optional title.",
    )


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    extra: dict[str, Any] | None = None


class BundleAsset(BaseModel):
    name: str = Field(..., description="filename relative to the bundle directory")
    role: str | None = Field(
        None, description="declared role from manifest.assets.references[].role"
    )
    hash: str | None = None
    size: int | None = Field(None, description="file size in bytes if readable")


class BundleAssetsResponse(BaseModel):
    textid: str
    assets: list[BundleAsset]


class OverlayFamily(BaseModel):
    id: str
    label: str
    count: int


class OverlaysResponse(BaseModel):
    overlays: list[OverlayFamily]


class TranslationResponsibility(BaseModel):
    role: str | None = None
    name: str | None = None


class TranslationSummary(BaseModel):
    id: str
    source_textid: str
    canonical_identifier: str | None = None
    source_canonical_identifier: str | None = None
    language: str | None = None
    title: str | None = None
    original_title: str | None = None
    responsibility: list[TranslationResponsibility] = Field(default_factory=list)
    date: str | None = None
    license: str | None = None
    juan_count: int = 0
    segment_count: int = 0
    source_juans: list[int] = Field(default_factory=list)


class TranslationListResponse(BaseModel):
    translations: list[TranslationSummary]
    total: int
    offset: int
    limit: int


class TranslationAlignedRow(BaseModel):
    corresp: str
    source_marker_id: str
    source_offset: int
    source_end: int
    source_text: str
    translation_text: str = ""
    translation_refs: list[str] = Field(default_factory=list)
    continued: bool = False
    resp: str | None = None


class TranslationAlignmentResponse(BaseModel):
    textid: str
    juan_seq: int
    translation: TranslationSummary | None = None
    status: str
    rows: list[TranslationAlignedRow] = Field(default_factory=list)


class SegmentTranslationEntry(BaseModel):
    bundle_id: str
    title: str | None = None
    language: str | None = None
    translator: str | None = None
    text: str


class SegmentTranslationsResponse(BaseModel):
    corresp: str
    source_text: str
    entries: list[SegmentTranslationEntry] = Field(default_factory=list)


class TranslationSegmentHit(BaseModel):
    bundle_id: str
    source_textid: str
    juan_seq: int
    corresp: str | None
    text: str
    source_text: str | None = None
    language: str | None = None
    title: str | None = None
    responsibility: list[TranslationResponsibility] = Field(default_factory=list)
    date: str | None = None
    is_ai: bool = False


class TranslationSearchFacets(BaseModel):
    language: list[SearchFacetValue] = Field(default_factory=list)
    category: list[SearchFacetValue] = Field(default_factory=list)
    date: SearchDateFacets = Field(default_factory=SearchDateFacets)
    type: list[SearchFacetValue] = Field(default_factory=list)


class TranslationSearchResponse(BaseModel):
    hits: list[TranslationSegmentHit]
    total: int
    offset: int
    limit: int
    q: str
    facets: TranslationSearchFacets = Field(default_factory=TranslationSearchFacets)


class CollisionCandidate(BaseModel):
    """One bundle that matched an ambiguous identifier lookup."""

    textid: str
    canonical_identifier: str | None = None
    edition_short: str | None = None
    base_edition: str | None = None
    title: str | None = None
    link: str = Field(..., description="direct /bundles/{textid} URL")


class MultipleChoicesResponse(BaseModel):
    """HTTP 300 body when an identifier resolves to more than one bundle."""

    error: str = "multiple_choices"
    identifier: str
    candidates: list[CollisionCandidate]


class RecipePin(BaseModel):
    """A single pin within a recipe (per bunkankun.md "Recipe format")."""

    role: str
    ref: str | None = Field(
        None,
        description=(
            "KR shorthand expanded into textid and selection when validating "
            "a RecipeRequest"
        ),
    )
    canonical_identifier: str | None = None
    textid: str | None = None
    hash: str | None = None
    selection: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class CatalogMatchOut(BaseModel):
    textid: str
    canonical_identifier: str | None = None
    title: str | None = None
    edition_short: str | None = None
    base_edition: str | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="echo of curated metadata fields used for filtering",
    )


class JuanSliceOut(BaseModel):
    """A sliced view onto one juan bucket."""

    textid: str | None = None
    juan_seq: int
    bucket: str
    span: list[int] = Field(
        ...,
        description="[start, end) within the bucket text (in chars)",
    )
    text: str
    markers: list[dict[str, Any]] = Field(
        default_factory=list,
        description="markers within the slice; offsets re-based to slice start",
    )


class RecipeRequest(BaseModel):
    """Body of POST /recipes:fulfil. ``pins`` is a list of RecipePin shapes."""

    pins: list[RecipePin]

    @model_validator(mode="before")
    @classmethod
    def _normalize_short_refs(cls, data: Any) -> Any:
        return normalize_recipe_refs(data)


class FulfilResult(BaseModel):
    pin_index: int
    role: str
    textid: str | None = None
    canonical_identifier: str | None = None
    selection: dict[str, Any] | None = None
    content: JuanSliceOut | list[JuanSliceOut] | None = None
    verified: bool
    manifest_hash: str | None = None
    error: dict[str, Any] | None = None


class FulfilResponse(BaseModel):
    """Response shape for POST /recipes:fulfil."""

    resolved_recipe: dict[str, Any] = Field(
        ...,
        description="echo of the request with every pin's textid + canonical_identifier + hash filled in",
    )
    results: list[FulfilResult]
    errors: list[dict[str, Any]] = Field(default_factory=list)


class CatalogResponse(BaseModel):
    """Catalog response: a recipe with one ``match`` pin per bundle."""

    total: int
    offset: int
    limit: int
    next_offset: int | None = None
    filters_applied: dict[str, list[str]] = Field(default_factory=dict)
    matches: list[CatalogMatchOut]
    recipe: dict[str, Any] = Field(
        ...,
        description="recipe-as-response: pins with role 'match' for every result on this page",
    )


class AnnotationForm(BaseModel):
    orig: str | None = None
    orth: str | None = None
    pron: str | None = None


class AnnotationSense(BaseModel):
    id: str | None = None
    pos: str | None = None
    syn_func: str | None = None
    sem_feat: str | None = None
    def_: str | None = Field(default=None, alias="def")
    usage: dict[str, Any] | None = None
    # Pre-resolved from the core index `senses` table; absent when no core
    # index is available or the sense id isn't known.
    syntactic_function_label: str | None = None
    semantic_feature_label: str | None = None

    model_config = {"populate_by_name": True}


class AnnotationTranslation(BaseModel):
    text: str | None = None
    title: str | None = None
    src: str | None = None


class AnnotationOut(BaseModel):
    """One annotation pinned to a bucket-relative text offset.

    Records are sourced from the bkk-annotations archive (one JSONL file
    per (text_id, juan)). Absent fields are omitted rather than emitted as
    null so the JSON stays small.
    """

    id: str | None = None
    offset: int = Field(..., description="char offset within the juan's bucket text")
    bucket: str | None = Field(None, description="front | body | back")
    length: int | None = Field(None, description="annotated span length")
    marker_id: str | None = Field(
        None, description="id of the marker the annotation is anchored to",
    )
    concept: str | None = None
    concept_id: str | None = None
    form: AnnotationForm | None = None
    sense: AnnotationSense | None = None
    translation: AnnotationTranslation | None = None
    metadata: dict[str, Any] | None = None
    did: str | None = Field(
        None, description="author DID (lets the UI decide who may delete)",
    )
    uri: str | None = Field(
        None, description="at-URI for bsky-native records; absent for legacy/synth",
    )
    curation_state: str | None = Field(
        None,
        description="resolved curation state; absent when ``proposed`` (the default)",
    )

    model_config = {"populate_by_name": True}
