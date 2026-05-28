"""Pydantic response models for the serve API.

Manifest and juan bodies are passed through as ``dict[str, Any]`` rather than
strictly modelled — the on-disk YAML carries project-defined extras that we
prefer to surface unchanged. Models with concrete fields are reserved for
shapes the server constructs itself (bundle summaries, search hits).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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


class SearchResponse(BaseModel):
    query: str
    total: int
    offset: int
    limit: int
    sort: str
    facets: SearchFacets = Field(default_factory=SearchFacets)
    hits: list[HitOut]


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

    model_config = {"populate_by_name": True}


class AnnotationTranslation(BaseModel):
    text: str | None = None
    title: str | None = None
    src: str | None = None


class AnnotationOut(BaseModel):
    """One annotation pinned to a text offset.

    Fields mirror the on-disk ``*.ann.yaml`` shape (TLS-derived). Absent
    fields are omitted rather than emitted as null so the JSON stays small.
    """

    id: str | None = None
    offset: int = Field(..., description="char offset within the master body text")
    length: int | None = Field(None, description="annotated span length, if recorded")
    concept: str | None = None
    concept_id: str | None = None
    seg_id: str | None = None
    pos: int | None = None
    form: AnnotationForm | None = None
    sense: AnnotationSense | None = None
    translation: AnnotationTranslation | None = None
    metadata: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}
