"""GitHub-backed editing for per-text BKK bundle repositories."""

from __future__ import annotations

import base64
import binascii
import copy
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import yaml
from fastapi import APIRouter, HTTPException, Path as PathParam, Query, Request
from pydantic import BaseModel, Field

from bkk.edit.offsets import (
    OffsetRebaseConflict,
    map_structural_span,
    rebase_content_span,
)
from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.idassigner import allocate_marker_ids
from bkk.importer.write.yaml_writer import dump, marker_to_flow, reflow_manifest
from bkk.marker_assets import (
    VALID_BUCKETS,
    build_marker_asset,
    effective_markers_for_bucket,
    marker_asset_entry_for_seq,
    marker_asset_filename,
    marker_asset_hash,
    split_inline_external_markers,
    toc_marker_ids,
)
from bkk.repair.parallels import append_stale_record, default_state_root
from bkk.serialize.yaml_io import dumps_record, load_record, loads_record

from ..state import AppState, UserSession
from .auth import SESSION_COOKIE, _github_json, _github_status

router = APIRouter(prefix="/bundles", tags=["bundles"])
log = logging.getLogger("bkk.serve.bundle_edit")

_MARKER_ID_RE = re.compile(r"_(?P<edition>[^_]+)_(?P<seq>\d{3})-")
_EDITION_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class TextSplice(BaseModel):
    start: int = Field(ge=0)
    delete_count: int = Field(ge=0)
    insert: str = ""


class BundleEditRequest(BaseModel):
    base_commit_sha: str
    bucket: Literal["front", "body", "back"]
    text: str
    markers: list[dict[str, Any]]
    text_splices: list[TextSplice] = Field(default_factory=list)
    renamed_marker_ids: dict[str, str] = Field(default_factory=dict)
    acknowledge_toc_deletions: bool = False
    unresolved_marker_indexes: list[int] = Field(default_factory=list)
    message: str | None = None


class BundleMoveSectionRequest(BaseModel):
    base_commit_sha: str
    source_bucket: Literal["front", "body", "back"]
    destination_bucket: Literal["front", "body", "back"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    message: str | None = None


class MarkerIdAllocationRequest(BaseModel):
    base_commit_sha: str
    bucket: Literal["front", "body", "back"]
    marker_types: list[str] = Field(min_length=1, max_length=1000)
    occupied_ids: list[str] = Field(default_factory=list, max_length=100_000)


@dataclass
class CoreRepair:
    rel_path: str
    data: dict[str, Any]
    original_attestations: dict[str, dict[str, Any]]


@dataclass
class CoreRepairResult:
    paths: list[str] = field(default_factory=list)
    commit_shas: list[str] = field(default_factory=list)


@dataclass
class PreparedRepairs:
    core_repairs: list[CoreRepair] = field(default_factory=list)


def _edition_label(manifest: dict[str, Any], short: str) -> str | None:
    for entry in manifest.get("editions") or []:
        if not isinstance(entry, dict) or entry.get("short") != short:
            continue
        label = entry.get("label")
        return label if isinstance(label, str) and label else None
    return None


def _edit_edition_options(
    manifest: dict[str, Any],
    *,
    requested_edition: str | None,
    current_short: str,
) -> list[dict[str, Any]]:
    metadata = manifest.get("metadata") or {}
    edition_meta = metadata.get("edition") or {}
    master_short = (
        edition_meta.get("short")
        if isinstance(edition_meta, dict) and isinstance(edition_meta.get("short"), str)
        else ""
    )
    if not master_short:
        master_short = current_short
    seen_queries: set[str | None] = set()
    options: list[dict[str, Any]] = []
    label = None
    if isinstance(edition_meta, dict) and isinstance(edition_meta.get("label"), str):
        label = edition_meta["label"]
    options.append({
        "short": master_short or "master",
        "label": label or (_edition_label(manifest, master_short) if master_short else None),
        "query": None,
        "scope": "master",
    })
    seen_queries.add(None)
    for entry in manifest.get("editions") or []:
        if not isinstance(entry, dict):
            continue
        short = entry.get("short")
        if not isinstance(short, str) or not short or short == master_short:
            continue
        query = short
        if query in seen_queries:
            continue
        label = entry.get("label")
        options.append({
            "short": short,
            "label": label if isinstance(label, str) and label else None,
            "query": query,
            "scope": "edition",
        })
        seen_queries.add(query)
    if requested_edition is not None and requested_edition not in seen_queries:
        options.append({
            "short": current_short or requested_edition,
            "label": None,
            "query": requested_edition,
            "scope": "edition",
        })
    return options


def _session(request: Request) -> UserSession:
    session = request.app.state.bkk.sessions.get(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="GitHub login required")
    return session


def _repo(state: AppState, textid: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", textid):
        raise HTTPException(status_code=400, detail="invalid bundle textid")
    return f"{state.config.bundle_github_org}/{textid}"


def _branch(state: AppState, token: str, repo: str) -> str:
    configured = state.config.bundle_github_branch
    if configured and configured != "auto":
        return configured
    try:
        payload = _github_json("GET", f"/repos/{repo}", token)
    except HTTPException as exc:
        if _github_status(exc) == 404:
            raise HTTPException(status_code=404, detail=f"{repo} not found") from exc
        raise
    default_branch = (payload or {}).get("default_branch")
    if not isinstance(default_branch, str) or not default_branch:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub repository {repo} has no default branch",
        )
    return default_branch


def _content_path(path: str) -> str:
    return quote(path, safe="/")


def _decode_file(payload: dict[str, Any], path: str) -> str:
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=502, detail=f"GitHub file {path} has no content")
    try:
        return base64.b64decode(content, validate=False).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"GitHub file {path} is not UTF-8") from exc


def _fetch_file(token: str, repo: str, path: str, ref: str) -> tuple[dict[str, Any], str]:
    try:
        payload = _github_json(
            "GET",
            f"/repos/{repo}/contents/{_content_path(path)}?ref={quote(ref, safe='')}",
            token,
        )
    except HTTPException as exc:
        status = _github_status(exc)
        if status == 404:
            raise HTTPException(status_code=404, detail=f"{path} not found in {repo}") from exc
        raise
    if not isinstance(payload, dict) or payload.get("type") != "file":
        raise HTTPException(status_code=502, detail=f"{path} in {repo} is not a file")
    if isinstance(payload.get("content"), str) and payload["content"]:
        return payload, _decode_file(payload, path)
    # The Contents API omits inline content for files above 1 MiB. Fetch the
    # blob explicitly so large juan YAML files remain editable.
    blob_sha = payload.get("sha")
    if not isinstance(blob_sha, str):
        raise HTTPException(status_code=502, detail=f"GitHub file {path} has no blob SHA")
    blob = _github_json("GET", f"/repos/{repo}/git/blobs/{blob_sha}", token)
    if not isinstance(blob, dict):
        raise HTTPException(status_code=502, detail=f"GitHub blob for {path} is invalid")
    return payload, _decode_file(blob, path)


def _head_sha(token: str, repo: str, branch: str) -> str:
    try:
        payload = _github_json(
            "GET", f"/repos/{repo}/git/ref/heads/{quote(branch, safe='')}", token,
        )
    except HTTPException as exc:
        if _github_status(exc) == 404:
            raise HTTPException(status_code=404, detail=f"{repo}@{branch} not found") from exc
        raise
    sha = ((payload or {}).get("object") or {}).get("sha")
    if not isinstance(sha, str):
        raise HTTPException(status_code=502, detail="GitHub branch response has no SHA")
    return sha


def _load_remote(
    token: str,
    repo: str,
    branch: str,
    textid: str,
    seq: int,
    *,
    ref: str | None = None,
    edition: str | None = None,
) -> dict[str, Any]:
    if edition is not None and not _EDITION_RE.fullmatch(edition):
        raise HTTPException(status_code=400, detail="invalid edition short")
    base_sha = ref or _head_sha(token, repo, branch)
    scope_prefix = f"editions/{edition}/" if edition else ""
    manifest_path = (
        f"{scope_prefix}{textid}-{edition}.manifest.yaml"
        if edition
        else f"{textid}.manifest.yaml"
    )
    _manifest_payload, manifest_text = _fetch_file(token, repo, manifest_path, base_sha)
    manifest = yaml.safe_load(manifest_text) or {}
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=422, detail="bundle manifest is not a mapping")
    parts = (manifest.get("assets") or {}).get("parts") or []
    part = next(
        (p for p in parts if isinstance(p, dict) and p.get("seq") == seq), None,
    )
    if part is None or not isinstance(part.get("filename"), str):
        raise HTTPException(status_code=404, detail=f"{textid} juan {seq} not found")
    juan_path = f"{scope_prefix}{part['filename']}"
    _juan_payload, juan_text = _fetch_file(token, repo, juan_path, base_sha)
    juan = yaml.safe_load(juan_text) or {}
    if not isinstance(juan, dict):
        raise HTTPException(status_code=422, detail=f"{juan_path} is not a mapping")

    marker_path: str | None = None
    marker_asset: dict[str, Any] | None = None
    marker_entry = marker_asset_entry_for_seq(manifest, seq)
    if marker_entry is not None and isinstance(marker_entry.get("filename"), str):
        marker_path = f"{scope_prefix}{marker_entry['filename']}"
        _marker_payload, marker_text = _fetch_file(token, repo, marker_path, base_sha)
        parsed = yaml.safe_load(marker_text) or {}
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=422, detail=f"{marker_path} is not a mapping")
        marker_asset = parsed
    return {
        "base_sha": base_sha,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "juan_path": juan_path,
        "juan": juan,
        "marker_path": marker_path,
        "marker_asset": marker_asset,
        "scope_prefix": scope_prefix,
    }


def _validate_markers(
    markers: list[dict[str, Any]], text_len: int, textid: str,
) -> None:
    def label(index: int, marker: dict[str, Any]) -> str:
        marker_type = marker.get("type")
        marker_id = marker.get("id")
        offset = marker.get("offset")
        bits = [f"marker {index}"]
        if isinstance(marker_type, str) and marker_type:
            bits.append(f"type={marker_type}")
        if isinstance(marker_id, str) and marker_id:
            bits.append(f"id={marker_id}")
        if isinstance(offset, int):
            bits.append(f"offset={offset}")
        return " (".join([bits[0], ", ".join(bits[1:]) + ")"]) if len(bits) > 1 else bits[0]

    seen_ids: set[str] = set()
    last_offset = -1
    for index, marker in enumerate(markers):
        marker_type = marker.get("type")
        offset = marker.get("offset")
        if not isinstance(marker_type, str) or not marker_type:
            raise HTTPException(status_code=422, detail=f"{label(index, marker)}: type is required")
        if not isinstance(offset, int) or isinstance(offset, bool):
            raise HTTPException(status_code=422, detail=f"{label(index, marker)}: offset must be an integer")
        if not 0 <= offset <= text_len:
            raise HTTPException(
                status_code=422,
                detail=f"{label(index, marker)}: offset {offset} outside [0, {text_len}]",
            )
        if offset < last_offset:
            raise HTTPException(status_code=422, detail=f"{label(index, marker)}: markers must be ordered by offset")
        last_offset = offset
        length = marker.get("length")
        if length is not None:
            if not isinstance(length, int) or isinstance(length, bool) or length < 0:
                raise HTTPException(status_code=422, detail=f"{label(index, marker)}: length must be non-negative")
            if offset + length > text_len:
                raise HTTPException(status_code=422, detail=f"{label(index, marker)}: span exceeds text")
        marker_id = marker.get("id")
        if isinstance(marker_id, str) and marker_id:
            if marker_id in seen_ids and marker_type not in ("tls:div-start", "tls:div-end"):
                raise HTTPException(status_code=422, detail=f"{label(index, marker)}: duplicate marker id {marker_id}")
            seen_ids.add(marker_id)
            if marker_type not in ("tls:ann", "voice"):
                parts = marker_id.split("_", 2)
                if len(parts) != 3 or not parts[2] or parts[0] != textid:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"{label(index, marker)}: id must match "
                            f"{textid}_<edition>_<location>"
                        ),
                    )


def _replay_splices(original: str, splices: list[TextSplice]) -> str:
    text = original
    for edit in splices:
        end = edit.start + edit.delete_count
        if end > len(text):
            raise HTTPException(status_code=422, detail="text splice is outside the current text")
        text = text[:edit.start] + edit.insert + text[end:]
    return text


def _rebase_toc_spans(
    manifest: dict[str, Any], seq: int, bucket: str, edits: list[TextSplice],
) -> None:
    for entry in manifest.get("table_of_contents") or []:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref")
        if not isinstance(ref, dict) or ref.get("seq") != seq:
            continue
        span = ref.get("span")
        if not (
            isinstance(span, list) and len(span) == 3 and span[0] == bucket
            and isinstance(span[1], int) and isinstance(span[2], int)
        ):
            continue
        start, end = map_structural_span(span[1], span[2], edits)
        ref["span"] = [bucket, start, max(start, end)]


def _cascade_toc_marker_ids(
    manifest: dict[str, Any],
    seq: int,
    all_marker_ids: set[str],
    renamed: dict[str, str],
    acknowledge_deletions: bool,
) -> list[str]:
    for old_id, new_id in renamed.items():
        if not old_id or not new_id or new_id not in all_marker_ids:
            raise HTTPException(
                status_code=422,
                detail=f"renamed marker {old_id!r} does not resolve to {new_id!r}",
            )
    removed: list[str] = []
    kept: list[Any] = []
    for entry in manifest.get("table_of_contents") or []:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        ref = entry.get("ref")
        if not isinstance(ref, dict) or ref.get("seq") != seq:
            kept.append(entry)
            continue
        marker_id = ref.get("marker_id")
        if isinstance(marker_id, str) and marker_id in renamed:
            ref["marker_id"] = renamed[marker_id]
            marker_id = renamed[marker_id]
        if isinstance(marker_id, str) and marker_id and marker_id not in all_marker_ids:
            if not acknowledge_deletions:
                raise HTTPException(
                    status_code=422,
                    detail=f"deleting TOC marker {marker_id} requires acknowledgement",
                )
            removed.append(marker_id)
            continue
        kept.append(entry)
    manifest["table_of_contents"] = kept
    return removed


def _marker_offsets(markers: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for marker in markers:
        marker_id = marker.get("id")
        offset = marker.get("offset")
        if isinstance(marker_id, str) and marker_id and isinstance(offset, int):
            out[marker_id] = offset
    return out


def _marker_buckets(juan: dict[str, Any], marker_asset: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for bucket in VALID_BUCKETS:
        for marker in effective_markers_for_bucket(juan, bucket, marker_asset):
            marker_id = marker.get("id")
            if isinstance(marker_id, str) and marker_id:
                out[marker_id] = bucket
    return out


def _parse_marker_id(marker_id: str) -> tuple[str, int] | None:
    match = _MARKER_ID_RE.search(marker_id)
    if match is None:
        return None
    return match.group("edition"), int(match.group("seq"))


def _attestation_key(rel_path: str, side: str) -> str:
    return f"{rel_path}:{side}"


def _iter_word_relation_records(core_root: Path) -> list[tuple[str, dict[str, Any]]]:
    root = core_root / "word-relations"
    if not root.is_dir():
        return []
    rows: list[tuple[str, dict[str, Any]]] = []
    for shard in sorted(root.iterdir()):
        if not shard.is_dir():
            continue
        for path in sorted(shard.glob("*.yml")):
            try:
                data = load_record(path)
            except OSError as exc:
                log.warning("cannot read core word relation %s: %s", path, exc)
                continue
            if isinstance(data, dict):
                rows.append((path.relative_to(core_root).as_posix(), data))
    return rows


def _prepare_core_attestation_repairs(
    state: AppState,
    session: UserSession,
    remote: dict[str, Any],
    textid: str,
    seq: int,
    request: BundleEditRequest,
) -> list[CoreRepair]:
    if state.core_root is None:
        return []

    original_markers = effective_markers_for_bucket(
        remote["juan"], request.bucket, remote["marker_asset"],
    )
    old_offsets = _marker_offsets(original_markers)
    new_offsets = _marker_offsets(request.markers)
    original_buckets = _marker_buckets(remote["juan"], remote["marker_asset"])
    repairs: list[CoreRepair] = []
    conflicts: list[dict[str, Any]] = []

    for rel_path, data in _iter_word_relation_records(state.core_root):
        changed = False
        next_data = copy.deepcopy(data)
        originals: dict[str, dict[str, Any]] = {}
        for side in ("left", "right"):
            item = next_data.get(side)
            if not isinstance(item, dict):
                continue
            att = item.get("attestation")
            if not isinstance(att, dict):
                continue
            line_id = att.get("line_id")
            if not isinstance(line_id, str) or not line_id.startswith(f"{textid}_"):
                continue
            parsed = _parse_marker_id(line_id)
            if parsed is None or parsed[1] != seq:
                continue
            marker_bucket = original_buckets.get(line_id)
            if marker_bucket != request.bucket:
                continue
            offset = att.get("offset", 0)
            span_length = att.get("range", 1)
            if (
                isinstance(offset, bool)
                or not isinstance(offset, int)
                or offset < 0
                or isinstance(span_length, bool)
                or not isinstance(span_length, int)
                or span_length < 1
            ):
                conflicts.append({
                    "kind": "core-attestation-invalid",
                    "path": rel_path,
                    "side": side,
                    "line_id": line_id,
                })
                continue

            new_line_id = request.renamed_marker_ids.get(line_id, line_id)
            if new_line_id not in new_offsets:
                conflicts.append({
                    "kind": "core-attestation-marker-missing",
                    "path": rel_path,
                    "side": side,
                    "line_id": line_id,
                    "new_line_id": new_line_id,
                })
                continue
            old_marker_offset = old_offsets.get(line_id)
            if old_marker_offset is None:
                conflicts.append({
                    "kind": "core-attestation-marker-missing",
                    "path": rel_path,
                    "side": side,
                    "line_id": line_id,
                })
                continue
            absolute_start = old_marker_offset + offset
            try:
                rebased = rebase_content_span(
                    absolute_start, span_length, request.text_splices,
                )
            except OffsetRebaseConflict:
                conflicts.append({
                    "kind": "core-attestation-overlap",
                    "path": rel_path,
                    "side": side,
                    "line_id": line_id,
                    "start": absolute_start,
                    "length": span_length,
                })
                continue
            new_local_offset = rebased.start - new_offsets[new_line_id]
            if new_local_offset < 0:
                conflicts.append({
                    "kind": "core-attestation-before-marker",
                    "path": rel_path,
                    "side": side,
                    "line_id": line_id,
                    "new_line_id": new_line_id,
                })
                continue
            key = _attestation_key(rel_path, side)
            originals[key] = dict(att)
            if att.get("line_id") != new_line_id:
                att["line_id"] = new_line_id
                changed = True
            if att.get("offset") != new_local_offset:
                att["offset"] = new_local_offset
                changed = True
            if att.get("range") != rebased.length:
                att["range"] = rebased.length
                changed = True

        if changed:
            repairs.append(CoreRepair(rel_path=rel_path, data=next_data, original_attestations=originals))

    if conflicts:
        raise HTTPException(status_code=422, detail={"message": "core attestation conflicts", "conflicts": conflicts})
    if repairs and not session.is_editor:
        raise HTTPException(
            status_code=403,
            detail="Editor role required to repair core attestations for this bundle edit",
        )
    if repairs and not state.config.core_upstream_repo:
        raise HTTPException(
            status_code=503,
            detail="core editing is not configured; cannot repair core attestations",
        )
    return repairs


def _prepare_repairs(
    state: AppState,
    session: UserSession,
    remote: dict[str, Any],
    textid: str,
    seq: int,
    request: BundleEditRequest,
) -> PreparedRepairs:
    if not request.text_splices and not request.renamed_marker_ids:
        return PreparedRepairs()
    core_repairs = _prepare_core_attestation_repairs(
        state, session, remote, textid, seq, request,
    )
    if core_repairs and not session.is_admin:
        raise HTTPException(
            status_code=403,
            detail=(
                "Direct bundle commit permission is required to repair core "
                "attestations"
            ),
        )
    return PreparedRepairs(core_repairs=core_repairs)


def _patch_self_hash(obj: dict[str, Any]) -> str:
    value = copy.deepcopy(obj)
    value["hash"] = ZERO_HASH
    return sha256_jcs(value)


def _prepare_files(
    remote: dict[str, Any], textid: str, seq: int, request: BundleEditRequest,
) -> tuple[dict[str, str | None], list[str]]:
    manifest = copy.deepcopy(remote["manifest"])
    juan = copy.deepcopy(remote["juan"])
    original_bucket = juan.get(request.bucket)
    if not isinstance(original_bucket, dict):
        raise HTTPException(status_code=404, detail=f"bucket {request.bucket} not found")
    original_text = original_bucket.get("text") or ""
    if _replay_splices(original_text, request.text_splices) != request.text:
        raise HTTPException(status_code=422, detail="text splice history does not produce submitted text")
    if request.unresolved_marker_indexes:
        raise HTTPException(status_code=422, detail="ambiguous markers must be resolved before save")
    _validate_markers(request.markers, len(request.text), textid)

    original_bucket["text"] = request.text
    original_bucket["hash"] = sha256_text(request.text) if request.text else ZERO_HASH
    keep_ids = {
        request.renamed_marker_ids.get(marker_id, marker_id)
        for marker_id in toc_marker_ids(manifest, seq)
    }
    inline, external = split_inline_external_markers(request.markers, keep_ids=keep_ids)
    if inline:
        original_bucket["markers"] = [marker_to_flow(dict(marker)) for marker in inline]
    else:
        original_bucket.pop("markers", None)

    marker_asset = copy.deepcopy(remote["marker_asset"])
    marker_path = remote["marker_path"]
    assets = manifest.setdefault("assets", {})
    marker_entries = assets.get("markers") or []
    if external:
        if marker_asset is None:
            edition = ((juan.get("metadata") or {}).get("edition") or {}).get("short")
            edition = edition if isinstance(edition, str) else None
            marker_asset = build_marker_asset(textid, seq, edition, {})
            marker_filename = marker_asset_filename(textid, seq, edition)
            marker_path = f"{remote.get('scope_prefix', '')}{marker_filename}"
            marker_entries = [
                entry for entry in marker_entries
                if not (isinstance(entry, dict) and entry.get("seq") == seq)
            ]
            marker_entries.append({"seq": seq, "filename": marker_filename, "hash": ZERO_HASH})
            marker_entries.sort(key=lambda entry: entry.get("seq", 0))
            assets["markers"] = marker_entries
        markers_obj = marker_asset.setdefault("markers", {})
        markers_obj[request.bucket] = [marker_to_flow(dict(marker)) for marker in external]
    elif marker_asset is not None:
        markers_obj = marker_asset.setdefault("markers", {})
        markers_obj.pop(request.bucket, None)

    _rebase_toc_spans(manifest, seq, request.bucket, request.text_splices)
    all_marker_ids: set[str] = set()
    for bucket in VALID_BUCKETS:
        effective = (
            request.markers
            if bucket == request.bucket
            else effective_markers_for_bucket(juan, bucket, marker_asset)
        )
        all_marker_ids.update(
            marker["id"] for marker in effective
            if isinstance(marker.get("id"), str) and marker["id"]
        )
    removed_toc = _cascade_toc_marker_ids(
        manifest,
        seq,
        all_marker_ids,
        request.renamed_marker_ids,
        request.acknowledge_toc_deletions,
    )

    files: dict[str, str | None] = {}
    if marker_asset is not None and any(
        marker_asset.get("markers", {}).get(bucket) for bucket in VALID_BUCKETS
    ):
        marker_asset["hash"] = marker_asset_hash(marker_asset)
        files[str(marker_path)] = dump(marker_asset)
        entry = marker_asset_entry_for_seq(manifest, seq)
        if entry is not None:
            entry["hash"] = marker_asset["hash"]
    elif marker_path is not None:
        files[str(marker_path)] = None
        assets["markers"] = [
            entry for entry in marker_entries
            if not (isinstance(entry, dict) and entry.get("seq") == seq)
        ]

    juan["hash"] = _patch_self_hash(juan)
    files[remote["juan_path"]] = dump(juan)
    for part in assets.get("parts") or []:
        if isinstance(part, dict) and part.get("seq") == seq:
            part["hash"] = juan["hash"]
            break
    reflow_manifest(manifest)
    manifest["hash"] = manifest_hash(manifest)
    files[remote["manifest_path"]] = dump(manifest)
    return files, removed_toc


def _marker_extent(marker: dict[str, Any], text_len: int) -> tuple[int, int] | None:
    offset = marker.get("offset")
    if not isinstance(offset, int) or isinstance(offset, bool):
        return None
    if offset < 0 or offset > text_len:
        return None
    length = marker.get("length")
    if length is None:
        return offset, offset
    if not isinstance(length, int) or isinstance(length, bool) or length < 0:
        return None
    return offset, min(text_len, offset + length)


def _point_marker_moves(offset: int, start: int, end: int, source_len: int) -> bool:
    return start <= offset < end or (offset == end and end == source_len)


def _move_bucket_markers(
    source_markers: list[dict[str, Any]],
    destination_markers: list[dict[str, Any]],
    *,
    start: int,
    end: int,
    source_len: int,
    destination_len: int,
    destination_insert: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    moved_len = end - start
    next_source: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    for marker in source_markers:
        extent = _marker_extent(marker, source_len)
        if extent is None:
            raise HTTPException(status_code=422, detail="source marker has invalid offset or length")
        marker_start, marker_end = extent
        length = marker.get("length")
        is_point = length is None or length == 0
        if is_point:
            if _point_marker_moves(marker_start, start, end, source_len):
                next_marker = dict(marker)
                next_marker["offset"] = destination_insert + (marker_start - start)
                moved.append(next_marker)
                continue
        elif marker_start >= start and marker_end <= end:
            next_marker = dict(marker)
            next_marker["offset"] = destination_insert + (marker_start - start)
            moved.append(next_marker)
            continue

        overlaps = marker_start < end and marker_end > start
        if overlaps:
            marker_id = marker.get("id")
            label = f" {marker_id}" if isinstance(marker_id, str) and marker_id else ""
            raise HTTPException(
                status_code=422,
                detail=f"marker{label} crosses the moved range boundary",
            )
        next_marker = dict(marker)
        if marker_start >= end:
            next_marker["offset"] = marker_start - moved_len
        next_source.append(next_marker)

    next_destination: list[dict[str, Any]] = []
    for marker in destination_markers:
        extent = _marker_extent(marker, destination_len)
        if extent is None:
            raise HTTPException(status_code=422, detail="destination marker has invalid offset or length")
        marker_start, _marker_end = extent
        next_marker = dict(marker)
        if marker_start >= destination_insert:
            next_marker["offset"] = marker_start + moved_len
        next_destination.append(next_marker)
    next_destination.extend(moved)
    next_destination.sort(key=lambda marker: marker.get("offset", 0))
    return next_source, next_destination


def _rebase_toc_range_after_move(
    span_start: int,
    span_end: int,
    move_start: int,
    move_end: int,
) -> tuple[int, int] | None:
    if span_end <= span_start:
        return None
    moved_len = move_end - move_start
    if span_end <= move_start:
        return span_start, span_end
    if span_start >= move_end:
        return span_start - moved_len, span_end - moved_len
    new_start = span_start if span_start < move_start else move_start
    if span_end <= move_end:
        new_end = move_start
    else:
        new_end = span_end - moved_len
    if new_end <= new_start:
        return None
    return new_start, new_end


def _move_toc_spans(
    manifest: dict[str, Any],
    *,
    seq: int,
    source_bucket: str,
    destination_bucket: str,
    start: int,
    end: int,
    destination_insert: int,
) -> None:
    moved_len = end - start
    for entry in manifest.get("table_of_contents") or []:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref")
        if not isinstance(ref, dict) or ref.get("seq") != seq:
            continue
        span = ref.get("span")
        if not (
            isinstance(span, list)
            and len(span) == 3
            and isinstance(span[0], str)
            and isinstance(span[1], int)
            and isinstance(span[2], int)
        ):
            continue
        bucket, span_start, span_end = span[0], span[1], span[2]
        if bucket == source_bucket:
            if span_start >= start and span_end <= end:
                ref["span"] = [
                    destination_bucket,
                    destination_insert + (span_start - start),
                    destination_insert + (span_end - start),
                ]
                continue
            rebased = _rebase_toc_range_after_move(span_start, span_end, start, end)
            if rebased is None:
                continue
            ref["span"] = [bucket, rebased[0], rebased[1]]
        elif bucket == destination_bucket and span_start >= destination_insert:
            ref["span"] = [bucket, span_start + moved_len, span_end + moved_len]


def _validate_move_policy(
    source_bucket: str,
    destination_bucket: str,
    start: int,
    end: int,
    source_len: int,
) -> Literal["append", "prepend"]:
    if end <= start:
        raise HTTPException(status_code=422, detail="move range must be non-empty")
    if end > source_len:
        raise HTTPException(status_code=422, detail="move range is outside the source bucket")
    if source_bucket == destination_bucket:
        raise HTTPException(status_code=422, detail="source and destination buckets must differ")
    if source_bucket == "body" and destination_bucket == "front":
        if start != 0:
            raise HTTPException(
                status_code=422,
                detail="moving body text to front requires a selection starting at the beginning of body",
            )
        return "append"
    if source_bucket == "body" and destination_bucket == "back":
        if end != source_len:
            raise HTTPException(
                status_code=422,
                detail="moving body text to back requires a selection ending at the end of body",
            )
        return "prepend"
    if source_bucket == "front" and destination_bucket == "body":
        if end != source_len:
            raise HTTPException(
                status_code=422,
                detail="moving front text to body requires a selection ending at the end of front",
            )
        return "prepend"
    if source_bucket == "back" and destination_bucket == "body":
        if start != 0:
            raise HTTPException(
                status_code=422,
                detail="moving back text to body requires a selection starting at the beginning of back",
            )
        return "append"
    raise HTTPException(
        status_code=422,
        detail="section moves are only supported between body/front or body/back",
    )


def _put_bucket_markers(
    juan: dict[str, Any],
    marker_asset: dict[str, Any] | None,
    manifest: dict[str, Any],
    remote: dict[str, Any],
    textid: str,
    seq: int,
    bucket: str,
    markers: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    keep_ids = toc_marker_ids(manifest, seq)
    inline, external = split_inline_external_markers(markers, keep_ids=keep_ids)
    bucket_obj = juan.get(bucket)
    if not isinstance(bucket_obj, dict):
        raise HTTPException(status_code=422, detail=f"bucket {bucket} is missing")
    if inline:
        bucket_obj["markers"] = [marker_to_flow(dict(marker)) for marker in inline]
    else:
        bucket_obj.pop("markers", None)

    marker_path = remote["marker_path"]
    assets = manifest.setdefault("assets", {})
    marker_entries = assets.get("markers") or []
    if external and marker_asset is None:
        edition = ((juan.get("metadata") or {}).get("edition") or {}).get("short")
        edition = edition if isinstance(edition, str) else None
        marker_asset = build_marker_asset(textid, seq, edition, {})
        marker_filename = marker_asset_filename(textid, seq, edition)
        marker_path = f"{remote.get('scope_prefix', '')}{marker_filename}"
        marker_entries = [
            entry for entry in marker_entries
            if not (isinstance(entry, dict) and entry.get("seq") == seq)
        ]
        marker_entries.append({"seq": seq, "filename": marker_filename, "hash": ZERO_HASH})
        marker_entries.sort(key=lambda entry: entry.get("seq", 0))
        assets["markers"] = marker_entries
    if marker_asset is not None:
        markers_obj = marker_asset.setdefault("markers", {})
        if external:
            markers_obj[bucket] = [marker_to_flow(dict(marker)) for marker in external]
        else:
            markers_obj.pop(bucket, None)
    return marker_asset, marker_path


def _prepare_move_files(
    remote: dict[str, Any], textid: str, seq: int, request: BundleMoveSectionRequest,
) -> tuple[dict[str, str | None], list[dict[str, Any]]]:
    manifest = copy.deepcopy(remote["manifest"])
    juan = copy.deepcopy(remote["juan"])
    marker_asset = copy.deepcopy(remote["marker_asset"])
    source = juan.get(request.source_bucket)
    if not isinstance(source, dict):
        raise HTTPException(status_code=404, detail=f"bucket {request.source_bucket} not found")
    destination = juan.get(request.destination_bucket)
    if not isinstance(destination, dict):
        destination = {"text": "", "hash": ZERO_HASH}
        juan[request.destination_bucket] = destination

    source_text = source.get("text") or ""
    destination_text = destination.get("text") or ""
    source_len = len(source_text)
    placement = _validate_move_policy(
        request.source_bucket,
        request.destination_bucket,
        request.start,
        request.end,
        source_len,
    )
    moved_text = source_text[request.start:request.end]
    moved_len = len(moved_text)
    destination_insert = len(destination_text) if placement == "append" else 0

    source_markers = effective_markers_for_bucket(
        juan, request.source_bucket, marker_asset,
    )
    destination_markers = effective_markers_for_bucket(
        juan, request.destination_bucket, marker_asset,
    )
    next_source_markers, next_destination_markers = _move_bucket_markers(
        source_markers,
        destination_markers,
        start=request.start,
        end=request.end,
        source_len=source_len,
        destination_len=len(destination_text),
        destination_insert=destination_insert,
    )

    next_source_text = source_text[:request.start] + source_text[request.end:]
    next_destination_text = (
        destination_text + moved_text
        if placement == "append"
        else moved_text + destination_text
    )

    if next_source_text:
        source["text"] = next_source_text
        source["hash"] = sha256_text(next_source_text)
    else:
        juan.pop(request.source_bucket, None)
        if marker_asset is not None:
            markers_obj = marker_asset.setdefault("markers", {})
            markers_obj.pop(request.source_bucket, None)
    destination["text"] = next_destination_text
    destination["hash"] = sha256_text(next_destination_text) if next_destination_text else ZERO_HASH

    _move_toc_spans(
        manifest,
        seq=seq,
        source_bucket=request.source_bucket,
        destination_bucket=request.destination_bucket,
        start=request.start,
        end=request.end,
        destination_insert=destination_insert,
    )

    marker_path = remote["marker_path"]
    if next_source_text:
        marker_asset, marker_path = _put_bucket_markers(
            juan,
            marker_asset,
            manifest,
            {**remote, "marker_path": marker_path},
            textid,
            seq,
            request.source_bucket,
            next_source_markers,
        )
    marker_asset, marker_path = _put_bucket_markers(
        juan,
        marker_asset,
        manifest,
        {**remote, "marker_path": marker_path},
        textid,
        seq,
        request.destination_bucket,
        next_destination_markers,
    )

    assets = manifest.setdefault("assets", {})
    marker_entries = assets.get("markers") or []
    files: dict[str, str | None] = {}
    if marker_asset is not None and any(
        marker_asset.get("markers", {}).get(bucket) for bucket in VALID_BUCKETS
    ):
        marker_asset["hash"] = marker_asset_hash(marker_asset)
        files[str(marker_path)] = dump(marker_asset)
        entry = marker_asset_entry_for_seq(manifest, seq)
        if entry is not None:
            entry["hash"] = marker_asset["hash"]
    elif marker_path is not None:
        files[str(marker_path)] = None
        assets["markers"] = [
            entry for entry in marker_entries
            if not (isinstance(entry, dict) and entry.get("seq") == seq)
        ]

    if not any(bucket in juan for bucket in VALID_BUCKETS):
        raise HTTPException(status_code=422, detail="move would remove every text bucket")
    juan["hash"] = _patch_self_hash(juan)
    files[remote["juan_path"]] = dump(juan)
    for part in assets.get("parts") or []:
        if isinstance(part, dict) and part.get("seq") == seq:
            part["hash"] = juan["hash"]
            break
    reflow_manifest(manifest)
    manifest["hash"] = manifest_hash(manifest)
    files[remote["manifest_path"]] = dump(manifest)

    splices = [
        {
            "bucket": request.source_bucket,
            "text_splices": [
                TextSplice(start=request.start, delete_count=moved_len, insert=""),
            ],
        },
        {
            "bucket": request.destination_bucket,
            "text_splices": [
                TextSplice(start=destination_insert, delete_count=0, insert=moved_text),
            ],
        },
    ]
    return files, splices


def _create_blob(token: str, repo: str, text: str) -> str:
    result = _github_json(
        "POST", f"/repos/{repo}/git/blobs", token,
        json={"content": text, "encoding": "utf-8"},
    )
    sha = (result or {}).get("sha")
    if not isinstance(sha, str):
        raise HTTPException(status_code=502, detail="GitHub blob response has no SHA")
    return sha


def _commit_files(
    token: str,
    repo: str,
    parent_sha: str,
    branch: str,
    message: str,
    files: dict[str, str | None],
    *,
    create_branch: bool,
) -> str:
    commit = _github_json("GET", f"/repos/{repo}/git/commits/{parent_sha}", token)
    base_tree = ((commit or {}).get("tree") or {}).get("sha")
    if not isinstance(base_tree, str):
        raise HTTPException(status_code=502, detail="GitHub commit response has no tree SHA")
    entries: list[dict[str, Any]] = []
    for path, text in files.items():
        entries.append({
            "path": path,
            "mode": "100644",
            "type": "blob",
            "sha": _create_blob(token, repo, text) if text is not None else None,
        })
    tree = _github_json(
        "POST", f"/repos/{repo}/git/trees", token,
        json={"base_tree": base_tree, "tree": entries},
    )
    tree_sha = (tree or {}).get("sha")
    if not isinstance(tree_sha, str):
        raise HTTPException(status_code=502, detail="GitHub tree response has no SHA")
    created = _github_json(
        "POST", f"/repos/{repo}/git/commits", token,
        json={"message": message, "tree": tree_sha, "parents": [parent_sha]},
    )
    commit_sha = (created or {}).get("sha")
    if not isinstance(commit_sha, str):
        raise HTTPException(status_code=502, detail="GitHub commit response has no SHA")
    ref_path = f"/repos/{repo}/git/refs"
    if create_branch:
        _github_json(
            "POST", ref_path, token,
            json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
        )
    else:
        try:
            _github_json(
                "PATCH", f"{ref_path}/heads/{quote(branch, safe='')}", token,
                json={"sha": commit_sha, "force": False},
            )
        except HTTPException as exc:
            if _github_status(exc) in (409, 422):
                raise HTTPException(status_code=409, detail="bundle changed; reload and retry") from exc
            raise
    return commit_sha


def _core_attestation_values(att: dict[str, Any]) -> dict[str, Any]:
    return {
        key: att.get(key)
        for key in ("line_id", "offset", "range")
        if key in att
    }


def _apply_core_repairs(
    state: AppState,
    session: UserSession,
    repairs: list[CoreRepair],
    *,
    message: str,
) -> CoreRepairResult:
    if not repairs:
        return CoreRepairResult()
    from . import core_edit

    upstream = state.config.core_upstream_repo
    if not upstream:
        raise HTTPException(
            status_code=503,
            detail="core editing is not configured; cannot repair core attestations",
        )
    branch = state.config.core_pr_base
    result = CoreRepairResult()
    for repair in repairs:
        payload = core_edit._fetch_file(
            session.access_token, upstream, repair.rel_path, branch,
        )
        if payload is None:
            raise HTTPException(
                status_code=404,
                detail=f"core file {repair.rel_path} not found on {upstream}@{branch}",
            )
        parent_sha = payload.get("sha")
        if not isinstance(parent_sha, str):
            raise HTTPException(
                status_code=502,
                detail=f"core file {repair.rel_path} response has no sha",
            )
        upstream_record = loads_record(core_edit._decode_file(payload))
        for side in ("left", "right"):
            key = _attestation_key(repair.rel_path, side)
            original = repair.original_attestations.get(key)
            if original is None:
                continue
            upstream_item = upstream_record.get(side)
            repair_item = repair.data.get(side)
            if not isinstance(upstream_item, dict) or not isinstance(repair_item, dict):
                raise HTTPException(
                    status_code=409,
                    detail=f"core file {repair.rel_path} changed before attestation repair",
                )
            upstream_att = upstream_item.get("attestation")
            repair_att = repair_item.get("attestation")
            if not isinstance(upstream_att, dict) or not isinstance(repair_att, dict):
                raise HTTPException(
                    status_code=409,
                    detail=f"core file {repair.rel_path} changed before attestation repair",
                )
            if _core_attestation_values(upstream_att) != _core_attestation_values(original):
                raise HTTPException(
                    status_code=409,
                    detail=f"core file {repair.rel_path} changed before attestation repair",
                )
            upstream_att.update(_core_attestation_values(repair_att))
        text = dumps_record(upstream_record)
        _blob_sha, commit_sha, _commit_url = core_edit._put_file(
            token=session.access_token,
            repo=upstream,
            rel_path=repair.rel_path,
            branch=branch,
            text=text,
            message=f"{message} (repair {repair.rel_path})",
            parent_sha=parent_sha,
        )
        core_edit._local_apply_upsert(state, repair.rel_path, text)
        result.paths.append(repair.rel_path)
        result.commit_shas.append(commit_sha)
    return result


def _record_parallel_stale(
    state: AppState,
    session: UserSession,
    payload: BundleEditRequest,
    textid: str,
    seq: int,
    *,
    result_commit_sha: str | None,
    kind: str,
) -> tuple[bool, str | None, str | None]:
    if not payload.text_splices:
        return False, None, None
    try:
        root = default_state_root(state.parallels_root, state.corpus_root)
        record = append_stale_record(
            root,
            textid=textid,
            seq=seq,
            bucket=payload.bucket,
            base_commit_sha=payload.base_commit_sha,
            result_commit_sha=result_commit_sha,
            text_splices=payload.text_splices,
            login=session.login,
            kind=kind,
        )
        return True, str(record.get("id")), None
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to record stale parallel change for %s/%s: %s", textid, seq, exc)
        return False, None, f"{type(exc).__name__}: {exc}"


def _record_parallel_stale_move(
    state: AppState,
    session: UserSession,
    payload: BundleMoveSectionRequest,
    textid: str,
    seq: int,
    splices_by_bucket: list[dict[str, Any]],
    *,
    result_commit_sha: str | None,
    kind: str,
) -> tuple[bool, str | None, str | None]:
    records: list[str] = []
    try:
        root = default_state_root(state.parallels_root, state.corpus_root)
        for item in splices_by_bucket:
            bucket = item.get("bucket")
            text_splices = item.get("text_splices")
            if not isinstance(bucket, str) or not text_splices:
                continue
            record = append_stale_record(
                root,
                textid=textid,
                seq=seq,
                bucket=bucket,
                base_commit_sha=payload.base_commit_sha,
                result_commit_sha=result_commit_sha,
                text_splices=text_splices,
                login=session.login,
                kind=kind,
            )
            record_id = record.get("id")
            if isinstance(record_id, str):
                records.append(record_id)
        return bool(records), ",".join(records) if records else None, None
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to record stale section move for %s/%s: %s", textid, seq, exc)
        return False, None, f"{type(exc).__name__}: {exc}"


def _ensure_fork(token: str, upstream: str, login: str, textid: str) -> str:
    fork = f"{login}/{textid}"
    try:
        payload = _github_json("GET", f"/repos/{fork}", token)
        parent = ((payload or {}).get("parent") or {}).get("full_name")
        if not isinstance(parent, str) or parent.lower() != upstream.lower():
            raise HTTPException(status_code=409, detail=f"{fork} exists but is not a fork of {upstream}")
        return fork
    except HTTPException as exc:
        if _github_status(exc) != 404:
            raise
    _github_json("POST", f"/repos/{upstream}/forks", token, json={})
    for _ in range(10):
        try:
            _github_json("GET", f"/repos/{fork}", token)
            return fork
        except HTTPException as exc:
            if _github_status(exc) != 404:
                raise
            time.sleep(0.25)
    raise HTTPException(status_code=502, detail="GitHub fork was not ready in time")


@router.get("/{textid}/juan/{seq}/edit")
def get_bundle_edit(
    request: Request,
    textid: str = PathParam(...),
    seq: int = PathParam(..., ge=0),
    edition: str | None = Query(None, description="optional edition subdirectory short"),
) -> dict[str, Any]:
    session = _session(request)
    state: AppState = request.app.state.bkk
    repo = _repo(state, textid)
    branch = _branch(state, session.access_token, repo)
    requested_edition = edition
    remote = _load_remote(session.access_token, repo, branch, textid, seq, edition=requested_edition)
    current_edition = ((remote["juan"].get("metadata") or {}).get("edition") or {}).get("short")
    if not isinstance(current_edition, str) or not current_edition:
        current_edition = ""
    editions_manifest = remote["manifest"]
    if requested_edition is not None:
        try:
            _payload, master_manifest_text = _fetch_file(
                session.access_token,
                repo,
                f"{textid}.manifest.yaml",
                remote["base_sha"],
            )
            parsed = yaml.safe_load(master_manifest_text) or {}
            if isinstance(parsed, dict):
                editions_manifest = parsed
        except HTTPException:
            editions_manifest = remote["manifest"]
    buckets: dict[str, Any] = {}
    for bucket in VALID_BUCKETS:
        bucket_obj = remote["juan"].get(bucket)
        if not isinstance(bucket_obj, dict):
            continue
        buckets[bucket] = {
            "text": bucket_obj.get("text") or "",
            "markers": effective_markers_for_bucket(
                remote["juan"], bucket, remote["marker_asset"],
            ),
        }
    return {
        "repository": repo,
        "branch": branch,
        "base_commit_sha": remote["base_sha"],
        "seq": seq,
        "buckets": buckets,
        "editions": _edit_edition_options(
            editions_manifest,
            requested_edition=requested_edition,
            current_short=current_edition,
        ),
        "toc_marker_ids": sorted(toc_marker_ids(remote["manifest"], seq)),
        "marker_id_context": {
            "edition": current_edition,
            "juan_label": f"{seq:03d}",
        },
    }


@router.post("/{textid}/juan/{seq}/edit/marker-ids")
def allocate_bundle_marker_ids(
    request: Request,
    payload: MarkerIdAllocationRequest,
    textid: str = PathParam(...),
    seq: int = PathParam(..., ge=0),
    edition: str | None = Query(None, description="optional edition subdirectory short"),
) -> dict[str, list[str]]:
    session = _session(request)
    state: AppState = request.app.state.bkk
    repo = _repo(state, textid)
    branch = _branch(state, session.access_token, repo)
    current_sha = _head_sha(session.access_token, repo, branch)
    if current_sha != payload.base_commit_sha:
        raise HTTPException(status_code=409, detail="bundle changed; reload and retry")
    remote = _load_remote(
        session.access_token, repo, branch, textid, seq,
        ref=payload.base_commit_sha,
        edition=edition,
    )
    if not isinstance(remote["juan"].get(payload.bucket), dict):
        raise HTTPException(status_code=404, detail=f"bucket {payload.bucket} not found")
    edition = ((remote["juan"].get("metadata") or {}).get("edition") or {}).get("short")
    if not isinstance(edition, str) or not edition:
        raise HTTPException(status_code=422, detail="juan edition short is required for marker IDs")
    marker_types = []
    for index, marker_type in enumerate(payload.marker_types):
        if not isinstance(marker_type, str) or not marker_type:
            raise HTTPException(
                status_code=422, detail=f"marker type {index} must be a non-empty string",
            )
        marker_types.append(marker_type)
    occupied = set(payload.occupied_ids)
    for bucket in VALID_BUCKETS:
        for marker in effective_markers_for_bucket(
            remote["juan"], bucket, remote["marker_asset"],
        ):
            marker_id = marker.get("id")
            if isinstance(marker_id, str) and marker_id:
                occupied.add(marker_id)
    return {
        "ids": allocate_marker_ids(
            marker_types,
            text_id=textid,
            edition=edition,
            juan_label=f"{seq:03d}",
            occupied_ids=occupied,
        ),
    }


@router.post("/{textid}/juan/{seq}/edit")
def save_bundle_edit(
    request: Request,
    payload: BundleEditRequest,
    textid: str = PathParam(...),
    seq: int = PathParam(..., ge=0),
    edition: str | None = Query(None, description="optional edition subdirectory short"),
) -> dict[str, Any]:
    session = _session(request)
    state: AppState = request.app.state.bkk
    upstream = _repo(state, textid)
    branch = _branch(state, session.access_token, upstream)
    current_sha = _head_sha(session.access_token, upstream, branch)
    if current_sha != payload.base_commit_sha:
        raise HTTPException(status_code=409, detail="bundle changed; reload and retry")
    remote = _load_remote(
        session.access_token, upstream, branch, textid, seq,
        ref=payload.base_commit_sha,
        edition=edition,
    )
    files, removed_toc = _prepare_files(remote, textid, seq, payload)
    repairs = _prepare_repairs(state, session, remote, textid, seq, payload)
    message = payload.message or f"Edit {textid} juan {seq}"

    if session.is_admin:
        commit_sha = _commit_files(
            session.access_token,
            upstream,
            payload.base_commit_sha,
            branch,
            message,
            files,
            create_branch=False,
        )
        core_repair = _apply_core_repairs(
            state, session, repairs.core_repairs, message=message,
        )
        parallel_stale, parallel_stale_id, parallel_stale_error = _record_parallel_stale(
            state,
            session,
            payload,
            textid,
            seq,
            result_commit_sha=commit_sha,
            kind="commit",
        )
        return {
            "kind": "commit",
            "commit_sha": commit_sha,
            "url": f"https://github.com/{upstream}/commit/{commit_sha}",
            "removed_toc_marker_ids": removed_toc,
            "repaired_core_records": len(core_repair.paths),
            "core_repair_paths": core_repair.paths,
            "core_repair_commit_shas": core_repair.commit_shas,
            "repaired_parallel_assets": 0,
            "parallel_repair_bundle_paths": [],
            "parallel_repair_corpus_paths": [],
            "parallel_stale": parallel_stale,
            "parallel_stale_id": parallel_stale_id,
            "parallel_stale_error": parallel_stale_error,
        }

    fork = _ensure_fork(session.access_token, upstream, session.login, textid)
    edit_branch = f"bkk-edit/{seq:03d}-{uuid.uuid4().hex[:10]}"
    commit_sha = _commit_files(
        session.access_token,
        fork,
        payload.base_commit_sha,
        edit_branch,
        message,
        files,
        create_branch=True,
    )
    pr = _github_json(
        "POST", f"/repos/{upstream}/pulls", session.access_token,
        json={
            "title": message,
            "head": f"{session.login}:{edit_branch}",
            "base": branch,
            "body": f"Edits `{textid}` juan {seq} from the BKK web editor.",
        },
    )
    pr_url = (pr or {}).get("html_url")
    pr_number = (pr or {}).get("number")
    if not isinstance(pr_url, str):
        raise HTTPException(status_code=502, detail="GitHub pull request response has no URL")
    parallel_stale, parallel_stale_id, parallel_stale_error = _record_parallel_stale(
        state,
        session,
        payload,
        textid,
        seq,
        result_commit_sha=commit_sha,
        kind="pull_request",
    )
    return {
        "kind": "pull_request",
        "commit_sha": commit_sha,
        "url": pr_url,
        "pull_request_number": pr_number,
        "removed_toc_marker_ids": removed_toc,
        "repaired_core_records": 0,
        "core_repair_paths": [],
        "core_repair_commit_shas": [],
        "repaired_parallel_assets": 0,
        "parallel_repair_bundle_paths": [],
        "parallel_repair_corpus_paths": [],
        "parallel_stale": parallel_stale,
        "parallel_stale_id": parallel_stale_id,
        "parallel_stale_error": parallel_stale_error,
    }


@router.post("/{textid}/juan/{seq}/edit/move-section")
def move_bundle_section(
    request: Request,
    payload: BundleMoveSectionRequest,
    textid: str = PathParam(...),
    seq: int = PathParam(..., ge=0),
    edition: str | None = Query(None, description="optional edition subdirectory short"),
) -> dict[str, Any]:
    session = _session(request)
    state: AppState = request.app.state.bkk
    upstream = _repo(state, textid)
    branch = _branch(state, session.access_token, upstream)
    current_sha = _head_sha(session.access_token, upstream, branch)
    if current_sha != payload.base_commit_sha:
        raise HTTPException(status_code=409, detail="bundle changed; reload and retry")
    remote = _load_remote(
        session.access_token, upstream, branch, textid, seq,
        ref=payload.base_commit_sha,
        edition=edition,
    )
    files, splices_by_bucket = _prepare_move_files(remote, textid, seq, payload)
    message = payload.message or (
        f"Move {textid} juan {seq} {payload.source_bucket} to {payload.destination_bucket}"
    )

    if session.is_admin:
        commit_sha = _commit_files(
            session.access_token,
            upstream,
            payload.base_commit_sha,
            branch,
            message,
            files,
            create_branch=False,
        )
        parallel_stale, parallel_stale_id, parallel_stale_error = _record_parallel_stale_move(
            state,
            session,
            payload,
            textid,
            seq,
            splices_by_bucket,
            result_commit_sha=commit_sha,
            kind="commit",
        )
        return {
            "kind": "commit",
            "commit_sha": commit_sha,
            "url": f"https://github.com/{upstream}/commit/{commit_sha}",
            "removed_toc_marker_ids": [],
            "repaired_core_records": 0,
            "core_repair_paths": [],
            "core_repair_commit_shas": [],
            "repaired_parallel_assets": 0,
            "parallel_repair_bundle_paths": [],
            "parallel_repair_corpus_paths": [],
            "parallel_stale": parallel_stale,
            "parallel_stale_id": parallel_stale_id,
            "parallel_stale_error": parallel_stale_error,
        }

    fork = _ensure_fork(session.access_token, upstream, session.login, textid)
    edit_branch = f"bkk-edit/{seq:03d}-move-{uuid.uuid4().hex[:10]}"
    commit_sha = _commit_files(
        session.access_token,
        fork,
        payload.base_commit_sha,
        edit_branch,
        message,
        files,
        create_branch=True,
    )
    pr = _github_json(
        "POST", f"/repos/{upstream}/pulls", session.access_token,
        json={
            "title": message,
            "head": f"{session.login}:{edit_branch}",
            "base": branch,
            "body": f"Moves a boundary section in `{textid}` juan {seq} from the BKK web editor.",
        },
    )
    pr_url = (pr or {}).get("html_url")
    pr_number = (pr or {}).get("number")
    if not isinstance(pr_url, str):
        raise HTTPException(status_code=502, detail="GitHub pull request response has no URL")
    parallel_stale, parallel_stale_id, parallel_stale_error = _record_parallel_stale_move(
        state,
        session,
        payload,
        textid,
        seq,
        splices_by_bucket,
        result_commit_sha=commit_sha,
        kind="pull_request",
    )
    return {
        "kind": "pull_request",
        "commit_sha": commit_sha,
        "url": pr_url,
        "pull_request_number": pr_number,
        "removed_toc_marker_ids": [],
        "repaired_core_records": 0,
        "core_repair_paths": [],
        "core_repair_commit_shas": [],
        "repaired_parallel_assets": 0,
        "parallel_repair_bundle_paths": [],
        "parallel_repair_corpus_paths": [],
        "parallel_stale": parallel_stale,
        "parallel_stale_id": parallel_stale_id,
        "parallel_stale_error": parallel_stale_error,
    }
