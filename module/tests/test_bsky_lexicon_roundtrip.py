"""Round-trip tests for BKK Bluesky lexicons.

Verifies the "two-place rule": for every record kind, an archive shape
converted to wire and back must equal the original on all stable fields.
Also verifies the legacy flat NSID still flows through the annotation
converter unchanged.
"""

from __future__ import annotations

from bkk.annotations.harvest import (
    annotation_wire_to_archive,
    comment_wire_to_archive,
    translation_wire_to_archive,
)
from bkk.serve.atproto import (
    ANNOTATION_NSID,
    COMMENT_NSID,
    LEGACY_ANNOTATION_NSID,
    TRANSLATION_NSID,
)
from bkk.serve.routers.annotations_write import (
    AnchorIn,
    AnnotationPostRequest,
    CommentPostRequest,
    StrongRefIn,
    TranslationPostRequest,
    _annotation_archive_to_wire,
    _comment_archive_to_wire,
    _translation_archive_to_wire,
)


DID = "did:plc:test-author"
PARENT_URI = "at://did:plc:other/org.bunkankun.annotation.note/abc"
PARENT_CID = "bafyparent"


def _strip_provenance(record: dict) -> dict:
    """Drop the harvest-only ``provenance`` block before equality checks."""
    out = dict(record)
    out.pop("provenance", None)
    out.pop("curation_state", None)
    out.pop("id", None)
    return out


def test_annotation_round_trip_preserves_anchor_and_payload():
    req = AnnotationPostRequest(
        text_id="KR1h0004",
        edition="tls",
        anchor=AnchorIn(
            marker_id="KR1h0004_tls_003-1a.5", offset=2, length=4,
        ),
        payload={"note": "hello", "concept": "concept://foo"},
        supersedes="bafyprior",
    )
    wire = _annotation_archive_to_wire(req)

    assert wire["$type"] == ANNOTATION_NSID
    assert wire["sourceRole"] == f"bsky:{ANNOTATION_NSID}"
    assert wire["supersedes"] == "bafyprior"
    assert wire["anchor"] == {
        "markerId": "KR1h0004_tls_003-1a.5", "offset": 2, "length": 4,
    }

    archive = annotation_wire_to_archive(wire, did=DID, cid="bafy1")
    assert archive is not None
    assert archive["text_id"] == "KR1h0004"
    assert archive["edition"] == "tls"
    assert archive["anchor"] == {
        "marker_id": "KR1h0004_tls_003-1a.5", "offset": 2, "length": 4,
    }
    assert archive["payload"] == {"note": "hello", "concept": "concept://foo"}
    assert archive["provenance"]["did"] == DID
    assert archive["provenance"]["cid"] == "bafy1"
    assert archive["provenance"]["supersedes"] == "bafyprior"
    assert archive["provenance"]["source_role"] == f"bsky:{ANNOTATION_NSID}"


def test_annotation_cross_marker_anchor_round_trips():
    req = AnnotationPostRequest(
        text_id="KR1h0004", edition="tls",
        anchor=AnchorIn(
            marker_id="KR1h0004_tls_003-1a.5", offset=0, length=12,
            end_marker_id="KR1h0004_tls_003-1a.6", end_length=4,
        ),
        payload={},
    )
    wire = _annotation_archive_to_wire(req)
    assert wire["anchor"]["endMarkerId"] == "KR1h0004_tls_003-1a.6"
    assert wire["anchor"]["endLength"] == 4
    archive = annotation_wire_to_archive(wire, did=DID, cid="bafy2")
    assert archive is not None
    assert archive["anchor"]["end_marker_id"] == "KR1h0004_tls_003-1a.6"
    assert archive["anchor"]["end_length"] == 4


def test_legacy_flat_nsid_records_decode_via_annotation_converter():
    """Records posted under the old flat NSID share the wire shape; only
    ``$type`` and the harvested ``source_role`` differ."""
    legacy_wire = {
        "$type": LEGACY_ANNOTATION_NSID,
        "textId": "KR1h0004",
        "edition": "tls",
        "anchor": {
            "markerId": "KR1h0004_tls_003-1a.5", "offset": 0, "length": 1,
        },
        "payload": {"note": "from before the rename"},
        "createdAt": "2024-01-02T03:04:05.000000Z",
    }
    # Per the harvester, legacy records are tagged with the *new* NSID's
    # source_role so the archive stays uniform.
    archive = annotation_wire_to_archive(legacy_wire, did=DID, cid="bafyleg")
    assert archive is not None
    assert archive["payload"] == {"note": "from before the rename"}
    assert archive["provenance"]["source_role"] == f"bsky:{ANNOTATION_NSID}"


def test_comment_with_anchor_round_trips():
    req = CommentPostRequest(
        text_id="KR1h0004",
        edition="tls",
        anchor=AnchorIn(
            marker_id="KR1h0004_tls_003-1a.5", offset=1, length=3,
        ),
        body="**bold** comment",
        lang="en",
    )
    wire = _comment_archive_to_wire(req)
    assert wire["$type"] == COMMENT_NSID
    assert wire["format"] == "markdown"
    assert "parent" not in wire

    archive = comment_wire_to_archive(wire, did=DID, cid="bafyc1")
    assert archive is not None
    assert archive["body"] == "**bold** comment"
    assert archive["lang"] == "en"
    assert archive["edition"] == "tls"
    assert archive["anchor"]["marker_id"] == "KR1h0004_tls_003-1a.5"
    assert "parent" not in archive


def test_comment_reply_round_trips_without_anchor():
    req = CommentPostRequest(
        text_id="KR1h0004",
        parent=StrongRefIn(uri=PARENT_URI, cid=PARENT_CID),
        root=StrongRefIn(uri=PARENT_URI, cid=PARENT_CID),
        body="agreed",
        lang="ja",
    )
    wire = _comment_archive_to_wire(req)
    assert "anchor" not in wire
    assert wire["parent"] == {"uri": PARENT_URI, "cid": PARENT_CID}
    assert wire["root"] == {"uri": PARENT_URI, "cid": PARENT_CID}

    archive = comment_wire_to_archive(wire, did=DID, cid="bafyc2")
    assert archive is not None
    assert archive["body"] == "agreed"
    assert archive["lang"] == "ja"
    assert "anchor" not in archive
    assert archive["parent"]["uri"] == PARENT_URI
    assert archive["root"]["uri"] == PARENT_URI


def test_comment_request_rejects_both_anchor_and_parent():
    import pytest

    with pytest.raises(ValueError):
        CommentPostRequest(
            text_id="KR1h0004", edition="tls",
            anchor=AnchorIn(
                marker_id="KR1h0004_tls_003-1a.5", offset=0, length=1,
            ),
            parent=StrongRefIn(uri=PARENT_URI, cid=PARENT_CID),
            body="ambiguous",
        )


def test_comment_request_rejects_neither_anchor_nor_parent():
    import pytest

    with pytest.raises(ValueError):
        CommentPostRequest(text_id="KR1h0004", body="no target", lang="en")


def test_comment_wire_with_both_anchor_and_parent_is_rejected():
    """Even if someone hand-builds an invalid wire record, the harvester
    enforces the xor and returns None."""
    bad_wire = {
        "$type": COMMENT_NSID,
        "textId": "KR1h0004",
        "edition": "tls",
        "anchor": {
            "markerId": "KR1h0004_tls_003-1a.5", "offset": 0, "length": 1,
        },
        "parent": {"uri": PARENT_URI, "cid": PARENT_CID},
        "body": "x",
        "lang": "en",
        "format": "markdown",
        "createdAt": "2024-01-02T03:04:05.000000Z",
    }
    assert comment_wire_to_archive(bad_wire, did=DID, cid="bafybad") is None


def test_translation_round_trips():
    req = TranslationPostRequest(
        text_id="KR1h0004",
        edition="tls",
        anchor=AnchorIn(
            marker_id="KR1h0004_tls_003-1a.5", offset=0, length=12,
        ),
        translation_id="bkk-tr-KR1h0004-smith-en",
        text="The Master said: ...",
        lang="en",
        title="Opening line",
        note="literal rendering",
    )
    wire = _translation_archive_to_wire(req)
    assert wire["$type"] == TRANSLATION_NSID
    assert wire["translationId"] == "bkk-tr-KR1h0004-smith-en"
    assert wire["text"] == "The Master said: ..."

    archive = translation_wire_to_archive(wire, did=DID, cid="bafyt1")
    assert archive is not None
    assert archive["translation_id"] == "bkk-tr-KR1h0004-smith-en"
    assert archive["text"] == "The Master said: ..."
    assert archive["title"] == "Opening line"
    assert archive["note"] == "literal rendering"
    assert archive["lang"] == "en"
    assert archive["format"] == "markdown"
    assert archive["anchor"]["marker_id"] == "KR1h0004_tls_003-1a.5"


def test_translation_optional_fields_omitted_when_absent():
    req = TranslationPostRequest(
        text_id="KR1h0004",
        edition="tls",
        anchor=AnchorIn(
            marker_id="KR1h0004_tls_003-1a.5", offset=0, length=1,
        ),
        translation_id="bkk-tr-KR1h0004-smith-en",
        text="x",
        lang="en",
    )
    wire = _translation_archive_to_wire(req)
    assert "title" not in wire
    assert "note" not in wire
    archive = translation_wire_to_archive(wire, did=DID, cid="bafyt2")
    assert archive is not None
    assert "title" not in archive
    assert "note" not in archive


def test_required_fields_missing_returns_none():
    bad = {"$type": COMMENT_NSID, "textId": "KR1h0004", "body": "x"}
    assert comment_wire_to_archive(bad, did=DID, cid="b") is None

    bad_ann = {"$type": ANNOTATION_NSID, "textId": "KR1h0004", "edition": "tls"}
    assert annotation_wire_to_archive(bad_ann, did=DID, cid="b") is None

    bad_tr = {"$type": TRANSLATION_NSID, "textId": "KR1h0004"}
    assert translation_wire_to_archive(bad_tr, did=DID, cid="b") is None


def test_archive_to_wire_strips_metadata_from_round_trip():
    """The post side adds wire-only fields (createdAt, $type, sourceRole) but
    the underlying text/edition/anchor/payload survive in both directions."""
    req = AnnotationPostRequest(
        text_id="KR1h0004", edition="tls",
        anchor=AnchorIn(marker_id="KR1h0004_tls_003-1a.5", offset=0, length=1),
        payload={"note": "hi"},
    )
    wire = _annotation_archive_to_wire(req)
    archive = annotation_wire_to_archive(wire, did=DID, cid="bafy")
    assert archive is not None
    stripped = _strip_provenance(archive)
    assert stripped["text_id"] == req.text_id
    assert stripped["edition"] == req.edition
    assert stripped["payload"] == req.payload
    assert stripped["anchor"]["marker_id"] == req.anchor.marker_id
