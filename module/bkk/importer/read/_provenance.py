"""Shared helper for lifting ``resp`` + ``date`` from TLS XML elements.

Each core record carries an optional ``source:`` block in its YAML output
holding ``resp`` (responsibility handle, e.g. ``"#CH"``) and ``date`` (an
ISO timestamp). The convention across TLS XML is:

* ``resp`` lives on the record-defining element (``<div>``, ``<entry>``,
  ``<sense>``, ``<tls:metadata>``).
* The creation timestamp uses ``tls:created`` or plain ``created``.
* The modification timestamp uses ``modified`` (on inner ``<p>``) or
  ``updated`` (on inner ``<def>``); it tends to live on the inner prose
  rather than on the record element itself.

``date`` prefers modified/updated when available, falling back to
tls:created/created.
"""

from __future__ import annotations


TLS_NS = "http://hxwd.org/ns/1.0"
TLS_GRAPH_NS = "http://exist-db.org/tls"  # used by graph XML, sigh


def lift_source(*elements) -> dict:
    """Lift ``{resp, date}`` from one or more XML elements.

    Earlier elements have priority for ``resp``; the *latest* date wins
    among the candidates, with modified/updated preferred over
    created/tls:created.
    """
    resp: str | None = None
    date_modified: str | None = None
    date_created: str | None = None

    for el in elements:
        if el is None:
            continue
        if resp is None:
            r = (el.get("resp") or "").strip()
            if r:
                resp = r
        for key in ("modified", "updated"):
            v = (el.get(key) or "").strip()
            if v and (date_modified is None or v > date_modified):
                date_modified = v
        for key in (f"{{{TLS_NS}}}created", f"{{{TLS_GRAPH_NS}}}created", "created"):
            v = (el.get(key) or "").strip()
            if v and (date_created is None or v > date_created):
                date_created = v

    out: dict = {}
    if resp:
        out["resp"] = resp
    date = date_modified or date_created
    if date:
        out["date"] = date
    return out
