"""Translation overlay endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path as PathParam, Query, Request

from bkk.serve import errors, selection
from bkk.serve.schemas import (
    OverlayFamily,
    OverlaysResponse,
    TranslationAlignmentResponse,
    TranslationListResponse,
)
from bkk.serve.state import AppState
from bkk.serve.translations import (
    align_translation,
    list_translation_bundles_from_catalog,
    list_translation_bundles,
    load_translation_bundle_from_catalog,
    load_translation_bundle,
)

router = APIRouter(tags=["translations"])


@router.get("/overlays", response_model=OverlaysResponse, summary="Available overlay families")
def overlays(request: Request) -> OverlaysResponse:
    state: AppState = request.app.state.bkk
    count = 0
    conn = state.open_catalog()
    if conn is not None:
        try:
            count = int(conn.execute("SELECT COUNT(*) FROM catalog_translation").fetchone()[0])
        except Exception:
            count = len(list_translation_bundles(state.corpus_root))
        finally:
            conn.close()
    else:
        count = len(list_translation_bundles(state.corpus_root))
    return OverlaysResponse(
        overlays=[OverlayFamily(id="translations", label="Translations", count=count)]
    )


@router.get(
    "/translations",
    response_model=TranslationListResponse,
    summary="List/search translation bundles",
)
def translations(
    request: Request,
    q: str | None = Query(None, description="search metadata and translated segment text"),
    source_textid: str | None = Query(None, description="restrict to one source text id"),
    lang: str | None = Query(None, description="restrict to one target language"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> TranslationListResponse:
    state: AppState = request.app.state.bkk
    conn = state.open_catalog()
    if conn is not None:
        search_conn = state.open_translation_search()
        try:
            page, total = list_translation_bundles_from_catalog(
                conn,
                search_conn=search_conn,
                q=q,
                source_textid=source_textid,
                lang=lang,
                limit=limit,
                offset=offset,
            )
        except Exception:
            matches = list_translation_bundles(
                state.corpus_root,
                q=q,
                source_textid=source_textid,
                lang=lang,
            )
            total = len(matches)
            page = matches[offset:offset + limit]
        finally:
            conn.close()
            if search_conn is not None:
                search_conn.close()
    else:
        matches = list_translation_bundles(
            state.corpus_root,
            q=q,
            source_textid=source_textid,
            lang=lang,
        )
        total = len(matches)
        page = matches[offset:offset + limit]
    return TranslationListResponse(
        translations=[bundle.summary for bundle in page],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/bundles/{textid}/translations",
    response_model=TranslationListResponse,
    summary="List translations available for a source bundle",
)
def bundle_translations(
    request: Request,
    textid: str = PathParam(...),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> TranslationListResponse:
    state: AppState = request.app.state.bkk
    if state.lookup_bundle(textid) is None:
        raise errors.bundle_not_found(textid)
    conn = state.open_catalog()
    if conn is not None:
        try:
            page, total = list_translation_bundles_from_catalog(
                conn,
                source_textid=textid,
                limit=limit,
                offset=offset,
            )
        except Exception:
            matches = list_translation_bundles(state.corpus_root, source_textid=textid)
            total = len(matches)
            page = matches[offset:offset + limit]
        finally:
            conn.close()
    else:
        matches = list_translation_bundles(state.corpus_root, source_textid=textid)
        total = len(matches)
        page = matches[offset:offset + limit]
    return TranslationListResponse(
        translations=[bundle.summary for bundle in page],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/bundles/{textid}/juan/{seq}/translations/{translation_id}",
    response_model=TranslationAlignmentResponse,
    summary="Align one source juan with a selected translation",
)
def juan_translation_alignment(
    request: Request,
    textid: str = PathParam(...),
    seq: int = PathParam(..., ge=0),
    translation_id: str = PathParam(...),
) -> TranslationAlignmentResponse:
    state: AppState = request.app.state.bkk
    rec = state.lookup_bundle(textid)
    if rec is None:
        raise errors.bundle_not_found(textid)
    translation = None
    conn = state.open_catalog()
    if conn is not None:
        try:
            translation = load_translation_bundle_from_catalog(
                conn,
                translation_id=translation_id,
                source_textid=textid,
                include_juans=True,
            )
        except Exception:
            translation = None
        finally:
            conn.close()
    if translation is None:
        for bundle in list_translation_bundles(state.corpus_root, source_textid=textid):
            if bundle.id == translation_id:
                translation = load_translation_bundle(bundle.path, include_juans=True)
                break
    if translation is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "translation_not_found",
                "textid": textid,
                "translation_id": translation_id,
            },
        )
    juan = selection.load_juan_file(rec.bundle_dir, rec.manifest, rec.textid, seq)
    return align_translation(
        textid=textid,
        seq=seq,
        source_juan=juan,
        translation=translation,
    )
