from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from bkk.importer.read.word_relation import read_word_relations
from bkk.importer.write.word_relation import (
    discover_stale_word_backrefs,
    patch_word_backrefs,
    word_relation_note_path,
    write_word_relation,
)
from bkk.serialize.yaml_io import dump_record, load_record


_FIXTURE = dedent('''\
    <?xml version="1.0" encoding="UTF-8"?>
    <TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="word-relations">
      <text>
        <body>
          <div type="word-rel-type" xml:id="uuid-aaaaaaaa-0000-0000-0000-000000000001">
            <head>Conv</head>
            <div type="word-rels">
              <p>converse (與 - 受)</p>
              <div type="word-rel">
                <link target="#uuid-bbbbbbbb-0000-0000-0000-000000000001 #uuid-cccccccc-0000-0000-0000-000000000001"/>
                <div xml:id="uuid-11111111-0000-0000-0000-000000000001" type="word-rel-ref">
                  <list>
                    <item p="left-word" corresp="#uuid-bbbbbbbb-0000-0000-0000-000000000001" concept="BEG" concept-id="uuid-dddddddd-0000-0000-0000-000000000001">乞</item>
                    <item p="right-word" corresp="#uuid-cccccccc-0000-0000-0000-000000000001" concept="GIVE" concept-id="uuid-eeeeeeee-0000-0000-0000-000000000001">與</item>
                  </list>
                </div>
                <div type="source-references">
                  <listBibl>
                    <bibl>
                      <ref target="#uuid-ffffffff-0000-0000-0000-000000000001">MAO YUANMING 1999</ref>
                      <title>左傳詞彙研究</title>
                      <biblScope unit="page">298</biblScope>
                    </bibl>
                  </listBibl>
                </div>
              </div>
              <div type="word-rel">
                <div type="word-rel-ref" xml:id="uuid-22222222-0000-0000-0000-000000000001">
                  <list>
                    <item p="left-word" txt="孟子" lineref="uuid-99999999-0000-0000-0000-000000000001" offset="6" range="1" corresp="#uuid-bbbbbbbb-0000-0000-0000-000000000001" concept="SERVE" concept-id="uuid-dddddddd-0000-0000-0000-000000000002" line-id="KR1h0001_tls_001-64a.16" textline="必使仰足以事父母">事</item>
                    <item p="right-word" txt="孟子" lineref="uuid-99999999-0000-0000-0000-000000000002" offset="4" range="1" corresp="#uuid-cccccccc-0000-0000-0000-000000000002" concept="REAR" concept-id="uuid-dddddddd-0000-0000-0000-000000000003" line-id="KR1h0001_tls_001-64a.17" textline="俯足以畜妻子">畜</item>
                  </list>
                </div>
                <div type="word-rel-ref" xml:id="uuid-22222222-0000-0000-0000-000000000002">
                  <list>
                    <item p="left-word" txt="禮記" lineref="uuid-99999999-0000-0000-0000-000000000003" offset="5" range="1" corresp="#uuid-bbbbbbbb-0000-0000-0000-000000000001" concept="LOVE" concept-id="uuid-dddddddd-0000-0000-0000-000000000004" line-id="X.1" textline="A">慈</item>
                    <item p="right-word" txt="禮記" lineref="uuid-99999999-0000-0000-0000-000000000004" offset="2" range="1" corresp="#uuid-cccccccc-0000-0000-0000-000000000003" concept="LOVE" concept-id="uuid-dddddddd-0000-0000-0000-000000000005" line-id="X.2" textline="B">孝</item>
                  </list>
                </div>
              </div>
            </div>
          </div>
        </body>
      </text>
    </TEI>
''')


def _write_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "word-relations.xml"
    path.write_text(_FIXTURE, encoding="utf-8")
    return path


def test_reader_parses_three_refs_with_inherited_metadata(tmp_path: Path):
    records = read_word_relations(_write_fixture(tmp_path))

    assert [r.uuid for r in records] == [
        "11111111-0000-0000-0000-000000000001",
        "22222222-0000-0000-0000-000000000001",
        "22222222-0000-0000-0000-000000000002",
    ]
    abstract, attested_a, attested_b = records
    assert abstract.rel_type == "Conv"
    assert abstract.rel_type_uuid == "aaaaaaaa-0000-0000-0000-000000000001"
    assert abstract.rel_label == "converse (與 - 受)"
    assert abstract.left.text == "乞"
    assert abstract.left.word_uuid == "bbbbbbbb-0000-0000-0000-000000000001"
    assert abstract.left.concept == "BEG"
    assert abstract.left.attestation is None
    assert abstract.right.word_uuid == "cccccccc-0000-0000-0000-000000000001"
    assert len(abstract.source_references) == 1
    src = abstract.source_references[0]
    assert src.bibliography_uuid == "ffffffff-0000-0000-0000-000000000001"
    assert src.title == "左傳詞彙研究"
    assert src.scope == "298"
    assert src.scope_unit == "page"

    # Attestation fields preserved verbatim
    att = attested_a.left.attestation
    assert att.text_title == "孟子"
    assert att.line_uuid == "99999999-0000-0000-0000-000000000001"
    assert att.line_id == "KR1h0001_tls_001-64a.16"
    assert att.textline == "必使仰足以事父母"
    assert att.offset == 6
    assert att.range == 1

    # Sibling refs share a group_uuid (parent has no xml:id → derived)
    assert attested_a.group_uuid == attested_b.group_uuid
    assert attested_a.group_uuid != abstract.group_uuid


def test_writer_emits_expected_yaml_shape(tmp_path: Path):
    out_root = tmp_path / "out"
    records = read_word_relations(_write_fixture(tmp_path))
    for record in records:
        write_word_relation(record, out_root)

    abstract_path = word_relation_note_path(
        out_root, "11111111-0000-0000-0000-000000000001"
    )
    assert abstract_path.exists()
    data = load_record(abstract_path)
    assert data["type"] == "word-relation"
    assert data["rel_type"] == "Conv"
    assert data["rel_label"] == "converse (與 - 受)"
    assert data["left"]["text"] == "乞"
    assert data["left"]["word_uuid"] == "bbbbbbbb-0000-0000-0000-000000000001"
    assert data["right"]["concept"] == "GIVE"
    assert "attestation" not in data["left"]
    assert data["source_references"][0]["scope"] == "298"

    attested_path = word_relation_note_path(
        out_root, "22222222-0000-0000-0000-000000000001"
    )
    att_data = load_record(attested_path)
    assert att_data["left"]["attestation"]["line_id"] == "KR1h0001_tls_001-64a.16"
    assert att_data["left"]["attestation"]["offset"] == 6


def test_patch_word_backrefs_replaces_field(tmp_path: Path):
    out_root = tmp_path / "out"
    word_uuid = "bbbbbbbb-0000-0000-0000-000000000001"
    word_path = out_root / "words" / word_uuid[0] / f"{word_uuid}.yml"
    dump_record(word_path, {
        "uuid": word_uuid,
        "type": "word",
        "word_relations": ["deadbeef-stale-uuid-leftover-from-prior-run"],
    })

    patched = patch_word_backrefs(out_root, {
        word_uuid: [
            "11111111-0000-0000-0000-000000000001",
            "22222222-0000-0000-0000-000000000001",
            "11111111-0000-0000-0000-000000000001",   # duplicate, must dedupe
        ],
    })
    assert patched == 1
    data = load_record(word_path)
    assert data["word_relations"] == sorted({
        "11111111-0000-0000-0000-000000000001",
        "22222222-0000-0000-0000-000000000001",
    })

    # Clearing the list with an empty value drops the key entirely.
    patched = patch_word_backrefs(out_root, {word_uuid: []})
    assert patched == 1
    data = load_record(word_path)
    assert "word_relations" not in data


def test_discover_stale_word_backrefs_finds_orphans(tmp_path: Path):
    out_root = tmp_path / "out"
    fresh_uuid = "bbbbbbbb-0000-0000-0000-000000000001"
    stale_uuid = "cccccccc-0000-0000-0000-000000000999"
    dump_record(
        out_root / "words" / fresh_uuid[0] / f"{fresh_uuid}.yml",
        {"uuid": fresh_uuid, "type": "word",
         "word_relations": ["some-rel"]},
    )
    dump_record(
        out_root / "words" / stale_uuid[0] / f"{stale_uuid}.yml",
        {"uuid": stale_uuid, "type": "word",
         "word_relations": ["some-orphan-rel"]},
    )
    dump_record(
        out_root / "words" / "0" / "00000000-0000-0000-0000-000000000000.yml",
        {"uuid": "00000000-0000-0000-0000-000000000000", "type": "word"},
    )

    stale = discover_stale_word_backrefs(out_root, {fresh_uuid})
    assert stale == [stale_uuid]
