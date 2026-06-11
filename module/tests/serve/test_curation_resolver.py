"""Unit tests for ``bkk.serve.curation.CurationResolver``."""

from __future__ import annotations

from bkk.serve.curation import (
    CurationResolver,
    DEFAULT_RATING,
    DEFAULT_STATE,
    Judgment,
)


EDITOR_A = "did:plc:editor-a"
EDITOR_B = "did:plc:editor-b"
ADMIN = "did:plc:admin"
AUTHOR = "did:plc:author"
TARGET_URI = f"at://{AUTHOR}/org.bunkankun.annotation.note/abc"


def _j(
    *,
    did: str,
    rkey: str = "r1",
    state: str | None = "accepted",
    rating: int = 0,
    created_at_us: int = 1,
    target_uri: str = TARGET_URI,
) -> Judgment:
    return Judgment(
        did=did,
        rkey=rkey,
        cid=f"bafy-{rkey}",
        target_uri=target_uri,
        target_cid="bafy-target",
        state=state,
        rating=rating,
        created_at_us=created_at_us,
    )


def _resolver(*, admin: bool = False) -> CurationResolver:
    return CurationResolver(
        editor_dids={EDITOR_A, EDITOR_B, AUTHOR, ADMIN},
        admin_dids={ADMIN} if admin else set(),
    )


def test_empty_returns_proposed_zero():
    r = _resolver()
    assert r.get(TARGET_URI) == (DEFAULT_STATE, DEFAULT_RATING)
    assert (DEFAULT_STATE, DEFAULT_RATING) == ("proposed", 0)


def test_latest_wins_by_created_at():
    r = _resolver()
    r.apply(_j(did=EDITOR_A, rkey="r1", state="accepted", rating=1, created_at_us=10))
    r.apply(_j(did=EDITOR_B, rkey="r2", state="rejected", rating=2, created_at_us=20))
    assert r.get(TARGET_URI) == ("rejected", 2)


def test_did_lex_tiebreak_on_equal_created_at():
    r = _resolver()
    # Same createdAt; lexicographically larger DID wins (matches sort
    # key in resolver — reverse=True, so larger DID is "first").
    r.apply(_j(did=EDITOR_A, rkey="r1", state="accepted", created_at_us=10))
    r.apply(_j(did=EDITOR_B, rkey="r2", state="rejected", created_at_us=10))
    assert r.get(TARGET_URI)[0] == "rejected"


def test_delete_falls_back_to_next():
    r = _resolver()
    r.apply(_j(did=EDITOR_A, rkey="r1", state="accepted", rating=1, created_at_us=10))
    r.apply(_j(did=EDITOR_B, rkey="r2", state="rejected", rating=2, created_at_us=20))
    affected = r.remove(did=EDITOR_B, rkey="r2")
    assert affected == TARGET_URI
    assert r.get(TARGET_URI) == ("accepted", 1)


def test_delete_unknown_is_noop():
    r = _resolver()
    assert r.remove(did=EDITOR_A, rkey="missing") is None


def test_non_allowlisted_did_ignored():
    r = CurationResolver(editor_dids={EDITOR_A}, admin_dids=set())
    r.apply(_j(did=EDITOR_B, rkey="r1", state="rejected", rating=2, created_at_us=10))
    assert r.get(TARGET_URI) == ("proposed", 0)


def test_self_accept_dropped_rating_kept():
    r = _resolver()
    r.apply(_j(did=AUTHOR, rkey="r1", state="accepted", rating=2, created_at_us=10))
    state, rating = r.get(TARGET_URI)
    assert state == "proposed"  # self-accept is filtered
    assert rating == 2          # rating is kept


def test_self_reject_allowed():
    """Authors may set their own record to ``rejected`` (retraction)."""
    r = _resolver()
    r.apply(_j(did=AUTHOR, rkey="r1", state="rejected", rating=0, created_at_us=10))
    assert r.get(TARGET_URI) == ("rejected", 0)


def test_self_withdraw_to_proposed_allowed():
    """Authors may move their own record back to ``proposed``."""
    r = _resolver()
    r.apply(_j(did=AUTHOR, rkey="r1", state="rejected", created_at_us=10))
    r.apply(_j(did=AUTHOR, rkey="r2", state="proposed", created_at_us=20))
    assert r.get(TARGET_URI)[0] == "proposed"


def test_self_superseded_dropped():
    """``superseded`` is not in the self-allowed set."""
    r = _resolver()
    r.apply(_j(did=AUTHOR, rkey="r1", state="superseded", created_at_us=10))
    assert r.get(TARGET_URI)[0] == "proposed"


def test_admin_can_self_curate():
    r = _resolver(admin=True)
    admin_target_uri = f"at://{ADMIN}/org.bunkankun.annotation.note/x"
    r.apply(_j(
        did=ADMIN, rkey="r1", state="accepted", rating=2,
        created_at_us=10, target_uri=admin_target_uri,
    ))
    assert r.get(admin_target_uri) == ("accepted", 2)


def test_self_filtered_state_does_not_override_editor_state():
    r = _resolver()
    # Editor A accepts at T=10.
    r.apply(_j(did=EDITOR_A, rkey="rA", state="accepted", rating=0, created_at_us=10))
    # Author tries to self-accept later (state stripped, rating kept).
    r.apply(_j(did=AUTHOR, rkey="rAuth", state="accepted", rating=2, created_at_us=20))
    state, rating = r.get(TARGET_URI)
    assert state == "accepted"   # editor A's state stands
    assert rating == 2           # author's rating wins (latest)


def test_self_reject_overrides_earlier_editor_accept():
    """An author retracting wins over an earlier editor acceptance."""
    r = _resolver()
    r.apply(_j(did=EDITOR_A, rkey="rA", state="accepted", created_at_us=10))
    r.apply(_j(did=AUTHOR, rkey="rAuth", state="rejected", created_at_us=20))
    assert r.get(TARGET_URI)[0] == "rejected"


def test_apply_returns_change_flag():
    r = _resolver()
    assert r.apply(_j(did=EDITOR_A, rkey="r1", state="accepted", created_at_us=10)) is True
    # Re-apply same provenance with same content: no change.
    assert r.apply(_j(did=EDITOR_A, rkey="r1", state="accepted", created_at_us=10)) is False
    # Same provenance with different state: change.
    assert r.apply(_j(did=EDITOR_A, rkey="r1", state="rejected", created_at_us=10)) is True


def test_known_targets_lists_observed_uris():
    r = _resolver()
    r.apply(_j(did=EDITOR_A, rkey="r1", created_at_us=10))
    other = f"at://{AUTHOR}/org.bunkankun.comment.post/zzz"
    r.apply(_j(did=EDITOR_A, rkey="r2", target_uri=other, created_at_us=10))
    assert set(r.known_targets()) == {TARGET_URI, other}
