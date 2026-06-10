"""Resolves editorial judgments (state + rating) per target record.

Curation records (``org.bunkankun.curation.judgment``) live on each
editor's PDS. The resolver aggregates harvested records and answers
"what is the current state/rating of target ``X``?" — latest record
(by ``createdAt``) from any DID in the editor allowlist wins, with
DID lexicographic tie-break for deterministic cross-replica resolution.

Self-curation rule: an editor cannot change the ``state`` on their own
record (parsed from the target URI's authority); they CAN change
``rating``. ``admin_dids`` bypass the rule entirely.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable


DEFAULT_STATE = "proposed"
DEFAULT_RATING = 0


def _target_author(target_uri: str) -> str | None:
    """Parse ``at://<did>/<nsid>/<rkey>`` to extract ``<did>``."""
    if not target_uri.startswith("at://"):
        return None
    rest = target_uri[len("at://"):]
    head = rest.split("/", 1)[0]
    return head or None


@dataclass(frozen=True)
class Judgment:
    did: str               # author of the curation record
    rkey: str
    cid: str
    target_uri: str
    target_cid: str | None
    state: str | None      # None when stripped by self-curation rule
    rating: int
    created_at_us: int


class CurationResolver:
    """In-memory aggregator over harvested curation records.

    Keeps *all* observed judgments per target so deletes can fall back to
    the next-latest without re-scanning the PDS.
    """

    def __init__(
        self,
        *,
        editor_dids: Iterable[str] = (),
        admin_dids: Iterable[str] = (),
    ) -> None:
        self._editor_dids: set[str] = set(editor_dids)
        self._admin_dids: set[str] = set(admin_dids)
        self._by_target: dict[str, list[Judgment]] = {}
        self._by_provenance: dict[tuple[str, str], Judgment] = {}

    # -- ingestion ----------------------------------------------------

    def apply(self, j: Judgment) -> bool:
        """Apply ``j`` and return True iff the resolved value changed."""
        j = self._filter_self_state(j)
        key = (j.did, j.rkey)
        prev = self._winner_pair(j.target_uri)

        old = self._by_provenance.pop(key, None)
        if old is not None and old.target_uri in self._by_target:
            self._by_target[old.target_uri] = [
                x for x in self._by_target[old.target_uri]
                if (x.did, x.rkey) != key
            ]
            if not self._by_target[old.target_uri]:
                self._by_target.pop(old.target_uri)

        self._by_provenance[key] = j
        lst = self._by_target.setdefault(j.target_uri, [])
        lst.append(j)
        lst.sort(
            key=lambda x: (x.created_at_us, x.did), reverse=True,
        )

        return prev != self._winner_pair(j.target_uri)

    def remove(self, *, did: str, rkey: str) -> str | None:
        """Remove the record for ``(did, rkey)``.

        Returns the affected ``target_uri`` when the resolved value
        actually changed; otherwise ``None``.
        """
        key = (did, rkey)
        j = self._by_provenance.pop(key, None)
        if j is None:
            return None
        prev = self._winner_pair(j.target_uri)
        lst = self._by_target.get(j.target_uri)
        if lst is not None:
            self._by_target[j.target_uri] = [
                x for x in lst if (x.did, x.rkey) != key
            ]
            if not self._by_target[j.target_uri]:
                self._by_target.pop(j.target_uri)
        return j.target_uri if prev != self._winner_pair(j.target_uri) else None

    # -- queries ------------------------------------------------------

    def get(self, target_uri: str) -> tuple[str, int]:
        """Return resolved ``(state, rating)``. Defaults to ``("proposed", 0)``."""
        return self._winner_pair(target_uri)

    def known_targets(self) -> list[str]:
        return list(self._by_target.keys())

    # -- internals ----------------------------------------------------

    def _filter_self_state(self, j: Judgment) -> Judgment:
        author = _target_author(j.target_uri)
        if author and j.did == author and j.did not in self._admin_dids:
            return replace(j, state=None)
        return j

    def _winner_pair(self, target_uri: str) -> tuple[str, int]:
        state: str | None = None
        rating: int | None = None
        for j in self._by_target.get(target_uri, ()):
            # iteration is already latest-first
            if j.did not in self._editor_dids:
                continue
            if state is None and j.state is not None:
                state = j.state
            if rating is None:
                rating = j.rating
            if state is not None and rating is not None:
                break
        return (
            state if state is not None else DEFAULT_STATE,
            rating if rating is not None else DEFAULT_RATING,
        )


__all__ = [
    "CurationResolver",
    "Judgment",
    "DEFAULT_STATE",
    "DEFAULT_RATING",
]
