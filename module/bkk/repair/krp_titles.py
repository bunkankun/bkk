"""Parse ``catalog/krp-titles.txt`` to extract alt-id assignments.

A typical line looks like::

    KR5a0001 @DZ0001 @JY001 @ZB5a0001 靈寶無量度人上品妙經--

Token[0] is the KRP text-id. Subsequent ``@``-prefixed tokens are
alternate identifiers; the title (and dynasty/author) follows. Section
headers like ``KR5a 洞真部`` have no ``@`` tokens and a short id without a
4-digit sequence — they are skipped.

Filter rules (token body, after the leading ``@``):

- bodies starting with ``ZB`` are dropped (clone catalog).
- bodies starting with capital ``S`` are dropped (Siku / Sibu / etc.
  catalog clones; also catches incidental author/dynasty noise).
- the literal placeholder ``TODO`` is dropped (often interleaved with
  real ids, e.g. ``KR1b0049 @TODO @SK1b0215 ...``).
"""

from __future__ import annotations

import re
from pathlib import Path

_TEXT_ID_RE = re.compile(r"^KR\d[a-z]?\d{4}$")


def _keep(body: str) -> bool:
    if not body:
        return False
    if body == "TODO":
        return False
    if body.startswith("ZB"):
        return False
    if body.startswith("S"):
        return False
    return True


def parse_alt_ids(titles_path: Path) -> dict[str, list[str]]:
    """Return ``{text_id: [alt_id, ...]}`` for every text line in
    ``titles_path``. Text-ids without any surviving alt id are omitted."""
    out: dict[str, list[str]] = {}
    with Path(titles_path).open(encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            tokens = line.split()
            text_id = tokens[0]
            if not _TEXT_ID_RE.match(text_id):
                continue
            alts: list[str] = []
            for tok in tokens[1:]:
                if not tok.startswith("@"):
                    break
                body = tok[1:]
                if _keep(body):
                    alts.append(body)
            if alts:
                out[text_id] = alts
    return out
