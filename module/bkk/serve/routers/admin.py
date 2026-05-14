"""Maintenance endpoints under ``/admin``.

Each ``POST /admin/...`` enqueues a ``BackgroundTask`` and returns ``202
Accepted`` with a job id. The work runs in-process; ``GET /admin/jobs/{id}``
polls status. The job registry lives only in memory and is discarded on
server restart — fine for v1, since these jobs are idempotent and re-runnable.

Auth: if ``ServeConfig.admin_token`` is set, every ``/admin/*`` request must
carry ``Authorization: Bearer <token>``. Mismatch returns 401. If the token
is unset, the routes are open and the app already logs a startup warning to
that effect (see :func:`bkk.serve.app.create_app`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Path as PathParam, Request
from fastapi.responses import JSONResponse

from bkk.index import build_index, merge_bundles
from bkk.validator import validate_bundle

from fastapi import HTTPException

from .. import _examples as ex
from .. import errors
from ..state import AppState, Job, JobRegistry

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(request: Request) -> AppState:
    state: AppState = request.app.state.bkk
    expected = state.config.admin_token
    if not expected:
        return state
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or token.strip() != expected:
        raise HTTPException(
            status_code=401,
            detail={"error": "admin_unauthorized"},
            headers={"WWW-Authenticate": "Bearer"},
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
