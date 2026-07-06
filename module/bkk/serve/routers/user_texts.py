"""Authenticated creation and serving metadata for private user text bundles."""

from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
import re
import subprocess
import shutil
import tempfile
import time
import uuid
import threading
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import yaml
from fastapi import APIRouter, HTTPException, Request
from lxml import etree
from pydantic import BaseModel, Field, model_validator

from bkk.chars.run import run_canonicalize
from bkk.chars.canonicalize import canonicalize_text_lenient
from bkk.importer.ir import Bundle
from bkk.importer.read.cbeta import read_cbeta
from bkk.importer.read.krp import _parse_juan_text
from bkk.importer.read.tls import read_tls
from bkk.importer.write.bundle import write_bundle
from bkk.index.build import build_index
from bkk.marker_assets import hydrate_juan_markers, load_marker_asset
from bkk.validator import validate_bundle

from ..state import AppState, UserSession
from .auth import (
    SESSION_COOKIE,
    _github_json,
    _github_status,
    _repo_exists,
)


router = APIRouter(prefix="/user-texts", tags=["user-texts"])

TEXT_ID_RE = re.compile(r"^KR\d+[a-z]\d{4}$")
TEXT_ID_SEARCH_RE = re.compile(r"\b(KR\d+[a-z]\d{4})\b")
PREVIEW_TTL = 30 * 60


class SourceFile(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    content: str


class PreviewRequest(BaseModel):
    format: Literal["krp", "tls", "cbeta"]
    paste: str | None = None
    files: list[SourceFile] = Field(default_factory=list)

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.paste) == bool(self.files):
            raise ValueError("provide exactly one of paste or files")
        return self


class CreateRequest(BaseModel):
    preview_token: str
    text_id: str
    title: str = Field(min_length=1, max_length=500)
    author: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=4000)


def _session(request: Request) -> UserSession:
    session = request.app.state.bkk.sessions.get(
        request.cookies.get(SESSION_COOKIE)
    )
    if session is None:
        raise HTTPException(status_code=401, detail="GitHub login required")
    return session


def _safe_name(name: str) -> str:
    name = Path(name).name
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=422, detail="invalid source filename")
    return name


def _source_files(payload: PreviewRequest) -> list[SourceFile]:
    if payload.files:
        return [SourceFile(name=_safe_name(f.name), content=f.content) for f in payload.files]
    suffix = {"krp": ".txt", "tls": ".xml", "cbeta": ".xml"}[payload.format]
    return [SourceFile(name=f"pasted-source{suffix}", content=payload.paste or "")]


def _detect_text_id(files: list[SourceFile]) -> str | None:
    found: set[str] = set()
    for source in files:
        for value in (source.name, source.content[:100_000]):
            found.update(TEXT_ID_SEARCH_RE.findall(value))
    if len(found) > 1:
        raise HTTPException(
            status_code=422,
            detail=f"source contains inconsistent text IDs: {', '.join(sorted(found))}",
        )
    return next(iter(found), None)


def _text_id_in_use(state: AppState, session: UserSession, text_id: str) -> bool:
    if state.lookup_user_text(session.login, text_id) is not None:
        return True
    return _repo_exists(session.access_token, session.login, text_id) is not None


def _next_text_id(state: AppState, session: UserSession) -> str:
    # Preview only needs a cheap owner-local suggestion. Creation performs the
    # real collision check against the visible corpus before anything is
    # published.
    for number in range(1, 10_000):
        candidate = f"KR9a{number:04d}"
        if not _text_id_in_use(state, session, candidate):
            return candidate
    raise HTTPException(status_code=409, detail="KR9a namespace is exhausted")


def _write_sources(root: Path, files: list[SourceFile]) -> list[Path]:
    paths: list[Path] = []
    for source in files:
        path = root / _safe_name(source.name)
        path.write_text(source.content, encoding="utf-8")
        paths.append(path)
    return paths


def _xml_title(path: Path) -> str | None:
    try:
        tree = etree.parse(str(path), etree.XMLParser(recover=True))
    except (OSError, etree.XMLSyntaxError):
        return None
    for xpath in (
        "//*[local-name()='titleStmt']/*[local-name()='title'][1]",
        "//*[local-name()='title'][1]",
    ):
        values = tree.xpath(xpath)
        if values:
            text = "".join(values[0].itertext()).strip()
            if text:
                return text
    return None


def _detected_title(fmt: str, files: list[SourceFile]) -> str | None:
    if fmt == "krp":
        for source in files:
            for line in source.content.splitlines()[:40]:
                if line.strip().startswith("#+TITLE:"):
                    return line.split(":", 1)[1].strip() or None
        return None
    with tempfile.TemporaryDirectory() as temp:
        paths = _write_sources(Path(temp), files)
        main = next((p for p in paths if "-ann" not in p.name), paths[0])
        return _xml_title(main)


def _apply_metadata(
    bundle: Bundle, *, title: str, author: str | None, notes: str | None,
) -> Bundle:
    bundle.metadata = dict(bundle.metadata)
    bundle.metadata["title"] = title.strip()
    if author and author.strip():
        bundle.metadata["authors"] = [{"name": author.strip()}]
    if notes and notes.strip():
        bundle.metadata["note"] = notes.strip()
    return bundle


def _canonicalize_title(state: AppState, title: str) -> str:
    ctx = state.canon_ctx
    if ctx is None:
        raise HTTPException(
            status_code=503,
            detail=state._canon_ctx_error or "canonicalization context unavailable",
        )
    canonical, _markers, _unmapped = canonicalize_text_lenient(title, ctx)
    return canonical


def _krp_bundle(paths: list[Path], text_id: str) -> Bundle:
    txt_paths = [p for p in paths if p.suffix.lower() == ".txt"]
    if not txt_paths:
        raise ValueError("KRP input requires at least one .txt source file")
    juans = []
    used: set[int] = set()
    for ordinal, path in enumerate(sorted(txt_paths), 1):
        match = re.search(r"_(\d{3,})\.txt$", path.name)
        seq = int(match.group(1)) if match else ordinal
        if seq in used:
            raise ValueError(f"duplicate KRP juan sequence {seq}")
        used.add(seq)
        juans.append(_parse_juan_text(
            path.read_text(encoding="utf-8"), seq, text_id, {}, "user",
        ))
    return Bundle(
        text_id=text_id,
        juans=sorted(juans, key=lambda j: j.seq),
        metadata={
            "identifiers": {"krp": text_id},
            "source": {"repository": "user", "format": "krp"},
        },
        edition_short="user",
        source={"repository": "user", "format": "krp"},
    )


def _tls_bundle(paths: list[Path], text_id: str) -> Bundle:
    xml_paths = [p for p in paths if p.suffix.lower() == ".xml"]
    main = next((p for p in xml_paths if "-ann" not in p.name), None)
    if main is None:
        raise ValueError("TLS input requires a main TEI XML file")
    anns = [p for p in xml_paths if p != main]
    swl = next((p for p in anns if "swl" in p.name.lower()), anns[0] if anns else None)
    doc = next(
        (p for p in anns if "doc" in p.name.lower()),
        anns[1] if len(anns) > 1 else None,
    )
    bundle = read_tls(main, swl, doc, text_id)
    bundle.source = {"repository": "user", "format": "tls"}
    bundle.metadata["source"] = bundle.source
    bundle.metadata.setdefault("identifiers", {})["krp"] = text_id
    return bundle


def _cbeta_old_id(path: Path) -> str:
    tree = etree.parse(str(path), etree.XMLParser(recover=True))
    candidates = tree.xpath(
        "//*[local-name()='idno' and "
        "translate(@type,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')"
        "!='kanripo']/text()"
    )
    for value in candidates:
        value = str(value).strip()
        if value:
            return value
    root_id = tree.getroot().get("{http://www.w3.org/XML/1998/namespace}id")
    return root_id or path.stem


def _cbeta_bundle(paths: list[Path], text_id: str) -> Bundle:
    xml_paths = [p for p in paths if p.suffix.lower() == ".xml"]
    if len(xml_paths) != 1:
        raise ValueError("CBETA input requires exactly one XML file")
    old_id = _cbeta_old_id(xml_paths[0])
    bundle = read_cbeta(
        xml_paths[0],
        {"kr_id": text_id, "old_id": old_id, "title": ""},
    )
    bundle.source = {"repository": "user", "format": "cbeta", "old_id": old_id}
    bundle.metadata["source"] = bundle.source
    return bundle


def _build_bundle(
    fmt: str,
    files: list[SourceFile],
    text_id: str,
    *,
    title: str,
    author: str | None,
    notes: str | None,
) -> tuple[Path, tempfile.TemporaryDirectory]:
    temp = tempfile.TemporaryDirectory(prefix="bkk-user-text-")
    root = Path(temp.name)
    source_root = root / "source"
    out_root = root / "out"
    source_root.mkdir()
    out_root.mkdir()
    paths = _write_sources(source_root, files)
    bundle = _read_source_bundle(fmt, paths, text_id)
    _apply_metadata(bundle, title=title, author=author, notes=notes)
    write_bundle(bundle, out_root)
    return out_root / text_id, temp


def _read_source_bundle(fmt: str, paths: list[Path], text_id: str) -> Bundle:
    if fmt == "krp":
        return _krp_bundle(paths, text_id)
    if fmt == "tls":
        return _tls_bundle(paths, text_id)
    return _cbeta_bundle(paths, text_id)


def _preview_source(
    state: AppState,
    fmt: str,
    files: list[SourceFile],
    text_id: str,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Parse and character-check source without serializing a full bundle.

    Full bundle serialization plus marker-asset validation is intentionally
    deferred to creation, where it is run again before GitHub is touched.
    Preview only needs reader validation and the canonicalization guard.
    Avoiding a YAML round-trip is material for pasted books with tens of
    thousands of punctuation markers.
    """
    ctx = state.canon_ctx
    if ctx is None:
        raise HTTPException(
            status_code=503,
            detail=state._canon_ctx_error or "canonicalization context unavailable",
        )
    try:
        with tempfile.TemporaryDirectory(prefix="bkk-user-preview-") as temp:
            paths = _write_sources(Path(temp), files)
            bundle = _read_source_bundle(fmt, paths, text_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"source import failed: {exc}") from exc
    if not bundle.juans:
        raise HTTPException(status_code=422, detail="source contains no juan")

    substitutions = 0
    findings: list[dict[str, Any]] = []
    for juan in bundle.juans:
        for section_index, section in enumerate(juan.sections, 1):
            _text, emitted, unmapped = canonicalize_text_lenient(section.text, ctx)
            substitutions += len(emitted)
            for issue in unmapped:
                findings.append({
                    "rule_id": "chars.canonical-set",
                    "severity": "error",
                    "path": f"juan {juan.seq} section {section_index}",
                    "message": str(issue),
                })
            for marker_index, marker in enumerate(section.markers):
                if marker.offset < 0 or marker.offset > len(section.text):
                    findings.append({
                        "rule_id": "markers.offset",
                        "severity": "error",
                        "path": f"juan {juan.seq} section {section_index}",
                        "message": (
                            f"marker {marker_index} offset {marker.offset} is "
                            f"outside text length {len(section.text)}"
                        ),
                    })
    errors = [finding for finding in findings if finding["severity"] == "error"]
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "source validation failed", "findings": errors},
        )
    return substitutions, min(juan.seq for juan in bundle.juans), findings


def _canonicalize_and_validate(
    state: AppState, bundle_dir: Path,
) -> tuple[int, list[dict[str, Any]]]:
    ctx = state.canon_ctx
    if ctx is None:
        raise HTTPException(
            status_code=503,
            detail=state._canon_ctx_error or "canonicalization context unavailable",
        )
    output = io.StringIO()
    diagnostics = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(diagnostics):
        rc = run_canonicalize(
            bundle_dir.parent,
            ctx=ctx,
            text_ids=[bundle_dir.name],
            abort_on_error=False,
        )
    if rc != 0:
        raise HTTPException(
            status_code=422,
            detail={
                "message": (
                    "canonicalization failed: source contains unmapped or "
                    "invalid characters"
                ),
                "diagnostics": [
                    line for line in diagnostics.getvalue().splitlines() if line.strip()
                ],
            },
        )
    substitutions = 0
    manifest = yaml.safe_load(
        (bundle_dir / f"{bundle_dir.name}.manifest.yaml").read_text("utf-8")
    )
    for part in (manifest.get("assets") or {}).get("parts") or []:
        juan = yaml.safe_load(
            (bundle_dir / part["filename"]).read_text("utf-8")
        )
        juan = hydrate_juan_markers(
            juan,
            load_marker_asset(bundle_dir, manifest, part["seq"]),
        )
        for bucket in ("front", "body", "back"):
            substitutions += sum(
                marker.get("type") == "substitution"
                for marker in ((juan.get(bucket) or {}).get("markers") or [])
            )
    report = validate_bundle(bundle_dir)
    findings = [
        {
            "rule_id": finding.rule_id,
            "severity": finding.severity,
            "path": finding.path,
            "message": finding.message,
        }
        for finding in report.findings
    ]
    if report.has_errors:
        raise HTTPException(
            status_code=422,
            detail={"message": "bundle validation failed", "findings": findings},
        )
    return substitutions, findings


def _stage(
    state: AppState,
    preview: dict[str, Any],
    text_id: str,
    *,
    title: str,
    author: str | None,
    notes: str | None,
) -> tuple[Path, tempfile.TemporaryDirectory, int, list[dict[str, Any]]]:
    try:
        bundle_dir, temp = _build_bundle(
            preview["format"],
            preview["files"],
            text_id,
            title=title,
            author=author,
            notes=notes,
        )
        substitutions, findings = _canonicalize_and_validate(state, bundle_dir)
        return bundle_dir, temp, substitutions, findings
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"source import failed: {exc}") from exc


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
    )


def _github_initial_commit(
    token: str, repo: str, bundle_dir: Path, text_id: str,
) -> str:
    with tempfile.TemporaryDirectory(prefix="bkk-user-git-") as temp:
        working = Path(temp) / text_id
        shutil.copytree(
            bundle_dir,
            working,
            ignore=shutil.ignore_patterns("*.bkkx", "*.bkkx-journal", "*.bkkx.sha256.json"),
        )
        for cmd in (
            ["init", "-b", "main"],
            ["config", "user.email", "bkk-user-texts@bkk.local"],
            ["config", "user.name", "bkk-user-texts"],
            ["add", "-A"],
            ["commit", "-m", f"Create BKK user text {text_id}"],
        ):
            r = _git(working, *cmd)
            if r.returncode != 0:
                raise HTTPException(
                    status_code=422,
                    detail=f"git {' '.join(cmd[:2])} failed: {(r.stderr or r.stdout).strip()}",
                )
        remote = _git(
            working,
            "remote",
            "add",
            "origin",
            f"https://x-access-token:{token}@github.com/{repo}.git",
        )
        if remote.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=f"git remote add failed: {(remote.stderr or remote.stdout).strip()}",
            )
        push = _git(working, "push", "-u", "origin", "main")
        if push.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=f"git push failed: {(push.stderr or push.stdout).strip()}",
            )
        sha = _git(working, "rev-parse", "HEAD")
        if sha.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=f"git rev-parse failed: {(sha.stderr or sha.stdout).strip()}",
            )
        commit_sha = sha.stdout.strip()
        if not commit_sha:
            raise HTTPException(
                status_code=502,
                detail="git rev-parse returned no commit sha",
            )
        return commit_sha


def _create_repo(session: UserSession, text_id: str) -> dict[str, Any]:
    try:
        return _github_json(
            "POST",
            "/user/repos",
            session.access_token,
            json={
                "name": text_id,
                "private": True,
                "auto_init": False,
                "description": f"BKK user text {text_id}",
            },
        )
    except HTTPException as exc:
        if _github_status(exc) == 422:
            raise HTTPException(
                status_code=409,
                detail=f"GitHub repository {session.login}/{text_id} already exists",
            ) from exc
        raise


def _update_registry(
    session: UserSession, text_id: str, commit_sha: str,
) -> None:
    repo = session.workspace["repo"]
    branch = session.workspace["branch"]
    path = "settings/user-texts.json"
    endpoint = f"/repos/{repo}/contents/{quote(path, safe='/')}"
    registry: dict[str, Any] = {"version": 1, "texts": []}
    sha: str | None = None
    try:
        current = _github_json(
            "GET",
            f"{endpoint}?ref={quote(branch, safe='')}",
            session.access_token,
            expected_statuses={404},
        )
        sha = current.get("sha")
        registry = json.loads(base64.b64decode(current["content"]).decode("utf-8"))
    except HTTPException as exc:
        if _github_status(exc) != 404:
            raise
    texts = [
        item for item in registry.get("texts", [])
        if isinstance(item, dict) and item.get("text_id") != text_id
    ]
    texts.append({
        "text_id": text_id,
        "repository": f"{session.login}/{text_id}",
        "branch": "main",
        "commit_sha": commit_sha,
    })
    body: dict[str, Any] = {
        "message": f"Register user text {text_id}",
        "content": base64.b64encode(
            json.dumps(
                {"version": 1, "texts": sorted(texts, key=lambda i: i["text_id"])},
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
        ).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    _github_json("PUT", endpoint, session.access_token, json=body)


def _remove_registry_entry(session: UserSession, text_id: str) -> None:
    repo = session.workspace["repo"]
    branch = session.workspace["branch"]
    path = "settings/user-texts.json"
    endpoint = f"/repos/{repo}/contents/{quote(path, safe='/')}"
    try:
        current = _github_json(
            "GET",
            f"{endpoint}?ref={quote(branch, safe='')}",
            session.access_token,
            expected_statuses={404},
        )
    except HTTPException as exc:
        if _github_status(exc) == 404:
            return
        raise
    try:
        registry = json.loads(base64.b64decode(current["content"]).decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=502, detail="user-text registry is invalid"
        ) from exc
    texts = [
        item for item in registry.get("texts", [])
        if isinstance(item, dict) and item.get("text_id") != text_id
    ]
    body: dict[str, Any] = {
        "message": f"Remove user text {text_id}",
        "content": base64.b64encode(
            json.dumps(
                {"version": 1, "texts": sorted(texts, key=lambda i: i["text_id"])},
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
        ).decode("ascii"),
        "branch": branch,
        "sha": current.get("sha"),
    }
    _github_json("PUT", endpoint, session.access_token, json=body)


def _load_registry(session: UserSession) -> list[dict[str, Any]]:
    repo = session.workspace["repo"]
    branch = session.workspace["branch"]
    endpoint = (
        f"/repos/{repo}/contents/settings/user-texts.json"
        f"?ref={quote(branch, safe='')}"
    )
    try:
        current = _github_json(
            "GET", endpoint, session.access_token, expected_statuses={404},
        )
    except HTTPException as exc:
        if _github_status(exc) == 404:
            return []
        raise
    try:
        document = json.loads(
            base64.b64decode(current["content"]).decode("utf-8")
        )
    except (KeyError, ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=502, detail="user-text registry is invalid"
        ) from exc
    return [
        item for item in document.get("texts", [])
        if isinstance(item, dict)
        and isinstance(item.get("text_id"), str)
        and isinstance(item.get("repository"), str)
    ]


def _remote_head(session: UserSession, repo: str, branch: str) -> str:
    ref = _github_json(
        "GET",
        f"/repos/{repo}/git/ref/heads/{quote(branch, safe='')}",
        session.access_token,
    )
    sha = ((ref or {}).get("object") or {}).get("sha")
    if not isinstance(sha, str):
        raise HTTPException(status_code=502, detail=f"{repo} has no branch SHA")
    return sha


def _download_remote_bundle(
    session: UserSession, repo: str, commit_sha: str, root: Path,
) -> None:
    tree = _github_json(
        "GET",
        f"/repos/{repo}/git/trees/{commit_sha}?recursive=1",
        session.access_token,
    )
    for item in tree.get("tree", []):
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        rel = item.get("path")
        sha = item.get("sha")
        if not isinstance(rel, str) or not isinstance(sha, str):
            continue
        target = (root / rel).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="unsafe path in user bundle") from exc
        blob = _github_json(
            "GET", f"/repos/{repo}/git/blobs/{sha}", session.access_token,
        )
        try:
            raw = base64.b64decode(blob["content"])
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=f"invalid GitHub blob {rel}") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def _promote_synced_bundle(
    state: AppState, owner: str, text_id: str, staged: Path,
) -> None:
    destination = state.user_text_dir(owner, text_id)
    incoming = destination.with_name(f".{text_id}.incoming-{uuid.uuid4().hex}")
    backup = destination.with_name(f".{text_id}.previous-{uuid.uuid4().hex}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staged, incoming)
    if destination.exists():
        destination.rename(backup)
    try:
        incoming.rename(destination)
    except Exception:
        if backup.exists() and not destination.exists():
            backup.rename(destination)
        raise
    finally:
        if incoming.exists():
            shutil.rmtree(incoming)
        if backup.exists():
            shutil.rmtree(backup)


def _sync_registered_texts(
    state: AppState, session: UserSession, text_id_only: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        entries = _load_registry(session)
    except Exception as exc:
        return [{"status": "failed", "error": str(exc)}]
    for entry in entries:
        text_id = entry["text_id"]
        if text_id_only is not None and text_id != text_id_only:
            continue
        repo = entry["repository"]
        branch = entry.get("branch") or "main"
        if not TEXT_ID_RE.fullmatch(text_id):
            continue
        try:
            head = _remote_head(session, repo, branch)
            current = state.user_text_status(session.login, text_id)
            destination = state.user_text_dir(session.login, text_id)
            if destination.is_dir() and current.get("commit_sha") == head:
                results.append({"text_id": text_id, "status": "unchanged"})
                continue
            if (
                destination.is_dir()
                and entry.get("commit_sha") == head
                and not current.get("commit_sha")
            ):
                state.set_user_text_status(
                    session.login,
                    text_id,
                    repository=repo,
                    commit_sha=head,
                    sync_status="ready",
                    index_status=(
                        "ready"
                        if (destination / f"{text_id}.bkkx").is_file()
                        else "pending"
                    ),
                )
                results.append({"text_id": text_id, "status": "unchanged"})
                continue
            with tempfile.TemporaryDirectory(prefix="bkk-user-sync-") as temp:
                staged = Path(temp) / text_id
                staged.mkdir()
                _download_remote_bundle(session, repo, head, staged)
                manifest = staged / f"{text_id}.manifest.yaml"
                if not manifest.is_file():
                    raise ValueError(f"{repo} does not contain {manifest.name}")
                _canonicalize_and_validate(state, staged)
                _promote_synced_bundle(state, session.login, text_id, staged)
            build_index(state.user_text_dir(session.login, text_id))
            state.set_user_text_status(
                session.login,
                text_id,
                repository=repo,
                commit_sha=head,
                sync_status="ready",
                index_status="ready",
            )
            results.append({"text_id": text_id, "status": "updated"})
        except Exception as exc:
            state.set_user_text_status(
                session.login,
                text_id,
                repository=repo,
                sync_status="failed",
                sync_error=str(exc),
            )
            results.append({"text_id": text_id, "status": "failed", "error": str(exc)})
    return results


def _run_index(state: AppState, owner: str, text_id: str) -> None:
    bundle_dir = state.user_text_dir(owner, text_id)
    if not bundle_dir.is_dir():
        return
    state.set_user_text_status(owner, text_id, index_status="indexing")
    try:
        build_index(bundle_dir)
    except Exception as exc:
        if not bundle_dir.is_dir():
            return
        state.set_user_text_status(
            owner, text_id, index_status="failed", index_error=str(exc),
        )
    else:
        if not bundle_dir.is_dir():
            return
        state.set_user_text_status(owner, text_id, index_status="ready")


def _start_index_later(
    state: AppState, owner: str, text_id: str, *, delay_s: float = 5.0,
) -> None:
    def _worker() -> None:
        if delay_s > 0:
            time.sleep(delay_s)
        _run_index(state, owner, text_id)

    threading.Thread(
        target=_worker,
        name=f"bkk-user-text-index-{owner}-{text_id}",
        daemon=True,
    ).start()


@router.post("/preview")
def preview_user_text(request: Request, payload: PreviewRequest) -> dict[str, Any]:
    session = _session(request)
    state: AppState = request.app.state.bkk
    files = _source_files(payload)
    size = sum(len(item.content.encode("utf-8")) for item in files)
    if size > state.config.user_text_upload_limit:
        raise HTTPException(status_code=413, detail="user text upload is too large")
    detected = _detect_text_id(files)
    suggested = (
        detected
        if detected is not None and not _text_id_in_use(state, session, detected)
        else _next_text_id(state, session)
    )
    title = _detected_title(payload.format, files)
    if title is not None:
        title = _canonicalize_title(state, title)
    draft = {"format": payload.format, "files": files, "owner": session.login}
    substitutions, first_seq, findings = _preview_source(
        state, payload.format, files, suggested,
    )
    token = uuid.uuid4().hex
    with state._user_text_lock:
        now = time.time()
        state._user_text_previews = {
            key: value
            for key, value in state._user_text_previews.items()
            if now - value["created_at"] < PREVIEW_TTL
        }
        state._user_text_previews[token] = {
            **draft,
            "created_at": now,
            "detected_text_id": detected,
        }
    return {
        "preview_token": token,
        "format": payload.format,
        "detected_text_id": detected,
        "suggested_text_id": suggested,
        "title": title,
        "source_files": [item.name for item in files],
        "substitution_count": substitutions,
        "findings": findings,
        "first_seq": first_seq,
    }


@router.post("", status_code=201)
def create_user_text(
    request: Request,
    payload: CreateRequest,
) -> dict[str, Any]:
    session = _session(request)
    state: AppState = request.app.state.bkk
    if not TEXT_ID_RE.fullmatch(payload.text_id):
        raise HTTPException(
            status_code=422,
            detail="text_id must match KR<number><lowercase-letter><four digits>",
        )
    if state.lookup_visible_bundle(payload.text_id, session.login) is not None:
        raise HTTPException(status_code=409, detail=f"text ID {payload.text_id} already exists")
    with state._user_text_lock:
        preview = copy.deepcopy(state._user_text_previews.get(payload.preview_token))
    if (
        preview is None
        or preview["owner"] != session.login
        or time.time() - preview["created_at"] >= PREVIEW_TTL
    ):
        raise HTTPException(status_code=404, detail="preview expired or not found")

    bundle_dir, temp, substitutions, findings = _stage(
        state,
        preview,
        payload.text_id,
        title=_canonicalize_title(state, payload.title),
        author=payload.author,
        notes=payload.notes,
    )
    try:
        repo_info = _create_repo(session, payload.text_id)
        repo = repo_info.get("full_name") or f"{session.login}/{payload.text_id}"
        commit_sha = _github_initial_commit(
            session.access_token, repo, bundle_dir, payload.text_id,
        )
        destination = state.user_text_dir(session.login, payload.text_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise HTTPException(status_code=409, detail="local user text already exists")
        shutil.copytree(bundle_dir, destination)
        registry_ok = True
        try:
            _update_registry(session, payload.text_id, commit_sha)
        except HTTPException:
            # The bundle repository is already durable. Keep serving it and
            # report registry repair through sync status rather than losing it.
            state.set_user_text_status(
                session.login,
                payload.text_id,
                sync_status="registry-error",
            )
            registry_ok = False
        manifest = yaml.safe_load(
            (destination / f"{payload.text_id}.manifest.yaml").read_text("utf-8")
        )
        first_seq = min(
            part["seq"] for part in manifest.get("assets", {}).get("parts", [])
        )
        state.set_user_text_status(
            session.login,
            payload.text_id,
            repository=repo,
            repository_url=repo_info.get("html_url"),
            commit_sha=commit_sha,
            sync_status="ready" if registry_ok else "registry-error",
            index_status="pending",
        )
        _start_index_later(state, session.login, payload.text_id)
        with state._user_text_lock:
            state._user_text_previews.pop(payload.preview_token, None)
        return {
            "text_id": payload.text_id,
            "title": _canonicalize_title(state, payload.title),
            "repository": repo,
            "repository_url": repo_info.get("html_url"),
            "commit_sha": commit_sha,
            "first_seq": first_seq,
            "substitution_count": substitutions,
            "findings": findings,
            "index_status": "pending",
        }
    finally:
        temp.cleanup()


@router.get("")
def list_user_texts(request: Request) -> dict[str, Any]:
    session = _session(request)
    state: AppState = request.app.state.bkk
    texts = []
    for rec in state.user_text_records(session.login):
        status = state.user_text_status(session.login, rec.textid)
        index_path = rec.bundle_dir / f"{rec.textid}.bkkx"
        texts.append({
            "text_id": rec.textid,
            "title": rec.title,
            "index_status": status.get(
                "index_status", "ready" if index_path.is_file() else "pending"
            ),
            "sync_status": status.get("sync_status", "ready"),
            **{k: v for k, v in status.items() if k not in {"index_status", "sync_status"}},
        })
    return {"texts": sorted(texts, key=lambda item: item["text_id"])}


@router.post("/sync")
def sync_user_texts(request: Request) -> dict[str, Any]:
    session = _session(request)
    state: AppState = request.app.state.bkk
    return {"results": _sync_registered_texts(state, session)}


@router.delete("/{text_id}")
def delete_user_text(
    request: Request,
    text_id: str,
    confirm_github_delete: bool = False,
) -> dict[str, Any]:
    session = _session(request)
    state: AppState = request.app.state.bkk
    if not TEXT_ID_RE.fullmatch(text_id):
        raise HTTPException(status_code=422, detail="invalid user text ID")
    record = state.lookup_user_text(session.login, text_id)
    if record is None:
        raise HTTPException(status_code=404, detail="user text not found")
    if not confirm_github_delete:
        raise HTTPException(
            status_code=428,
            detail="GitHub delete confirmation required",
        )

    try:
        _github_json(
            "DELETE",
            f"/repos/{session.login}/{text_id}",
            session.access_token,
            expected_statuses={404},
        )
    except HTTPException as exc:
        if _github_status(exc) != 404:
            if _github_status(exc) == 403:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "GitHub repository delete requires the delete_repo OAuth "
                        "scope. Log out and sign back in to refresh the token."
                    ),
                ) from exc
            raise
    _remove_registry_entry(session, text_id)
    shutil.rmtree(record.bundle_dir)
    state.delete_user_text_status(session.login, text_id)
    return {
        "text_id": text_id,
        "deleted": True,
        "github_deleted": True,
    }
