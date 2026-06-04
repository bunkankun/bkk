"""bkk-core knowledge layer browse under ``/core``.

Drives the CORE activity in the web frontend: list collections, search a
collection by label, expand a super-entry into its constituent words, and
fetch a single record's detail (frontmatter + raw body markdown).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from bkk.index.catalog import normalize_search_text
from bkk.serve.state import AppState

router = APIRouter(prefix="/core", tags=["core"])


COLLECTION_TYPES: dict[str, str] = {
    "concepts": "concept",
    "graphs": "graph",
    "syntactic-functions": "syntactic-function",
    "semantic-features": "semantic-feature",
    "bibliography": "bibliography",
    "words": "word",
    "super-entries": "super-entry",
}

COLLECTION_LABELS: dict[str, str] = {
    "concepts": "Concepts",
    "graphs": "Graphs",
    "syntactic-functions": "Syntactic functions",
    "semantic-features": "Semantic features",
    "bibliography": "Bibliography",
    "words": "Words",
}

# Collections the CORE activity exposes for browsing (excludes super-entries,
# which are surfaced through the Words two-level view).
BROWSE_COLLECTIONS: tuple[str, ...] = (
    "concepts", "words", "syntactic-functions",
    "semantic-features", "graphs", "bibliography",
)

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)


class CollectionInfo(BaseModel):
    id: str
    label: str
    count: int


class CollectionsResponse(BaseModel):
    collections: list[CollectionInfo]


class CoreMatch(BaseModel):
    uuid: str
    type: str
    display_label: str
    alt_labels: list[str] = Field(default_factory=list)


class SuperEntryMatch(BaseModel):
    super_entry_uuid: str
    orth: str
    word_count: int


class CoreListResponse(BaseModel):
    collection: str
    total: int
    offset: int
    limit: int
    matches: list[CoreMatch] = Field(default_factory=list)
    super_entries: list[SuperEntryMatch] = Field(default_factory=list)


class SuperEntryWord(BaseModel):
    uuid: str
    display_label: str | None
    concept: str | None
    n: str | None


class SuperEntryExpansion(BaseModel):
    uuid: str
    orth: str
    words: list[SuperEntryWord]


class SuperEntryByOrth(BaseModel):
    uuid: str
    orth: str


class FullSense(BaseModel):
    uuid: str
    body_number: int | None
    pos: str | None
    syn_func: str | None
    sem_feat: str | None
    def_: str | None = Field(default=None, alias="def")

    model_config = {"populate_by_name": True}


class FullWord(BaseModel):
    uuid: str
    display_label: str | None
    concept: str | None
    concept_uuid: str | None
    pinyin: str | None
    n: str | None
    senses: list[FullSense]


class SuperEntryFull(BaseModel):
    uuid: str
    orth: str
    words: list[FullWord]


class ConceptWord(BaseModel):
    uuid: str
    display_label: str | None
    super_entry_uuid: str | None
    super_entry_orth: str | None
    n: str | None


class ConceptWordsResponse(BaseModel):
    concept_uuid: str
    words: list[ConceptWord]


class BacklinkItem(BaseModel):
    uuid: str
    type: str
    collection: str
    display_label: str
    relation: str | None


class BacklinkGroup(BaseModel):
    collection: str
    type: str
    total: int
    items: list[BacklinkItem]


class BacklinksResponse(BaseModel):
    uuid: str
    total: int
    groups: list[BacklinkGroup]


class CoreRecordLink(BaseModel):
    target_uuid: str
    target_type: str | None
    target_collection: str | None
    target_label: str | None
    relation: str | None


class CoreRecordResponse(BaseModel):
    uuid: str
    type: str
    collection: str
    display_label: str
    path: str
    frontmatter: dict[str, Any]
    body_markdown: str
    links: list[CoreRecordLink] = Field(default_factory=list)


# ---------- helpers ---------------------------------------------------------


def _open(state: AppState) -> sqlite3.Connection:
    conn = state.open_core()
    if conn is None:
        raise HTTPException(
            status_code=503,
            detail="core knowledge index is not configured; "
                   "set core.root in .bkkrc and run `bkk index core`",
        )
    return conn


def _require_collection(collection: str) -> str:
    if collection not in COLLECTION_TYPES:
        raise HTTPException(
            status_code=404,
            detail=f"unknown collection {collection!r}; "
                   f"valid: {sorted(COLLECTION_TYPES)}",
        )
    return COLLECTION_TYPES[collection]


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}, match.group(2)
    return (fm if isinstance(fm, dict) else {}), match.group(2)


def _collection_of_type(type_name: str) -> str | None:
    for coll, t in COLLECTION_TYPES.items():
        if t == type_name:
            return coll
    return None


# ---------- /collections ----------------------------------------------------


@router.get(
    "/collections",
    response_model=CollectionsResponse,
    summary="List the browseable core knowledge collections",
)
def collections(request: Request) -> CollectionsResponse:
    state: AppState = request.app.state.bkk
    conn = _open(state)
    try:
        counts = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT collection, COUNT(*) FROM notes GROUP BY collection"
            )
        }
    finally:
        conn.close()
    out = [
        CollectionInfo(
            id=coll, label=COLLECTION_LABELS[coll], count=int(counts.get(coll, 0)),
        )
        for coll in BROWSE_COLLECTIONS
    ]
    return CollectionsResponse(collections=out)


# ---------- /{collection} (Words list = super-entries) -----------------------


@router.get(
    "/{collection}",
    response_model=CoreListResponse,
    summary="List or search records in a core knowledge collection",
)
def list_collection(
    request: Request,
    collection: str,
    q: str | None = Query(default=None, description="label substring filter"),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> CoreListResponse:
    if collection not in BROWSE_COLLECTIONS:
        raise HTTPException(
            status_code=404,
            detail=f"collection {collection!r} is not browseable; "
                   f"valid: {sorted(BROWSE_COLLECTIONS)}",
        )
    state: AppState = request.app.state.bkk
    conn = _open(state)
    try:
        if collection == "words":
            return _list_super_entries(conn, q, limit, offset)
        return _list_notes(conn, collection, q, limit, offset)
    finally:
        conn.close()


def _list_notes(
    conn: sqlite3.Connection,
    collection: str,
    q: str | None,
    limit: int,
    offset: int,
) -> CoreListResponse:
    q = q.strip() if q else None
    if q and any(ch.isupper() for ch in q):
        # Uppercase in the query → case-sensitive starts-with match on the
        # main display label only. Lets the user pin a search like "ABLE"
        # without dragging in records that mention it as an alt label.
        glob = f"{q}*"
        total = conn.execute(
            "SELECT COUNT(*) FROM notes WHERE collection = ? AND display_label GLOB ?",
            (collection, glob),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT uuid, type, display_label FROM notes "
            "WHERE collection = ? AND display_label GLOB ? "
            "ORDER BY display_label LIMIT ? OFFSET ?",
            (collection, glob, limit, offset),
        ).fetchall()
    elif q:
        norm = normalize_search_text(q)
        like = f"%{norm}%"
        total = conn.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT n.uuid FROM notes n JOIN labels l ON l.uuid = n.uuid "
            "WHERE n.collection = ? AND l.label_search LIKE ? GROUP BY n.uuid"
            ")",
            (collection, like),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT n.uuid, n.type, n.display_label FROM notes n "
            "JOIN labels l ON l.uuid = n.uuid "
            "WHERE n.collection = ? AND l.label_search LIKE ? "
            "GROUP BY n.uuid "
            "ORDER BY n.display_label COLLATE NOCASE "
            "LIMIT ? OFFSET ?",
            (collection, like, limit, offset),
        ).fetchall()
    else:
        total = conn.execute(
            "SELECT COUNT(*) FROM notes WHERE collection = ?",
            (collection,),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT uuid, type, display_label FROM notes "
            "WHERE collection = ? "
            "ORDER BY display_label COLLATE NOCASE "
            "LIMIT ? OFFSET ?",
            (collection, limit, offset),
        ).fetchall()

    matches: list[CoreMatch] = []
    for uuid_, type_, display in rows:
        alts = _alt_labels(conn, uuid_, display)
        matches.append(CoreMatch(
            uuid=uuid_, type=type_, display_label=display, alt_labels=alts,
        ))
    return CoreListResponse(
        collection=collection, total=int(total),
        offset=offset, limit=limit, matches=matches,
    )


def _alt_labels(
    conn: sqlite3.Connection, uuid_: str, display: str, *, limit: int = 6,
) -> list[str]:
    rows = conn.execute(
        "SELECT label FROM labels WHERE uuid = ? AND label_type != 'display' "
        "ORDER BY rowid LIMIT ?",
        (uuid_, limit + 1),
    ).fetchall()
    seen: set[str] = {display}
    out: list[str] = []
    for (label,) in rows:
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
        if len(out) >= limit:
            break
    return out


def _list_super_entries(
    conn: sqlite3.Connection,
    q: str | None,
    limit: int,
    offset: int,
) -> CoreListResponse:
    norm = normalize_search_text(q) if q else None
    if norm:
        like = f"%{norm}%"
        total = conn.execute(
            "SELECT COUNT(*) FROM super_entries WHERE orth_search LIKE ?",
            (like,),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT uuid, orth, word_count FROM super_entries "
            "WHERE orth_search LIKE ? "
            "ORDER BY orth COLLATE NOCASE LIMIT ? OFFSET ?",
            (like, limit, offset),
        ).fetchall()
    else:
        total = conn.execute("SELECT COUNT(*) FROM super_entries").fetchone()[0]
        rows = conn.execute(
            "SELECT uuid, orth, word_count FROM super_entries "
            "ORDER BY orth COLLATE NOCASE LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    super_entries = [
        SuperEntryMatch(super_entry_uuid=u, orth=o, word_count=int(c))
        for (u, o, c) in rows
    ]
    return CoreListResponse(
        collection="words", total=int(total),
        offset=offset, limit=limit, super_entries=super_entries,
    )


# ---------- /words/super-entry/{uuid} ---------------------------------------


@router.get(
    "/words/super-entry/{uuid}",
    response_model=SuperEntryExpansion,
    summary="Expand a super-entry into its constituent word records",
)
def expand_super_entry(request: Request, uuid: str) -> SuperEntryExpansion:
    state: AppState = request.app.state.bkk
    conn = _open(state)
    try:
        se = conn.execute(
            "SELECT uuid, orth FROM super_entries WHERE uuid = ?",
            (uuid,),
        ).fetchone()
        if se is None:
            raise HTTPException(status_code=404, detail=f"super-entry {uuid!r} not found")
        rows = conn.execute(
            "SELECT sew.word_uuid, sew.concept, sew.n, n.display_label "
            "FROM super_entry_words sew "
            "LEFT JOIN notes n ON n.uuid = sew.word_uuid "
            "WHERE sew.super_entry_uuid = ? "
            "ORDER BY COALESCE(sew.concept, '')",
            (uuid,),
        ).fetchall()
    finally:
        conn.close()
    words = [
        SuperEntryWord(uuid=u, display_label=d, concept=c, n=n)
        for (u, c, n, d) in rows
    ]
    return SuperEntryExpansion(uuid=se[0], orth=se[1], words=words)


# ---------- /super-entries/by-orth/{orth} -----------------------------------


@router.get(
    "/super-entries/by-orth/{orth}",
    response_model=SuperEntryByOrth,
    summary="Look up a super-entry by its orth (used to resolve [[X]] wikilinks)",
)
def super_entry_by_orth(request: Request, orth: str) -> SuperEntryByOrth:
    state: AppState = request.app.state.bkk
    conn = _open(state)
    try:
        row = conn.execute(
            "SELECT uuid, orth FROM super_entries WHERE orth = ? LIMIT 1",
            (orth,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no super-entry with orth {orth!r}")
    return SuperEntryByOrth(uuid=row[0], orth=row[1])


# ---------- /super-entries/by-orth/{orth}/full ------------------------------


@router.get(
    "/super-entries/by-orth/{orth}/full",
    response_model=SuperEntryFull,
    response_model_by_alias=True,
    summary="Look up a super-entry by orth and return its words + senses in one shot",
)
def super_entry_by_orth_full(request: Request, orth: str) -> SuperEntryFull:
    state: AppState = request.app.state.bkk
    conn = _open(state)
    try:
        se = conn.execute(
            "SELECT uuid, orth FROM super_entries WHERE orth = ? LIMIT 1",
            (orth,),
        ).fetchone()
        if se is None:
            raise HTTPException(
                status_code=404, detail=f"no super-entry with orth {orth!r}",
            )
        se_uuid, se_orth = se[0], se[1]
        word_rows = conn.execute(
            "SELECT sew.word_uuid, n.display_label, sew.concept, "
            "       sew.concept_uuid, sew.pinyin, sew.n "
            "FROM super_entry_words sew "
            "LEFT JOIN notes n ON n.uuid = sew.word_uuid "
            "WHERE sew.super_entry_uuid = ? "
            "ORDER BY COALESCE(sew.concept, '')",
            (se_uuid,),
        ).fetchall()
        word_uuids = [r[0] for r in word_rows]
        senses_by_word: dict[str, list[FullSense]] = {u: [] for u in word_uuids}
        if word_uuids:
            placeholders = ",".join("?" * len(word_uuids))
            sense_rows = conn.execute(
                f"SELECT uuid, word_uuid, body_number, pos, syn_func, sem_feat, def "
                f"FROM senses WHERE word_uuid IN ({placeholders}) "
                f"ORDER BY word_uuid, COALESCE(body_number, 0)",
                word_uuids,
            ).fetchall()
            for (s_uuid, w_uuid, bn, pos, sf, sfeat, dfn) in sense_rows:
                senses_by_word.setdefault(w_uuid, []).append(FullSense(
                    uuid=s_uuid, body_number=bn, pos=pos,
                    syn_func=sf, sem_feat=sfeat, **{"def": dfn},
                ))
    finally:
        conn.close()
    words = [
        FullWord(
            uuid=u, display_label=label, concept=concept,
            concept_uuid=concept_uuid, pinyin=pinyin, n=n,
            senses=senses_by_word.get(u, []),
        )
        for (u, label, concept, concept_uuid, pinyin, n) in word_rows
    ]
    return SuperEntryFull(uuid=se_uuid, orth=se_orth, words=words)


# ---------- /concepts/{uuid}/words ------------------------------------------


@router.get(
    "/concepts/{uuid}/words",
    response_model=ConceptWordsResponse,
    summary="List the words attached to a concept, ordered by n descending",
)
def concept_words(request: Request, uuid: str) -> ConceptWordsResponse:
    state: AppState = request.app.state.bkk
    conn = _open(state)
    try:
        # A word now links to its concept twice (frontmatter `concept`
        # relation + body markdown link), so dedupe by source word.
        rows = conn.execute(
            "SELECT DISTINCT l.source_uuid, n.display_label, "
            "                sew.super_entry_uuid, se.orth, sew.n "
            "FROM links l "
            "JOIN notes n ON n.uuid = l.source_uuid "
            "LEFT JOIN super_entry_words sew ON sew.word_uuid = l.source_uuid "
            "LEFT JOIN super_entries se ON se.uuid = sew.super_entry_uuid "
            "WHERE l.target_uuid = ? AND l.target_type = 'concept' "
            "  AND l.source_type = 'word' "
            "ORDER BY CAST(COALESCE(sew.n, '0') AS INTEGER) DESC, "
            "         n.display_label COLLATE NOCASE",
            (uuid,),
        ).fetchall()
    finally:
        conn.close()
    words = [
        ConceptWord(
            uuid=u, display_label=label,
            super_entry_uuid=se_uuid, super_entry_orth=se_orth, n=n,
        )
        for (u, label, se_uuid, se_orth, n) in rows
    ]
    return ConceptWordsResponse(concept_uuid=uuid, words=words)


# ---------- /{collection}/{uuid}/backlinks ----------------------------------


@router.get(
    "/{collection}/{uuid}/backlinks",
    response_model=BacklinksResponse,
    summary="List records that reference this record, grouped by source collection",
)
def backlinks(
    request: Request,
    collection: str,
    uuid: str,
    per_group_limit: int = Query(default=200, ge=1, le=2000),
) -> BacklinksResponse:
    _require_collection(collection)
    state: AppState = request.app.state.bkk
    conn = _open(state)
    try:
        # A source record may link to the same target with multiple relations
        # (e.g. a frontmatter `super_entry` link plus a body markdown link to
        # the same super-entry). Surface each source once, preferring the
        # non-body relation so the UI shows the semantic edge label.
        rows = conn.execute(
            "SELECT l.source_uuid, l.source_type, "
            "       MIN(CASE WHEN l.relation IN ('body', 'body-wikilink') "
            "                THEN NULL ELSE l.relation END) AS relation, "
            "       n.collection, n.display_label "
            "FROM links l "
            "JOIN notes n ON n.uuid = l.source_uuid "
            "WHERE l.target_uuid = ? "
            "GROUP BY l.source_uuid, l.source_type, n.collection, n.display_label "
            "ORDER BY n.collection, n.display_label COLLATE NOCASE",
            (uuid,),
        ).fetchall()
    finally:
        conn.close()

    by_collection: dict[str, list[BacklinkItem]] = {}
    by_type: dict[str, str] = {}
    for src_uuid, src_type, relation, src_collection, src_label in rows:
        bucket = by_collection.setdefault(src_collection, [])
        bucket.append(BacklinkItem(
            uuid=src_uuid,
            type=src_type,
            collection=src_collection,
            display_label=src_label,
            relation=relation,
        ))
        by_type.setdefault(src_collection, src_type)

    groups = [
        BacklinkGroup(
            collection=coll,
            type=by_type[coll],
            total=len(items),
            items=items[:per_group_limit],
        )
        for coll, items in sorted(by_collection.items())
    ]
    total = sum(g.total for g in groups)
    return BacklinksResponse(uuid=uuid, total=total, groups=groups)


# ---------- /{collection}/{uuid} --------------------------------------------


@router.get(
    "/{collection}/{uuid}",
    response_model=CoreRecordResponse,
    summary="Fetch a single core knowledge record with raw body markdown",
)
def get_record(request: Request, collection: str, uuid: str) -> CoreRecordResponse:
    type_name = _require_collection(collection)
    state: AppState = request.app.state.bkk
    conn = _open(state)
    try:
        row = conn.execute(
            "SELECT uuid, type, collection, path, display_label "
            "FROM notes WHERE uuid = ? AND collection = ?",
            (uuid, collection),
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"{collection}/{uuid} not found in core index",
            )
        link_rows = conn.execute(
            "SELECT l.target_uuid, l.target_type, l.relation, n2.display_label "
            "FROM links l "
            "LEFT JOIN notes n2 ON n2.uuid = l.target_uuid "
            "WHERE l.source_uuid = ? "
            "ORDER BY l.rowid",
            (uuid,),
        ).fetchall()
    finally:
        conn.close()

    if state.core_root is None:
        raise HTTPException(status_code=503, detail="core_root not configured")
    md_path = state.core_root / row[3]
    try:
        raw = md_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(
            status_code=410,
            detail=f"index references missing file {row[3]!r}; rerun `bkk index core`",
        )
    fm, body = _split_frontmatter(raw)

    if type_name != row[1]:
        # index/collection mismatch — bail loudly instead of silently lying.
        raise HTTPException(
            status_code=500,
            detail=f"type/collection mismatch in index for {uuid}",
        )

    links = [
        CoreRecordLink(
            target_uuid=t_uuid,
            target_type=t_type,
            target_collection=_collection_of_type(t_type) if t_type else None,
            target_label=t_label,
            relation=relation,
        )
        for (t_uuid, t_type, relation, t_label) in link_rows
    ]

    return CoreRecordResponse(
        uuid=row[0],
        type=row[1],
        collection=row[2],
        display_label=row[4],
        path=row[3],
        frontmatter=fm,
        body_markdown=body,
        links=links,
    )
