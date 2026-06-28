"""Admin duplications editor: list, inspect, and act on rows in dups.tsv.

The TSV report is the source of truth — actions are recorded by rewriting
the row's ``action`` / ``action_actor`` / ``action_at`` columns in place,
then the corresponding bundle mutation runs (delete a juan/bucket, excise
duplicated spans) and the affected bundles' per-bundle ``.bkkx`` files
are rebuilt.

Auth mirrors ``admin.py``: every endpoint requires an authenticated GitHub
session whose user is in the admin team.
"""

from __future__ import annotations

import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Path as PathParam,
    Query,
    Request,
)
from fastapi.responses import JSONResponse

from bkk.edit.sections import EditError, delete_juan_bucket, delete_spans
from bkk.index.duplications import (
    VALID_ACTIONS,
    ReportFormatError,
    read_duplications_report,
    update_action,
)

from .. import errors
from .. import selection
from ..state import AppState, Job, JobRegistry
from .admin import _require_admin

router = APIRouter(prefix="/admin/duplications", tags=["admin"])


# Actions that an admin may submit, grouped by row shape. Used to validate
# requests against the duplications.VALID_ACTIONS set.
_INTER_ACTIONS = frozenset({
    "keep", "delete_a_juan", "delete_b_juan", "delete_a_span", "delete_b_span",
})
_INTRA_ACTIONS = frozenset({"keep", "delete_span"})


def _report_path_or_503(state: AppState):
    path = state.duplications_report_path
    if path is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "duplications report not configured; set [duplications].report "
                "in .bkkrc or BKK_DUPLICATIONS_REPORT"
            ),
        )
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"duplications report missing at {path}; run `bkk index duplications`",
        )
    return path


def _read_rows(state: AppState) -> list[dict]:
    path = _report_path_or_503(state)
    try:
        return read_duplications_report(path)
    except ReportFormatError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _row_summary(row: dict) -> dict[str, Any]:
    """Strip span JSON blobs out of the list payload — only needed in detail."""
    keys = (
        "id",
        "textid_a", "juan_seq_a", "bucket_a",
        "textid_b", "juan_seq_b", "bucket_b",
        "chars_a", "chars_b",
        "juan_length_a", "juan_length_b",
        "coverage_a", "coverage_b",
        "longest_span", "cluster_count", "intra_juan",
        "action", "action_actor", "action_at",
    )
    return {k: row[k] for k in keys}


def _row_full(row: dict) -> dict[str, Any]:
    return {
        **_row_summary(row),
        "longest_a": list(row["longest_a"]),
        "longest_b": list(row["longest_b"]),
        "spans_a": [list(s) for s in row["spans_a"]],
        "spans_b": [list(s) for s in row["spans_b"]],
    }


@router.get("", summary="List duplication rows (paginated)")
def list_duplications(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    filter: str = Query("all", pattern="^(all|pending|done)$"),
    state: AppState = Depends(_require_admin),
) -> dict[str, Any]:
    rows = _read_rows(state)
    if filter == "pending":
        rows = [r for r in rows if not r["action"]]
    elif filter == "done":
        rows = [r for r in rows if r["action"]]
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "returned": len(page),
        "rows": [_row_summary(r) for r in page],
    }


def _context_payload(
    state: AppState, textid: str, juan_seq: int, bucket: str,
    longest: tuple[int, int], window: int,
) -> dict[str, Any]:
    """Return head/tail snippets around ``longest`` for one side."""
    rec = state.lookup_bundle(textid)
    if rec is None:
        raise errors.bundle_not_found(textid)
    juan = selection.load_juan_file(rec.bundle_dir, rec.manifest, textid, juan_seq)
    body = juan.get(bucket) or {}
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=500, detail=f"bucket {bucket!r} of {textid}/{juan_seq} not an object",
        )
    text = body.get("text") or ""
    bucket_len = len(text)

    start, end = longest
    head_lo = max(0, start - window)
    head_hi = min(bucket_len, start + window)
    tail_lo = max(0, end - window)
    tail_hi = min(bucket_len, end + window)

    head = selection.slice_by_offset(
        juan, juan_seq, head_lo, head_hi - head_lo, bucket=bucket,
    )
    tail = selection.slice_by_offset(
        juan, juan_seq, tail_lo, tail_hi - tail_lo, bucket=bucket,
    )
    return {
        "textid": textid,
        "juan_seq": juan_seq,
        "bucket": bucket,
        "bucket_length": bucket_len,
        "longest": [start, end],
        "head": {
            "offset": head_lo, "end": head_hi,
            "text": head.text, "markers": head.markers,
        },
        "tail": {
            "offset": tail_lo, "end": tail_hi,
            "text": tail.text, "markers": tail.markers,
        },
    }


@router.get(
    "/{row_id}",
    summary="Inspect one duplication row with head/tail context around the longest span",
)
def get_duplication(
    request: Request,
    row_id: int = PathParam(..., ge=1),
    window: int = Query(250, ge=0, le=4000),
    state: AppState = Depends(_require_admin),
) -> dict[str, Any]:
    rows = _read_rows(state)
    if row_id > len(rows):
        raise HTTPException(status_code=404, detail=f"row {row_id} not in report")
    row = rows[row_id - 1]
    side_a = _context_payload(
        state, row["textid_a"], row["juan_seq_a"], row["bucket_a"],
        row["longest_a"], window,
    )
    side_b = _context_payload(
        state, row["textid_b"], row["juan_seq_b"], row["bucket_b"],
        row["longest_b"], window,
    )
    return {
        "row": _row_full(row),
        "sides": {"a": side_a, "b": side_b},
    }


def _validate_action(row: dict, action: str) -> None:
    if action not in VALID_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown action {action!r}; valid: {sorted(VALID_ACTIONS)}",
        )
    allowed = _INTRA_ACTIONS if row["intra_juan"] else _INTER_ACTIONS
    if action not in allowed:
        shape = "intra-juan" if row["intra_juan"] else "inter-juan"
        raise HTTPException(
            status_code=400,
            detail=(
                f"action {action!r} not valid for {shape} row; "
                f"valid for this row: {sorted(allowed)}"
            ),
        )


def _bundle_dir(state: AppState, textid: str) -> Path:
    rec = state.lookup_bundle(textid)
    if rec is None:
        raise EditError(f"bundle {textid!r} not found in corpus")
    return rec.bundle_dir


def _execute_deletion(
    state: AppState, row: dict, action: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply the deletion implied by ``action`` to the bundle(s).

    Returns ``(operations, touched_bundles)`` where ``operations`` is the
    list of per-bundle mutation results and ``touched_bundles`` lists the
    text-ids whose source files were mutated. The per-bundle ``.bkkx``
    indexes are left stale on purpose — an admin reruns the index/catalog
    rebuild from the Operations tab once a batch of edits is done.
    """
    if action == "keep":
        return [], []

    ops: list[dict[str, Any]] = []
    touched: set[str] = set()

    if action == "delete_a_juan":
        dir_a = _bundle_dir(state, row["textid_a"])
        ops.append(delete_juan_bucket(
            dir_a, row["textid_a"], row["juan_seq_a"], row["bucket_a"],
        ))
        touched.add(row["textid_a"])
    elif action == "delete_b_juan":
        dir_b = _bundle_dir(state, row["textid_b"])
        ops.append(delete_juan_bucket(
            dir_b, row["textid_b"], row["juan_seq_b"], row["bucket_b"],
        ))
        touched.add(row["textid_b"])
    elif action == "delete_a_span":
        dir_a = _bundle_dir(state, row["textid_a"])
        ops.append(delete_spans(
            dir_a, row["textid_a"], row["juan_seq_a"], row["bucket_a"],
            list(row["spans_a"]),
        ))
        touched.add(row["textid_a"])
    elif action == "delete_b_span":
        dir_b = _bundle_dir(state, row["textid_b"])
        ops.append(delete_spans(
            dir_b, row["textid_b"], row["juan_seq_b"], row["bucket_b"],
            list(row["spans_b"]),
        ))
        touched.add(row["textid_b"])
    elif action == "delete_span":
        # Intra-juan: longest_a is the first occurrence to keep, longest_b
        # is the duplicate to drop. The full spans_a list contains both
        # copies; deleting only longest_b preserves the first.
        dir_a = _bundle_dir(state, row["textid_a"])
        ops.append(delete_spans(
            dir_a, row["textid_a"], row["juan_seq_a"], row["bucket_a"],
            [tuple(row["longest_b"])],
        ))
        touched.add(row["textid_a"])
    else:  # pragma: no cover — _validate_action already gated this
        raise EditError(f"unsupported action {action!r}")

    return ops, sorted(touched)


def _run_action(
    jobs: JobRegistry,
    job_id: str,
    state: AppState,
    report_path: Path,
    row_id: int,
    action: str,
    actor: str,
    at: str,
) -> None:
    """Record the decision, mutate the bundle(s), and rebuild their indexes.

    The TSV rewrite is taken under an exclusive ``fcntl.flock`` so concurrent
    admin actions on different rows serialize. The bundle mutation happens
    after the rewrite — if it fails the TSV still reflects the decision so
    the admin can rerun ``bkk index duplications`` to surface the inconsistency.
    """
    jobs.mark_running(job_id)
    try:
        rows = read_duplications_report(report_path)
        if row_id > len(rows):
            raise EditError(f"row {row_id} not in report")
        row = rows[row_id - 1]
        with open(report_path, "r+", encoding="utf-8") as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            try:
                update_action(report_path, row_id, action, actor=actor, at=at)
            finally:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
        ops, touched_bundles = _execute_deletion(state, row, action)
        jobs.mark_done(job_id, {
            "row_id": row_id,
            "action": action,
            "deletion_executed": action != "keep",
            "operations": ops,
            "touched_bundles": touched_bundles,
        })
    except Exception as exc:
        jobs.mark_error(job_id, exc)


@router.post(
    "/{row_id}/action",
    summary="Record an admin decision against one row (queues a background task)",
)
def post_action(
    request: Request,
    row_id: int = PathParam(..., ge=1),
    background: BackgroundTasks = None,  # type: ignore[assignment]
    payload: dict[str, Any] = Body(..., examples=[{"action": "keep", "confirm": True}]),
    state: AppState = Depends(_require_admin),
) -> JSONResponse:
    action = payload.get("action")
    if not isinstance(action, str):
        raise HTTPException(status_code=400, detail="missing 'action' string in body")
    if not payload.get("confirm"):
        raise HTTPException(status_code=400, detail="set 'confirm': true to apply the action")

    rows = _read_rows(state)
    if row_id > len(rows):
        raise HTTPException(status_code=404, detail=f"row {row_id} not in report")
    row = rows[row_id - 1]
    _validate_action(row, action)

    # Resolve the actor from the session cookie. _require_admin already
    # confirmed the session exists and is_admin; re-fetch to read the login.
    from .auth import SESSION_COOKIE  # local import to avoid circular at module load
    session = state.sessions.get(request.cookies.get(SESSION_COOKIE))
    actor = session.login if session is not None else "unknown"
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    job: Job = state.jobs.create(kind="duplications_action", target=f"row:{row_id}")
    background.add_task(
        _run_action,
        state.jobs, job.id, state, state.duplications_report_path,
        row_id, action, actor, at,
    )
    return JSONResponse(status_code=202, content=job.to_dict())
