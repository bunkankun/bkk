from bkk.importer.canonicalize import merge_sections, nfc
from bkk.importer.hashing import sha256_text
from bkk.importer.ir import Marker, Section


def test_nfc_idempotent():
    s = "臨濟錄"
    assert nfc(s) == s
    # Compatibility decomposable composition stays composed.
    composed = "\u00e9"  # é precomposed
    decomposed = "e\u0301"
    assert nfc(decomposed) == composed


def test_merge_empty():
    text, markers, offsets = merge_sections([])
    assert text == ""
    assert markers == []
    assert offsets == {}


def test_merge_shifts_offsets():
    s1 = Section(
        head_text="A",
        head_marker_id="h-1",
        text="abc",
        markers=[
            Marker("tls:head", 0, "", "h-1"),
            Marker("tls:seg", 0, "", "s-1"),
        ],
    )
    s2 = Section(
        head_text="B",
        head_marker_id="h-2",
        text="xy",
        markers=[
            Marker("tls:head", 0, "", "h-2"),
            Marker("punctuation", 1, "，", ""),
        ],
    )
    text, markers, offsets = merge_sections([s1, s2])
    assert text == "abcxy"
    # First section's markers stay at 0; second section's shift by len(s1.text)=3.
    assert [(m.type, m.offset) for m in markers] == [
        ("tls:head", 0),
        ("tls:seg", 0),
        ("tls:head", 3),
        ("punctuation", 4),
    ]
    assert offsets == {"h-1": 0, "s-1": 0, "h-2": 3}


def test_text_hash_stable_for_known_string():
    # Hash of "abc" UTF-8 — verifies our hashing wiring against a known SHA-256.
    expected = "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256_text("abc") == expected
