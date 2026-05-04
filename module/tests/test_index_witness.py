"""Witness application + offset translation."""

from __future__ import annotations

from bkk.index.witness import apply_witness, witness_to_master_span


def test_single_char_replacement():
    master = "ABCDE"
    variants = [{"offset": 2, "length": 1, "content": "C", "X": "c"}]
    text, segs = apply_witness(master, variants, "X")
    assert text == "ABcDE"
    # Pure identity match
    assert witness_to_master_span(segs, 0, 2) == (0, 2)
    # Hit on the variant char alone -> master span [2,3)
    assert witness_to_master_span(segs, 2, 3) == (2, 1)
    # Hit crossing variant + identity -> spans both fully
    assert witness_to_master_span(segs, 1, 4) == (1, 3)


def test_skips_variants_for_other_witnesses():
    master = "ABCDE"
    variants = [
        {"offset": 1, "length": 1, "content": "B", "X": "b"},
        {"offset": 3, "length": 1, "content": "D", "Y": "d"},
    ]
    text, _ = apply_witness(master, variants, "X")
    assert text == "AbCDE"
    text, _ = apply_witness(master, variants, "Y")
    assert text == "ABCdE"


def test_insertion_and_deletion():
    master = "AB CD"
    variants = [
        {"offset": 2, "length": 0, "content": "", "X": "x"},   # insert "x" before space
        {"offset": 3, "length": 1, "content": "C", "X": ""},  # delete C
    ]
    text, segs = apply_witness(master, variants, "X")
    # AB | (insert x) | (space) | (delete C) | D
    assert text == "ABx D"
    # Hit on 'x' alone -> master span [2,2) (length 0)
    m_off, m_len = witness_to_master_span(segs, 2, 3)
    assert (m_off, m_len) == (2, 0)
    # Hit on 'D' (witness pos 4) -> master pos 4
    assert witness_to_master_span(segs, 4, 5) == (4, 1)


def test_multi_char_replacement_widens_master_span():
    master = "ABCDE"
    # Master "BCD" replaced by witness "Z"
    variants = [{"offset": 1, "length": 3, "content": "BCD", "X": "Z"}]
    text, segs = apply_witness(master, variants, "X")
    assert text == "AZE"
    # Witness 'Z' at pos 1 -> master span [1,4) (the full 3-char master span)
    assert witness_to_master_span(segs, 1, 2) == (1, 3)
    # Witness 'AZ' -> master 'ABCD' (identity 'A' + variant span)
    assert witness_to_master_span(segs, 0, 2) == (0, 4)


def test_consecutive_identity_runs():
    master = "ABCDE"
    variants = [
        {"offset": 1, "length": 1, "content": "B", "X": "b"},
        {"offset": 3, "length": 1, "content": "D", "X": "d"},
    ]
    text, segs = apply_witness(master, variants, "X")
    assert text == "AbCdE"
    # Identity-only hits collapse to exact master spans
    assert witness_to_master_span(segs, 0, 1) == (0, 1)
    assert witness_to_master_span(segs, 2, 3) == (2, 1)
    assert witness_to_master_span(segs, 4, 5) == (4, 1)
    # Mixed hit "bCd" spans both variants and the identity middle
    assert witness_to_master_span(segs, 1, 4) == (1, 3)
