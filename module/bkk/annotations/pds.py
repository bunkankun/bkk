"""Resolve a DID to its PDS service endpoint."""

from __future__ import annotations

import requests

DEFAULT_PDS = "https://bsky.social"
PLC_DIRECTORY = "https://plc.directory"


def resolve_pds(did: str) -> str:
    """Return the user's PDS endpoint, falling back to bsky.social on lookup failure."""
    try:
        r = requests.get(f"{PLC_DIRECTORY}/{did}", timeout=15)
        r.raise_for_status()
        doc = r.json()
    except (requests.RequestException, ValueError):
        return DEFAULT_PDS

    services = doc.get("service") if isinstance(doc, dict) else None
    if not isinstance(services, list):
        return DEFAULT_PDS
    for svc in services:
        if not isinstance(svc, dict):
            continue
        if svc.get("type") == "AtprotoPersonalDataServer":
            endpoint = svc.get("serviceEndpoint")
            if isinstance(endpoint, str) and endpoint:
                return endpoint
    return DEFAULT_PDS


__all__ = ["resolve_pds", "DEFAULT_PDS"]
