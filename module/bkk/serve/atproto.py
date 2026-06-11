"""Minimal atproto XRPC client for BKK annotations.

The MVP touches three endpoints — ``com.atproto.server.createSession``,
``com.atproto.server.refreshSession``, ``com.atproto.repo.createRecord``. We
talk to them with ``requests`` rather than pulling in ``atproto-sdk`` (which
targets the firehose and adds heavy crypto deps).
"""

from __future__ import annotations

import time
from typing import Any

import requests
from fastapi import HTTPException


ANNOTATION_NSID = "org.bunkankun.annotation.note"
COMMENT_NSID = "org.bunkankun.comment.post"
TRANSLATION_NSID = "org.bunkankun.translation.segment"
CURATION_NSID = "org.bunkankun.curation.judgment"

# Records posted before the 2026 hierarchical-NSID rename live forever under
# the old flat NSID; the harvester and live feed read both. Once the active
# DID population has re-posted (or aged out), drop this constant and the
# legacy reader paths that key off it.
LEGACY_ANNOTATION_NSID = "org.bunkankun.annotation"

DEFAULT_PDS = "https://bsky.social"
APPVIEW_URL = "https://public.api.bsky.app"


def _headers(jwt: str | None) -> dict[str, str]:
    h = {"Accept": "application/json", "User-Agent": "bkk-serve"}
    if jwt:
        h["Authorization"] = f"Bearer {jwt}"
    return h


def _xrpc(
    method: str,
    service: str,
    nsid: str,
    *,
    jwt: str | None = None,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
    retry_429: bool = True,
) -> Any:
    url = f"{service.rstrip('/')}/xrpc/{nsid}"
    try:
        r = requests.request(
            method,
            url,
            headers=_headers(jwt),
            json=json_body if method != "GET" else None,
            params=params,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"atproto request failed: {exc}") from exc

    if r.status_code == 429 and retry_429:
        delay = r.headers.get("Retry-After")
        try:
            time.sleep(min(30, float(delay)) if delay else 1.0)
        except ValueError:
            time.sleep(1.0)
        return _xrpc(
            method, service, nsid,
            jwt=jwt, json_body=json_body, params=params, retry_429=False,
        )

    if r.status_code >= 400:
        try:
            detail = r.json()
        except ValueError:
            detail = r.text
        raise HTTPException(
            status_code=502,
            detail={"atproto_status": r.status_code, "body": detail},
        )
    if not r.content:
        return None
    return r.json()


def _is_expired(exc: HTTPException) -> bool:
    detail = exc.detail
    if not isinstance(detail, dict):
        return False
    body = detail.get("body")
    if isinstance(body, dict) and body.get("error") in ("ExpiredToken", "InvalidToken"):
        return True
    return detail.get("atproto_status") == 401


def create_session(handle: str, app_password: str, *, service: str = DEFAULT_PDS) -> dict[str, Any]:
    """Exchange handle + app password for an atproto session."""
    return _xrpc(
        "POST", service, "com.atproto.server.createSession",
        json_body={"identifier": handle, "password": app_password},
    )


def refresh_session(refresh_jwt: str, *, service: str = DEFAULT_PDS) -> dict[str, Any]:
    return _xrpc(
        "POST", service, "com.atproto.server.refreshSession",
        jwt=refresh_jwt,
    )


def create_record(
    *,
    service: str,
    access_jwt: str,
    refresh_jwt: str,
    repo: str,
    collection: str,
    record: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """Create a record. On expired access token, refresh once and retry.

    Returns ``(response, new_tokens)``. ``new_tokens`` is non-None when the
    access JWT was refreshed; the caller must persist the new pair.
    """
    body = {"repo": repo, "collection": collection, "record": record}
    try:
        result = _xrpc(
            "POST", service, "com.atproto.repo.createRecord",
            jwt=access_jwt, json_body=body,
        )
        return result, None
    except HTTPException as exc:
        if not _is_expired(exc):
            raise

    refreshed = refresh_session(refresh_jwt, service=service)
    new_access = refreshed.get("accessJwt")
    new_refresh = refreshed.get("refreshJwt")
    if not isinstance(new_access, str) or not isinstance(new_refresh, str):
        raise HTTPException(status_code=502, detail="atproto refresh returned no JWTs")
    result = _xrpc(
        "POST", service, "com.atproto.repo.createRecord",
        jwt=new_access, json_body=body,
    )
    return result, {"access_jwt": new_access, "refresh_jwt": new_refresh}


def delete_record(
    *,
    service: str,
    access_jwt: str,
    refresh_jwt: str,
    repo: str,
    collection: str,
    rkey: str,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    """Delete a record. On expired access token, refresh once and retry.

    Returns ``(response, new_tokens)``. ``new_tokens`` is non-None when the
    access JWT was refreshed; the caller must persist the new pair.
    """
    body = {"repo": repo, "collection": collection, "rkey": rkey}
    try:
        result = _xrpc(
            "POST", service, "com.atproto.repo.deleteRecord",
            jwt=access_jwt, json_body=body,
        )
        return result, None
    except HTTPException as exc:
        if not _is_expired(exc):
            raise

    refreshed = refresh_session(refresh_jwt, service=service)
    new_access = refreshed.get("accessJwt")
    new_refresh = refreshed.get("refreshJwt")
    if not isinstance(new_access, str) or not isinstance(new_refresh, str):
        raise HTTPException(status_code=502, detail="atproto refresh returned no JWTs")
    result = _xrpc(
        "POST", service, "com.atproto.repo.deleteRecord",
        jwt=new_access, json_body=body,
    )
    return result, {"access_jwt": new_access, "refresh_jwt": new_refresh}


def get_profiles(dids: list[str], *, batch_size: int = 25) -> dict[str, dict[str, Any]]:
    """Fetch actor profiles from the Bluesky AppView (no auth needed). Returns {did: profile}."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(dids), batch_size):
        batch = dids[i : i + batch_size]
        try:
            data = _xrpc("GET", APPVIEW_URL, "app.bsky.actor.getProfiles", params={"actors": batch})
            for p in data.get("profiles", []):
                if isinstance(p.get("did"), str):
                    out[p["did"]] = p
        except HTTPException:
            pass
    return out


def list_records(
    *,
    service: str,
    repo: str,
    collection: str,
    limit: int = 100,
    cursor: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"repo": repo, "collection": collection, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    return _xrpc(
        "GET", service, "com.atproto.repo.listRecords",
        params=params,
    )


__all__ = [
    "ANNOTATION_NSID",
    "COMMENT_NSID",
    "TRANSLATION_NSID",
    "LEGACY_ANNOTATION_NSID",
    "APPVIEW_URL",
    "DEFAULT_PDS",
    "create_session",
    "refresh_session",
    "create_record",
    "delete_record",
    "get_profiles",
    "list_records",
]
