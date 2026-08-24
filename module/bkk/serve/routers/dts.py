"""DTS-compatible collection, navigation, and document endpoints."""

from __future__ import annotations

import csv
import html
import re
import sqlite3
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from .. import errors, selection
from ..state import AppState


router = APIRouter(prefix="/dts", tags=["dts"])

DTS_CONTEXT = "https://dtsapi.org/context/v1.0.json"
DTS_VERSION = "1.0"
DTS_NS = "https://w3id.org/api/dts#"
TEI_NS = "http://www.tei-c.org/ns/1.0"
PAGE_SIZE = 100
_TEXTID_RE = re.compile(r"^(?P<section>KR\d+[a-z]+)(?P<number>\d+)$")
_REF_SPAN_RE = re.compile(r"@(?P<offset>\d+)\+(?P<length>\d+)$")


@dataclass(frozen=True)
class CatalogSection:
    code: str
    parent_code: str | None
    title: str | None
    title_pinyin: str | None
    title_english: str | None
    direct_bundle_count: int
    descendant_bundle_count: int


@dataclass(frozen=True)
class CatalogBundle:
    textid: str
    section_code: str
    title: str | None
    title_pinyin: str | None
    title_english: str | None
    not_before: int | None
    not_after: int | None
    dzt_date: int | None
    index_date: int
    canonical_identifier: str | None


@dataclass(frozen=True)
class CtfNode:
    id: str
    parent_id: str
    label: str
    seq: int | None
    start: int | None
    end: int | None
    level: int
    cite_type: str


@router.get("", summary="DTS entry point")
def entry_point(request: Request) -> JSONResponse:
    base = _dts_base(request)
    return _jsonld({
        "@context": DTS_CONTEXT,
        "@id": base,
        "@type": "EntryPoint",
        "dtsVersion": DTS_VERSION,
        "collection": f"{base}/collection{{?id,page,nav}}",
        "navigation": f"{base}/navigation{{?resource,ref,down,start,end,tree,page}}",
        "document": f"{base}/document{{?resource,ref,start,end,tree,mediaType}}",
    })


@router.get("/collection", summary="DTS Collection endpoint")
def collection(
    request: Request,
    id_: str | None = Query(None, alias="id"),
    page: int = Query(1, ge=1),
    nav: str = Query("children", pattern="^(children|parents)$"),
) -> JSONResponse:
    state: AppState = request.app.state.bkk
    sections, bundles = _load_catalog(state)
    base = _dts_base(request)
    top = _top_categories()

    if id_ is None:
        members = [
            _collection_obj(
                code,
                title=top.get(code, {}).get("label") or code,
                zh=top.get(code, {}).get("zh"),
                base=base,
                total_parents=1,
                total_children=_top_child_count(code, sections),
                bundle_count=_top_bundle_count(code, sections),
                include_members=False,
            )
            for code in sorted(k for k in top if re.fullmatch(r"KR\d+", k))
        ]
        return _jsonld(_collection_response(
            request=request,
            id_value="bkk",
            title="BKK",
            members=_paginate(members, page, request),
            total_parents=0,
            total_children=len(members),
            base=base,
            page=page,
        ))

    bundle = bundles.get(id_)
    if bundle is not None:
        body = _resource_obj(bundle, base=base, total_parents=1)
        body.update({
            "@context": DTS_CONTEXT,
            "@id": str(request.url),
            "dtsVersion": DTS_VERSION,
        })
        if nav == "parents":
            parent = sections.get(bundle.section_code)
            body["member"] = [
                _section_member(parent, base=base, include_members=False)
            ] if parent is not None else []
        return _jsonld(body)

    if id_ in top and re.fullmatch(r"KR\d+", id_):
        if nav == "parents":
            members: list[dict[str, Any]] = []
        else:
            members = [
                _section_member(section, base=base, include_members=False)
                for section in sorted(
                    (
                        s for s in sections.values()
                        if s.parent_code is None and s.code.startswith(id_)
                    ),
                    key=lambda s: s.code,
                )
            ]
        info = top[id_]
        return _jsonld(_collection_response(
            request=request,
            id_value=id_,
            title=info.get("label") or id_,
            members=_paginate(members, page, request),
            total_parents=1,
            total_children=len(members),
            base=base,
            page=page,
            zh=info.get("zh"),
            bundle_count=_top_bundle_count(id_, sections),
        ))

    section = sections.get(id_)
    if section is None:
        raise HTTPException(status_code=404, detail={"error": "dts_not_found", "id": id_})

    if nav == "parents":
        parent = _section_parent(section, sections, top, base)
        members = [parent] if parent is not None else []
    else:
        child_sections = [
            _section_member(child, base=base, include_members=False)
            for child in sorted(
                (s for s in sections.values() if s.parent_code == section.code),
                key=lambda s: s.code,
            )
        ]
        child_bundles = [
            _resource_obj(bundle, base=base, total_parents=1)
            for bundle in sorted(
                (b for b in bundles.values() if b.section_code == section.code),
                key=lambda b: (b.index_date, b.textid),
            )
        ]
        members = child_sections + child_bundles
    return _jsonld(_collection_response(
        request=request,
        id_value=section.code,
        title=_section_title(section),
        members=_paginate(members, page, request),
        total_parents=1,
        total_children=len(members),
        base=base,
        page=page,
        zh=section.title,
        bundle_count=section.descendant_bundle_count,
    ))


@router.get("/navigation", summary="DTS Navigation endpoint")
def navigation(
    request: Request,
    resource: str = Query(...),
    ref: str | None = Query(None),
    down: int = Query(1),
    start: str | None = Query(None),
    end: str | None = Query(None),
    tree: str | None = Query(None),
    page: int = Query(1, ge=1),
) -> JSONResponse:
    if start is not None or end is not None:
        raise errors.bad_request("dts_range_navigation_unsupported")
    if tree is not None:
        raise errors.bad_request("dts_tree_unsupported", tree=tree)

    state: AppState = request.app.state.bkk
    _record_or_404(state, resource)
    nodes = _load_ctf_nodes(state, resource)
    by_id = {node.id: node for node in nodes}
    if ref is not None and ref not in by_id:
        raise HTTPException(
            status_code=404,
            detail={"error": "dts_ref_not_found", "resource": resource, "ref": ref},
        )

    selected = _navigation_nodes(nodes, resource=resource, ref=ref, down=down)
    base = _dts_base(request)
    body: dict[str, Any] = {
        "@context": DTS_CONTEXT,
        "@id": str(request.url),
        "@type": "Navigation",
        "dtsVersion": DTS_VERSION,
        "resource": _resource_for_navigation(state, resource, base),
        "member": [
            _citable_unit(node, resource=resource)
            for node in _paginate(selected, page, request)
        ],
    }
    if ref is not None:
        body["ref"] = _citable_unit(by_id[ref], resource=resource)
    _add_view(body, selected, page, request)
    return _jsonld(body)


@router.get("/document", summary="DTS Document endpoint")
def document(
    request: Request,
    resource: str = Query(...),
    ref: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    tree: str | None = Query(None),
    mediaType: str | None = Query(None),
) -> Response:
    if start is not None or end is not None:
        raise errors.bad_request("dts_range_document_unsupported")
    if tree is not None:
        raise errors.bad_request("dts_tree_unsupported", tree=tree)
    media_type = mediaType or "application/tei+xml"
    if media_type not in {"application/tei+xml", "text/plain"}:
        raise HTTPException(
            status_code=404,
            detail={"error": "dts_media_type_not_available", "mediaType": media_type},
        )

    state: AppState = request.app.state.bkk
    rec = _record_or_404(state, resource)
    text = _document_text(state, rec, ref)
    if media_type == "text/plain":
        return Response(text, media_type="text/plain; charset=utf-8")
    return Response(
        _tei_document(text, ref=ref),
        media_type="application/tei+xml; charset=utf-8",
        headers={"Link": f'<{_dts_base(request)}/collection?id={quote(resource)}>; rel="collection"'},
    )


def _jsonld(body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(body, media_type="application/ld+json")


def _dts_base(request: Request) -> str:
    return str(request.base_url).rstrip("/") + "/api/dts"


def _load_catalog(state: AppState) -> tuple[dict[str, CatalogSection], dict[str, CatalogBundle]]:
    conn = state.open_catalog()
    if conn is None:
        raise errors.index_unavailable(
            "catalog index is required for DTS collection access"
        )
    conn.row_factory = sqlite3.Row
    try:
        sections = {
            row["code"]: CatalogSection(
                code=row["code"],
                parent_code=row["parent_code"],
                title=row["title"],
                title_pinyin=row["title_pinyin"],
                title_english=row["title_english"],
                direct_bundle_count=int(row["direct_bundle_count"]),
                descendant_bundle_count=int(row["descendant_bundle_count"]),
            )
            for row in conn.execute("SELECT * FROM catalog_section")
        }
        bundles = {
            row["textid"]: CatalogBundle(
                textid=row["textid"],
                section_code=row["section_code"],
                title=row["title"],
                title_pinyin=row["title_pinyin"],
                title_english=row["title_english"],
                not_before=row["not_before"],
                not_after=row["not_after"],
                dzt_date=row["dzt_date"],
                index_date=int(row["index_date"]),
                canonical_identifier=row["canonical_identifier"],
            )
            for row in conn.execute("SELECT * FROM catalog_bundle")
        }
    finally:
        conn.close()
    return sections, bundles


def _top_categories() -> dict[str, dict[str, str]]:
    raw = yaml.safe_load(
        files("bkk.data").joinpath("kr_categories.yaml").read_text("utf-8")
    )
    return {
        key: value
        for key, value in raw.items()
        if key != "_provenance" and isinstance(value, dict)
    }


def _collection_response(
    *,
    request: Request,
    id_value: str,
    title: str,
    members: list[dict[str, Any]],
    total_parents: int,
    total_children: int,
    base: str,
    page: int,
    zh: str | None = None,
    bundle_count: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = _collection_obj(
        id_value,
        title=title,
        zh=zh,
        base=base,
        total_parents=total_parents,
        total_children=total_children,
        bundle_count=bundle_count,
        include_members=True,
    )
    body.update({
        "@context": DTS_CONTEXT,
        "@id": str(request.url),
        "dtsVersion": DTS_VERSION,
        "member": members,
    })
    _add_view(body, [None] * total_children, page, request)
    return body


def _collection_obj(
    id_value: str,
    *,
    title: str,
    zh: str | None,
    base: str,
    total_parents: int,
    total_children: int,
    bundle_count: int | None = None,
    include_members: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "@id": id_value,
        "@type": "Collection",
        "title": title,
        "totalParents": total_parents,
        "totalChildren": total_children,
        "collection": f"{base}/collection?id={quote(id_value)}{{&page,nav}}",
    }
    if zh:
        out["dublinCore"] = {"title": [{"lang": "zh-Hant", "value": zh}]}
    if bundle_count is not None:
        out["extensions"] = {"bkk:bundleCount": bundle_count}
    if include_members:
        out.setdefault("member", [])
    return out


def _section_member(section: CatalogSection, *, base: str, include_members: bool) -> dict[str, Any]:
    return _collection_obj(
        section.code,
        title=_section_title(section),
        zh=section.title,
        base=base,
        total_parents=1,
        total_children=section.direct_bundle_count,
        bundle_count=section.descendant_bundle_count,
        include_members=include_members,
    )


def _section_parent(
    section: CatalogSection,
    sections: dict[str, CatalogSection],
    top: dict[str, dict[str, str]],
    base: str,
) -> dict[str, Any] | None:
    if section.parent_code is not None:
        parent = sections.get(section.parent_code)
        return _section_member(parent, base=base, include_members=False) if parent else None
    top_code = re.match(r"KR\d+", section.code)
    if top_code is None:
        return None
    code = top_code.group(0)
    info = top.get(code, {})
    return _collection_obj(
        code,
        title=info.get("label") or code,
        zh=info.get("zh"),
        base=base,
        total_parents=1,
        total_children=0,
        include_members=False,
    )


def _resource_obj(bundle: CatalogBundle, *, base: str, total_parents: int) -> dict[str, Any]:
    textid_q = quote(bundle.textid)
    title = bundle.title or bundle.textid
    return {
        "@id": bundle.textid,
        "@type": "Resource",
        "title": title,
        "totalParents": total_parents,
        "collection": f"{base}/collection?id={textid_q}{{&page,nav}}",
        "navigation": f"{base}/navigation?resource={textid_q}{{&ref,down,start,end,tree,page}}",
        "document": f"{base}/document?resource={textid_q}{{&ref,start,end,tree,mediaType}}",
        "mediaTypes": ["application/tei+xml", "text/plain"],
        "citationTrees": [_default_citation_tree()],
        "dublinCore": {
            "title": [{"lang": "zh-Hant", "value": title}],
            **({"alternative": [bundle.title_english]} if bundle.title_english else {}),
        },
        "extensions": {
            key: value
            for key, value in {
                "bkk:textid": bundle.textid,
                "bkk:section": bundle.section_code,
                "bkk:titlePinyin": bundle.title_pinyin,
                "bkk:notBefore": bundle.not_before,
                "bkk:notAfter": bundle.not_after,
                "bkk:dztDate": bundle.dzt_date,
                "bkk:canonicalIdentifier": bundle.canonical_identifier,
            }.items()
            if value is not None
        },
    }


def _resource_for_navigation(state: AppState, textid: str, base: str) -> dict[str, Any]:
    sections, bundles = _load_catalog(state)
    bundle = bundles.get(textid)
    if bundle is None:
        rec = _record_or_404(state, textid)
        bundle = CatalogBundle(
            textid=textid,
            section_code=_section_for_textid(textid) or "",
            title=(rec.manifest.get("metadata") or {}).get("title"),
            title_pinyin=None,
            title_english=None,
            not_before=None,
            not_after=None,
            dzt_date=None,
            index_date=0,
            canonical_identifier=rec.canonical_identifier,
        )
    return _resource_obj(bundle, base=base, total_parents=1)


def _section_title(section: CatalogSection) -> str:
    return section.title_english or section.title_pinyin or section.title or section.code


def _top_child_count(code: str, sections: dict[str, CatalogSection]) -> int:
    return sum(1 for section in sections.values() if section.parent_code is None and section.code.startswith(code))


def _top_bundle_count(code: str, sections: dict[str, CatalogSection]) -> int:
    return sum(
        section.descendant_bundle_count
        for section in sections.values()
        if section.parent_code is None and section.code.startswith(code)
    )


def _paginate(items: list[Any], page: int, request: Request) -> list[Any]:
    start = (page - 1) * PAGE_SIZE
    return items[start:start + PAGE_SIZE]


def _add_view(body: dict[str, Any], all_items: list[Any], page: int, request: Request) -> None:
    if len(all_items) <= PAGE_SIZE and page == 1:
        return
    last_page = max(1, (len(all_items) + PAGE_SIZE - 1) // PAGE_SIZE)
    url = request.url.include_query_params
    body["view"] = {
        "@id": str(request.url),
        "@type": "Pagination",
        "first": str(url(page=1)),
        "last": str(url(page=last_page)),
        **({"previous": str(url(page=page - 1))} if page > 1 else {}),
        **({"next": str(url(page=page + 1))} if page < last_page else {}),
    }


def _record_or_404(state: AppState, textid: str):
    rec = state.lookup_bundle(textid)
    if rec is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "dts_resource_not_found", "resource": textid},
        )
    return rec


def _ctf_root(state: AppState) -> Path:
    root = state.config.ctf_root
    if root is None:
        raise errors.index_unavailable("ctf_root is required for DTS navigation")
    if not root.is_dir():
        raise errors.index_unavailable(f"ctf_root is not a directory: {root}")
    return root


def _load_ctf_nodes(state: AppState, textid: str) -> list[CtfNode]:
    root = _ctf_root(state)
    section = _section_for_textid(textid)
    if section is None:
        return []
    section_dir = root / section
    tsv_path = section_dir / f"{textid}.ctf.tsv"
    if tsv_path.is_file():
        return _read_ctf_tsv(tsv_path, textid)
    nodes: list[CtfNode] = []
    for path in sorted(section_dir.glob(f"{textid}_*.ctf.yaml")):
        nodes.extend(_read_ctf_yaml(path, textid))
    return nodes


def _read_ctf_tsv(path: Path, textid: str) -> list[CtfNode]:
    nodes: list[CtfNode] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            node_id = row.get("id") or ""
            if not node_id or node_id == textid:
                continue
            parent_id = row.get("parent_id") or textid
            label = row.get("label") or node_id
            seq = _seq_from_ref(node_id, textid)
            offset, length = _offset_length_from_ref(node_id)
            end_raw = row.get("end") or ""
            end = int(end_raw) if end_raw.isdigit() else (
                offset + length if offset is not None and length is not None else None
            )
            nodes.append(CtfNode(
                id=node_id,
                parent_id=parent_id,
                label=label,
                seq=seq,
                start=offset,
                end=end,
                level=_level_from_ref(node_id, textid),
                cite_type=_cite_type(node_id, parent_id, textid, end),
            ))
    return nodes


def _read_ctf_yaml(path: Path, textid: str) -> list[CtfNode]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return []
    nodes: list[CtfNode] = []
    for node in data.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        parent_id = node.get("parent_id")
        if not isinstance(node_id, str) or node_id == textid or not isinstance(parent_id, str):
            continue
        span_ref = node.get("span_ref")
        if isinstance(span_ref, str):
            offset, length = _offset_length_from_ref(span_ref)
        else:
            offset, length = _offset_length_from_ref(node_id)
        end = offset + length if offset is not None and length is not None else None
        level = node.get("level")
        nodes.append(CtfNode(
            id=node_id,
            parent_id=parent_id,
            label=node.get("label") if isinstance(node.get("label"), str) else node_id,
            seq=_seq_from_ref(node_id, textid),
            start=offset,
            end=end,
            level=level if isinstance(level, int) else _level_from_ref(node_id, textid),
            cite_type=_cite_type(node_id, parent_id, textid, end if isinstance(span_ref, str) else None),
        ))
    return nodes


def _navigation_nodes(
    nodes: list[CtfNode], *, resource: str, ref: str | None, down: int,
) -> list[CtfNode]:
    children: dict[str, list[CtfNode]] = {}
    for node in nodes:
        children.setdefault(node.parent_id, []).append(node)
    if ref is None:
        roots = children.get(resource, [])
        return roots if down == 1 else _descendants(roots, children, down)
    by_id = {node.id: node for node in nodes}
    root = by_id[ref]
    if down == 0:
        return [root]
    return [root, *_descendants(children.get(ref, []), children, down)]


def _descendants(
    roots: list[CtfNode], children: dict[str, list[CtfNode]], down: int,
) -> list[CtfNode]:
    out: list[CtfNode] = []

    def visit(node: CtfNode, depth: int) -> None:
        if down != -1 and depth > down:
            return
        out.append(node)
        for child in children.get(node.id, []):
            visit(child, depth + 1)

    for root in roots:
        visit(root, 1)
    return out


def _citable_unit(node: CtfNode, *, resource: str) -> dict[str, Any]:
    parent = None if node.parent_id == resource else node.parent_id
    out: dict[str, Any] = {
        "identifier": node.id,
        "@type": "CitableUnit",
        "level": max(1, node.level + 1),
        "parent": parent,
        "citeType": node.cite_type,
        "dublinCore": {"title": [{"lang": "zh-Hant", "value": node.label}]},
    }
    extensions = {
        key: value
        for key, value in {
            "bkk:start": node.start,
            "bkk:end": node.end,
            "bkk:juan": node.seq,
        }.items()
        if value is not None
    }
    if extensions:
        out["extensions"] = extensions
    return out


def _document_text(state: AppState, rec, ref: str | None) -> str:
    if ref is None or ref == rec.textid:
        parts: list[str] = []
        for entry in (rec.manifest.get("assets") or {}).get("parts") or []:
            seq = entry.get("seq")
            if not isinstance(seq, int):
                continue
            juan = selection.load_juan_file(rec.bundle_dir, rec.manifest, rec.textid, seq)
            body = juan.get("body") or {}
            if isinstance(body, dict) and isinstance(body.get("text"), str):
                parts.append(body["text"])
        return "".join(parts)

    nodes = _load_ctf_nodes(state, rec.textid)
    node = next((item for item in nodes if item.id == ref), None)
    if node is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "dts_ref_not_found", "resource": rec.textid, "ref": ref},
        )
    if node.seq is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "dts_ref_not_sliceable", "resource": rec.textid, "ref": ref},
        )
    juan = selection.load_juan_file(rec.bundle_dir, rec.manifest, rec.textid, node.seq)
    if node.start is None or node.end is None:
        return selection.slice_whole(juan, node.seq, bucket="body").text
    return selection.slice_by_offset(
        juan,
        node.seq,
        node.start,
        node.end - node.start,
        bucket="body",
    ).text


def _tei_document(text: str, *, ref: str | None) -> str:
    escaped = html.escape(text, quote=False)
    if ref is None:
        body = f"<text><body>{escaped}</body></text>"
    else:
        body = (
            f'<text><body><dts:wrapper xmlns:dts="{DTS_NS}" '
            f'ref="{html.escape(ref, quote=True)}">{escaped}</dts:wrapper></body></text>'
        )
    return f'<?xml version="1.0" encoding="UTF-8"?><TEI xmlns="{TEI_NS}">{body}</TEI>'


def _default_citation_tree() -> dict[str, Any]:
    return {
        "@type": "CitationTree",
        "citeStructure": [{
            "@type": "CiteStructure",
            "citeType": "juan",
            "citeStructure": [
                {"@type": "CiteStructure", "citeType": "label"},
                {
                    "@type": "CiteStructure",
                    "citeType": "fragment",
                    "citeStructure": [{"@type": "CiteStructure", "citeType": "fragment"}],
                },
            ],
        }],
    }


def _section_for_textid(textid: str) -> str | None:
    match = _TEXTID_RE.fullmatch(textid)
    return match.group("section") if match else None


def _seq_from_ref(ref: str, textid: str) -> int | None:
    prefix = f"{textid}/"
    if not ref.startswith(prefix):
        return None
    part = ref[len(prefix):].split("/", 1)[0]
    return int(part) if part.isdigit() else None


def _offset_length_from_ref(ref: str) -> tuple[int | None, int | None]:
    match = _REF_SPAN_RE.search(ref)
    if match is None:
        return None, None
    return int(match.group("offset")), int(match.group("length"))


def _level_from_ref(ref: str, textid: str) -> int:
    prefix = f"{textid}/"
    if not ref.startswith(prefix):
        return 0
    rest = ref[len(prefix):]
    if "/" not in rest:
        return 0
    before_at = rest.split("/@", 1)[0]
    parts = before_at.split("/")
    return max(0, len(parts) - 1)


def _cite_type(node_id: str, parent_id: str, textid: str, span_end: int | None) -> str:
    if parent_id == textid and _offset_length_from_ref(node_id) == (None, None):
        return "juan"
    if span_end is None:
        return "label"
    return "fragment"
