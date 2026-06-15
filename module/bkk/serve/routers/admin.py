"""Maintenance endpoints under ``/admin``.

Each ``POST /admin/...`` enqueues a ``BackgroundTask`` and returns ``202
Accepted`` with a job id. The work runs in-process; ``GET /admin/jobs/{id}``
polls status. The job registry lives only in memory and is discarded on
server restart — fine for v1, since these jobs are idempotent and re-runnable.

Auth: every ``/admin/*`` request requires an authenticated GitHub session
(``bkk_session`` cookie) whose user is an active member of the GitHub team
named in ``ServeConfig.admin_team`` (default ``bunkankun/bkk-admin``).
Membership is determined at OAuth callback time and cached on the session;
see :func:`bkk.serve.routers.auth._is_team_member`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Path as PathParam, Request
from fastapi.responses import JSONResponse

from bkk.index import (
    build_annotation_index,
    build_catalog_index,
    build_index,
    merge_bundles,
    merge_translations,
)
from bkk.index.catalog import default_catalog_csv
from bkk.index.core import build_core_index
from bkk.validator import validate_bundle

from fastapi import HTTPException

from .. import _examples as ex
from .. import errors
from ..state import AppState, Job, JobRegistry
from .auth import SESSION_COOKIE

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(request: Request) -> AppState:
    state: AppState = request.app.state.bkk
    session = state.sessions.get(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="Login required")
    if not session.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Admin team membership required",
        )
    return state


def _bundle_dir_or_404(state: AppState, textid: str):
    rec = state.cache.lookup(textid)
    if rec is None:
        raise errors.bundle_not_found(textid)
    return rec.bundle_dir


def _accepted(job: Job) -> JSONResponse:
    return JSONResponse(status_code=202, content=job.to_dict())


def _run_build_index(jobs: JobRegistry, job_id: str, bundle_dir):
    jobs.mark_running(job_id)
    try:
        out = build_index(bundle_dir)
        jobs.mark_done(job_id, {"index_path": str(out)})
    except Exception as exc:
        jobs.mark_error(job_id, exc)


def _run_merge(jobs: JobRegistry, job_id: str, corpus_root, out_path):
    jobs.mark_running(job_id)
    try:
        out = merge_bundles(corpus_root, out_path)
        jobs.mark_done(job_id, {"index_path": str(out)})
    except Exception as exc:
        jobs.mark_error(job_id, exc)


def _run_catalog_index(jobs: JobRegistry, job_id: str, corpus_root, csv_path, out_path):
    jobs.mark_running(job_id)
    try:
        out = build_catalog_index(corpus_root, csv_path, out_path)
        jobs.mark_done(job_id, {"catalog_path": str(out)})
    except Exception as exc:
        jobs.mark_error(job_id, exc)


def _run_translation_index(jobs: JobRegistry, job_id: str, corpus_root, out_path):
    jobs.mark_running(job_id)
    try:
        out = merge_translations(corpus_root, out_path)
        jobs.mark_done(job_id, {"translation_search_path": str(out)})
    except Exception as exc:
        jobs.mark_error(job_id, exc)


def _run_annotation_index(jobs: JobRegistry, job_id: str, annotations_root, out_path):
    jobs.mark_running(job_id)
    try:
        out = build_annotation_index(annotations_root, out_path)
        jobs.mark_done(job_id, {"annotations_index_path": str(out)})
    except Exception as exc:
        jobs.mark_error(job_id, exc)


def _run_self_update(jobs: JobRegistry, job_id: str, source_root, branch: str):
    jobs.mark_running(job_id)
    try:
        import subprocess
        import sys

        fetch = subprocess.run(
            ["git", "-C", str(source_root), "fetch", "origin", branch],
            capture_output=True, text=True, timeout=120,
        )
        if fetch.returncode != 0:
            raise RuntimeError(f"git fetch failed: {fetch.stderr.strip()}")
        merge = subprocess.run(
            ["git", "-C", str(source_root), "merge", "--ff-only", f"origin/{branch}"],
            capture_output=True, text=True, timeout=60,
        )
        if merge.returncode != 0:
            raise RuntimeError(
                f"git merge --ff-only origin/{branch} failed: "
                f"{merge.stderr.strip() or merge.stdout.strip()}"
            )
        head = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        pulled_sha = head.stdout.strip()
        pip = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", f"{source_root}/module"],
            capture_output=True, text=True, timeout=600,
        )
        if pip.returncode != 0:
            raise RuntimeError(
                f"pip install failed:\nstdout:\n{pip.stdout}\nstderr:\n{pip.stderr}"
            )
        jobs.mark_done(job_id, {
            "pulled_sha": pulled_sha,
            "merge_output": merge.stdout.strip(),
            "pip_output": pip.stdout.strip().splitlines()[-20:],
        })
    except Exception as exc:
        jobs.mark_error(job_id, exc)


def _delayed_sigterm():
    import os
    import signal
    import time

    time.sleep(0.5)
    os.kill(os.getpid(), signal.SIGTERM)


def run_core_sync(jobs: JobRegistry, job_id: str, core_root, core_index_path, pr_base):
    jobs.mark_running(job_id)
    try:
        import subprocess

        fetch = subprocess.run(
            ["git", "-C", str(core_root), "fetch", "origin", pr_base],
            capture_output=True, text=True,
        )
        if fetch.returncode != 0:
            raise RuntimeError(f"git fetch failed: {fetch.stderr.strip()}")
        merge = subprocess.run(
            ["git", "-C", str(core_root), "merge", "--ff-only", f"origin/{pr_base}"],
            capture_output=True, text=True,
        )
        if merge.returncode != 0:
            raise RuntimeError(
                f"git merge --ff-only origin/{pr_base} failed: "
                f"{merge.stderr.strip() or merge.stdout.strip()}"
            )
        head = subprocess.run(
            ["git", "-C", str(core_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        pulled_sha = head.stdout.strip()
        out = build_core_index(core_root, core_index_path)
        jobs.mark_done(job_id, {
            "pulled_sha": pulled_sha,
            "core_index_path": str(out),
        })
    except Exception as exc:
        jobs.mark_error(job_id, exc)


def _run_validate(jobs: JobRegistry, job_id: str, bundle_dir):
    jobs.mark_running(job_id)
    try:
        report = validate_bundle(bundle_dir)
        import json
        jobs.mark_done(job_id, json.loads(report.render_json()))
    except Exception as exc:
        jobs.mark_error(job_id, exc)


@router.post(
    "/index/{textid}",
    summary="Rebuild the per-bundle .bkkx for one textid",
)
def post_index_one(
    request: Request,
    background: BackgroundTasks,
    textid: str = PathParam(..., openapi_examples=ex.TEXTID),
    state: AppState = Depends(_require_admin),
) -> JSONResponse:
    bundle_dir = _bundle_dir_or_404(state, textid)
    job = state.jobs.create(kind="index", target=textid)
    background.add_task(_run_build_index, state.jobs, job.id, bundle_dir)
    return _accepted(job)


@router.post(
    "/index",
    summary="Re-merge every bundle into the corpus index",
)
def post_index_all(
    request: Request,
    background: BackgroundTasks,
    state: AppState = Depends(_require_admin),
) -> JSONResponse:
    job = state.jobs.create(kind="merge", target=None)
    background.add_task(
        _run_merge, state.jobs, job.id, state.corpus_root, state.index_path,
    )
    return _accepted(job)


@router.post(
    "/catalog",
    summary="Rebuild the corpus catalog index",
)
def post_catalog_index(
    request: Request,
    background: BackgroundTasks,
    state: AppState = Depends(_require_admin),
) -> JSONResponse:
    csv_path = default_catalog_csv()
    if csv_path is None:
        raise errors.bad_request(
            "catalog_frontmatter_missing",
            reason="could not find catalog/frontmatter.csv from the current directory",
        )
    job = state.jobs.create(kind="catalog", target=None)
    background.add_task(
        _run_catalog_index,
        state.jobs,
        job.id,
        state.corpus_root,
        csv_path,
        state.catalog_path,
    )
    return _accepted(job)


@router.post(
    "/translations",
    summary="Rebuild the translation fulltext search index",
)
def post_translation_search_index(
    request: Request,
    background: BackgroundTasks,
    state: AppState = Depends(_require_admin),
) -> JSONResponse:
    job = state.jobs.create(kind="translation_search", target=None)
    background.add_task(
        _run_translation_index,
        state.jobs,
        job.id,
        state.corpus_root,
        state.translation_search_path,
    )
    return _accepted(job)


@router.post(
    "/annotations",
    summary="Rebuild the annotation location index",
)
def post_annotation_index(
    request: Request,
    background: BackgroundTasks,
    state: AppState = Depends(_require_admin),
) -> JSONResponse:
    if state.annotations_root is None:
        raise errors.bad_request(
            "annotations_root_missing",
            reason="set serve.annotations_root / annotations.annotations_root or BKK_ANNOTATIONS_ROOT",
        )
    job = state.jobs.create(kind="annotation_index", target=None)
    background.add_task(
        _run_annotation_index,
        state.jobs,
        job.id,
        state.annotations_root,
        state.annotations_index_path,
    )
    return _accepted(job)


@router.post(
    "/core/sync",
    summary="Fast-forward the local bkk-core clone from upstream and rebuild its index",
)
def post_core_sync(
    request: Request,
    background: BackgroundTasks,
    state: AppState = Depends(_require_admin),
) -> JSONResponse:
    if state.core_root is None or state.core_index_path is None:
        raise errors.bad_request(
            "core_root_missing",
            reason="set core.root in .bkkrc or BKK_CORE_ROOT to enable /core/* and admin sync",
        )
    job = state.jobs.create(kind="core_sync", target=None)
    background.add_task(
        run_core_sync,
        state.jobs,
        job.id,
        state.core_root,
        state.core_index_path,
        state.config.core_pr_base,
    )
    return _accepted(job)


@router.post(
    "/update",
    summary="git pull the source checkout and reinstall the bkk package",
)
def post_self_update(
    request: Request,
    background: BackgroundTasks,
    state: AppState = Depends(_require_admin),
) -> JSONResponse:
    if state.source_root is None:
        raise errors.bad_request(
            "source_root_missing",
            reason="set serve.source_root in .bkkrc or BKK_SOURCE_ROOT",
        )
    if not (state.source_root / ".git").exists():
        raise errors.bad_request(
            "source_root_not_git",
            reason=f"{state.source_root} is not a git checkout",
        )
    job = state.jobs.create(kind="self_update", target=state.source_branch)
    background.add_task(
        _run_self_update,
        state.jobs,
        job.id,
        state.source_root,
        state.source_branch,
    )
    return _accepted(job)


@router.post(
    "/restart",
    summary="Terminate the server process; a supervisor (systemd) restarts it",
)
def post_restart(
    request: Request,
    background: BackgroundTasks,
    state: AppState = Depends(_require_admin),
) -> JSONResponse:
    del state
    background.add_task(_delayed_sigterm)
    return JSONResponse(status_code=202, content={"status": "restarting"})


@router.post(
    "/validate/{textid}",
    summary="Run the validator over one bundle",
)
def post_validate(
    request: Request,
    background: BackgroundTasks,
    textid: str = PathParam(..., openapi_examples=ex.TEXTID),
    state: AppState = Depends(_require_admin),
) -> JSONResponse:
    bundle_dir = _bundle_dir_or_404(state, textid)
    job = state.jobs.create(kind="validate", target=textid)
    background.add_task(_run_validate, state.jobs, job.id, bundle_dir)
    return _accepted(job)


@router.get(
    "/jobs/{job_id}",
    summary="Poll the status of an admin job",
)
def get_job(
    request: Request,
    job_id: str = PathParam(...),
    state: AppState = Depends(_require_admin),
) -> dict[str, Any]:
    job = state.jobs.get(job_id)
    if job is None:
        raise errors.bad_request("job_not_found", id=job_id)
    return job.to_dict()


@router.get(
    "/info",
    summary="Admin-only health snapshot (corpus, indexes, catalog, config)",
)
def get_admin_info(
    request: Request,
    state: AppState = Depends(_require_admin),
) -> dict[str, Any]:
    from bkk.config import load_rc
    from bkk.info.cli import collect_info_report

    catalog_path = state.catalog_path or (state.corpus_root / "_catalog.bkkc")
    report = collect_info_report(
        corpus=state.corpus_root,
        index_path=state.index_path,
        catalog_path=catalog_path,
        rc=load_rc(),
    )
    report["server_version"] = "0.1.0"
    core_upstream = state.config.core_upstream_repo
    core_editing_enabled = bool(core_upstream and "/" in core_upstream)
    if (
        state.core_index_path is not None
        or state.core_root is not None
        or core_upstream
    ):
        report["core"] = {
            "path": str(state.core_index_path) if state.core_index_path else "",
            "built": bool(
                state.core_index_path and state.core_index_path.exists()
            ),
            "root": str(state.core_root) if state.core_root else None,
            "upstream_repo": core_upstream,
            "pr_base": state.config.core_pr_base,
            "editing_enabled": core_editing_enabled,
        }
    else:
        report["core"] = None
    report["source"] = (
        {
            "path": str(state.source_root),
            "branch": state.source_branch,
            "is_git": (state.source_root / ".git").exists(),
        }
        if state.source_root is not None
        else None
    )
    report["annotations"] = (
        {
            "path": str(state.annotations_index_path),
            "built": state.annotations_index_path.exists(),
        }
        if state.annotations_index_path is not None
        else None
    )
    return report
