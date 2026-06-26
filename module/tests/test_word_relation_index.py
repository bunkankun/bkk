"""Indexer coverage for the word-relation collection."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from bkk.index.core import build_core_index
from bkk.serialize.yaml_io import dump_record


WORD_LEFT = "bbbbbbbb-0000-0000-0000-000000000001"
WORD_RIGHT = "cccccccc-0000-0000-0000-000000000001"
CONCEPT_LEFT = "dddddddd-0000-0000-0000-000000000001"
CONCEPT_RIGHT = "eeeeeeee-0000-0000-0000-000000000001"
BIB_UUID = "ffffffff-0000-0000-0000-000000000001"
REL_UUID = "11111111-0000-0000-0000-000000000001"


def _build_fixture(root: Path) -> None:
    dump_record(
        root / "concepts" / CONCEPT_LEFT[0] / f"{CONCEPT_LEFT}.yml",
        {"uuid": CONCEPT_LEFT, "type": "concept", "concept": "BEG"},
    )
    dump_record(
        root / "concepts" / CONCEPT_RIGHT[0] / f"{CONCEPT_RIGHT}.yml",
        {"uuid": CONCEPT_RIGHT, "type": "concept", "concept": "GIVE"},
    )
    dump_record(
        root / "words" / WORD_LEFT[0] / f"{WORD_LEFT}.yml",
        {
            "uuid": WORD_LEFT, "type": "word",
            "form": {"orth": "乞"},
            "concept_uuid": CONCEPT_LEFT,
            "word_relations": [REL_UUID],
        },
    )
    dump_record(
        root / "words" / WORD_RIGHT[0] / f"{WORD_RIGHT}.yml",
        {
            "uuid": WORD_RIGHT, "type": "word",
            "form": {"orth": "與"},
            "concept_uuid": CONCEPT_RIGHT,
            "word_relations": [REL_UUID],
        },
    )
    dump_record(
        root / "bibliography" / BIB_UUID[0] / f"{BIB_UUID}.yml",
        {"uuid": BIB_UUID, "type": "bibliography",
         "citation_label": "MAO YUANMING 1999"},
    )
    dump_record(
        root / "word-relations" / REL_UUID[0] / f"{REL_UUID}.yml",
        {
            "uuid": REL_UUID,
            "type": "word-relation",
            "group_uuid": "99999999-0000-0000-0000-000000000001",
            "rel_type": "Conv",
            "rel_type_uuid": "aaaaaaaa-0000-0000-0000-000000000001",
            "rel_label": "converse (與 - 受)",
            "left": {
                "word_uuid": WORD_LEFT,
                "text": "乞",
                "concept": "BEG",
                "concept_uuid": CONCEPT_LEFT,
            },
            "right": {
                "word_uuid": WORD_RIGHT,
                "text": "與",
                "concept": "GIVE",
                "concept_uuid": CONCEPT_RIGHT,
            },
            "source_references": [
                {"bibliography_uuid": BIB_UUID,
                 "title": "left", "scope": "298", "scope_unit": "page"},
            ],
        },
    )


def test_index_emits_word_relation_rows(tmp_path: Path):
    root = tmp_path / "core"
    _build_fixture(root)
    db = build_core_index(root, root / "_core.bkki")
    conn = sqlite3.connect(str(db))
    try:
        note = conn.execute(
            "SELECT type, collection, display_label FROM notes WHERE uuid = ?",
            (REL_UUID,),
        ).fetchone()
        assert note is not None
        assert note[0] == "word-relation"
        assert note[1] == "word-relations"
        # Composite display label resolves via word_display cache.
        assert "Conv" in note[2]
        assert "乞" in note[2]
        assert "與" in note[2]
        assert "↔" in note[2]

        labels = {
            (label, label_type)
            for label, label_type in conn.execute(
                "SELECT label, label_type FROM labels WHERE uuid = ?",
                (REL_UUID,),
            ).fetchall()
        }
        assert ("Conv", "rel_type") in labels
        assert ("converse (與 - 受)", "rel_label") in labels
        assert ("乞", "left_text") in labels
        assert ("與", "right_text") in labels

        out_links = {
            (target_uuid, target_type, relation)
            for target_uuid, target_type, relation in conn.execute(
                "SELECT target_uuid, target_type, relation FROM links "
                "WHERE source_uuid = ?",
                (REL_UUID,),
            ).fetchall()
        }
        assert (WORD_LEFT, "word", "left_word") in out_links
        assert (WORD_RIGHT, "word", "right_word") in out_links
        assert (CONCEPT_LEFT, "concept", "left_concept") in out_links
        assert (CONCEPT_RIGHT, "concept", "right_concept") in out_links
        assert (BIB_UUID, "bibliography", "source_reference") in out_links

        # Word → word-relation back-link is recorded as well.
        back = conn.execute(
            "SELECT relation FROM links "
            "WHERE source_uuid = ? AND target_uuid = ?",
            (WORD_LEFT, REL_UUID),
        ).fetchone()
        assert back is not None and back[0] == "word_relation"
    finally:
        conn.close()
