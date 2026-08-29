"""Convenience view redirects for compact text references."""

from __future__ import annotations

import re
from urllib.parse import urlencode

from fastapi import APIRouter, Path as PathParam, Query, Request
from fastapi.responses import RedirectResponse

from bkk.exporter.recipe import RecipeError
from bkk.exporter.tei import (
    FragmentRef,
    _parse_fragment_ref,
    _resolve_fragment,
)
from bkk.short_refs import compact_text_id

from .. import errors
from ..state import AppState
from .dts import _load_ctf_nodes

router = APIRouter(tags=["view"])
_BUCKET_SLASH_SPAN_RE = re.compile(r"/(front|body|back)/@")
_BARE_TEXT_ID_RE = re.compile(r"^(?:KR)?[0-9][a-z]{1,2}[0-9]{1,4}$")


@router.get("/view", summary="Redirect a compact text reference into the SPA")
def view_query(
    request: Request,
    ref: str = Query(..., description="compact text or CTF reference"),
) -> RedirectResponse:
    return _redirect_view(request, ref)


@router.get("/view/{ref:path}", summary="Redirect a compact text reference into the SPA")
def view_path(
    request: Request,
    ref: str = PathParam(..., description="compact text or CTF reference"),
) -> RedirectResponse:
    return _redirect_view(request, ref)


def _redirect_view(request: Request, raw_ref: str) -> RedirectResponse:
    state: AppState = request.app.state.bkk
    ref = _normalize_view_ref(raw_ref)
    if not ref:
        raise errors.bad_request("view_ref_missing")
    try:
        parsed = _parse_fragment_ref(ref)
    except RecipeError as exc:
        raise errors.bad_request("view_ref_invalid", ref=raw_ref) from exc
    try:
        fragment = _resolve_fragment(ref, ctf_root=state.config.ctf_root)
    except RecipeError:
        fragment = _resolve_prefix_ctf_ref(state, parsed)
    if getattr(fragment, "length", None) == -1 and parsed.offset is None:
        prefix_fragment = _resolve_prefix_ctf_ref(state, parsed)
        if prefix_fragment.offset is not None and prefix_fragment.length is not None:
            fragment = prefix_fragment
    params = {
        "view_textid": fragment.textid,
        "view_seq": str(fragment.juan),
        "view_bucket": fragment.bucket,
    }
    if fragment.offset is not None and fragment.length is not None and fragment.length >= 0:
        params["view_offset"] = str(fragment.offset)
        params["view_length"] = str(max(1, fragment.length))
    return RedirectResponse(url=f"/?{urlencode(params)}", status_code=303)


def _resolve_prefix_ctf_ref(state: AppState, parsed: FragmentRef) -> FragmentRef:
    if state.config.ctf_root is None:
        return parsed
    try:
        nodes = _load_ctf_nodes(state, parsed.textid)
    except Exception:
        return parsed
    candidates = _ctf_prefix_candidates(parsed)
    for node in nodes:
        if node.id not in candidates and _ctf_node_prefix(node.id) not in candidates:
            continue
        if node.start is None or node.end is None or node.end < node.start:
            return parsed
        return FragmentRef(
            original=parsed.original,
            textid=parsed.textid,
            juan=node.seq if node.seq is not None else parsed.juan,
            bucket=parsed.bucket,
            offset=node.start,
            length=node.end - node.start,
            id_prefix=parsed.id_prefix,
        )
    return parsed


def _ctf_prefix_candidates(ref: FragmentRef) -> set[str]:
    tail = ref.id_prefix.split("/", 2)[2] if ref.id_prefix.count("/") >= 2 else ""
    raw_tail = ref.original.strip().split("/", 2)[2] if ref.original.strip().count("/") >= 2 else tail
    return {
        ref.id_prefix.rstrip("/"),
        f"{ref.textid}/{ref.juan}/{tail}".rstrip("/"),
        f"{compact_text_id(ref.textid)}/{ref.juan}/{tail}".rstrip("/"),
        f"{ref.textid}/{ref.juan}/{raw_tail}".rstrip("/"),
        f"{compact_text_id(ref.textid)}/{ref.juan}/{raw_tail}".rstrip("/"),
    }


def _ctf_node_prefix(node_id: str) -> str:
    if "/@" in node_id:
        return node_id.rsplit("/@", 1)[0]
    return node_id


def _normalize_view_ref(raw_ref: str) -> str:
    ref = raw_ref.strip().replace(" ", "+")
    if _BARE_TEXT_ID_RE.fullmatch(ref):
        ref = f"{ref}/1"
    return _BUCKET_SLASH_SPAN_RE.sub(r"/\1@", ref)
