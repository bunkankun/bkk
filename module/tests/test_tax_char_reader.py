from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from bkk.importer.read.tax_char import read_tax_chars


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "taxchar.xml"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<div xmlns="http://www.tei-c.org/ns/1.0">\n'
        + body
        + '\n</div>\n',
        encoding="utf-8",
    )
    return path


def test_reader_parses_basic_record_with_nested_senses(tmp_path: Path):
    path = _write(tmp_path, dedent('''\
        <div type="taxchar" xml:id="uuid-61933a9c-6db2-48e7-aa14-2e7f2a7da546" resp="CH" modified="2024-01-16T00:29:44.701+09:00">
          <head>之</head>
          <list>
            <item type="pron">zhī (OC: kljɯ MC: tɕɨ) 止而切 平 廣韻：【適也往也 】<list>
              <item>physical movement&gt;  <ref target="#uuid-6b4e349a-a643-4404-97da-0687e3043fe3">GO TO</ref>
                <list>
                  <item>grammaticalised, adnominal&gt;  <ref target="#uuid-6106f200-6c09-483d-b636-68f140442ca0">THIS</ref></item>
                </list>
              </item>
            </list></item>
            <item>grammaticalised&gt;  <ref target="#uuid-976a96df-788e-40c3-9630-a579a83b7689">PARTICLE</ref></item>
          </list>
        </div>'''))

    records = read_tax_chars(path)
    assert len(records) == 1
    rec = records[0]
    assert rec.uuid == "61933a9c-6db2-48e7-aa14-2e7f2a7da546"
    assert rec.heads == ["之"]
    assert rec.metadata == {
        "source_file": "taxchar.xml",
        "resp": "CH",
        "date": "2024-01-16T00:29:44.701+09:00",
    }

    assert len(rec.pronunciations) == 1
    pron = rec.pronunciations[0]
    assert pron.reading == "zhī"
    assert pron.old_chinese == "kljɯ"
    assert pron.middle_chinese == "tɕɨ"
    assert pron.fanqie == "止而切"
    assert pron.tone == "平"
    assert pron.guangyun == "適也往也"
    assert pron.raw is None
    assert len(pron.senses) == 1
    top = pron.senses[0]
    assert top.gloss == "physical movement"
    assert top.concept_uuid == "6b4e349a-a643-4404-97da-0687e3043fe3"
    assert top.concept_label == "GO TO"
    assert len(top.children) == 1
    assert top.children[0].gloss == "grammaticalised, adnominal"
    assert top.children[0].concept_uuid == "6106f200-6c09-483d-b636-68f140442ca0"

    assert len(rec.unattributed_senses) == 1
    assert rec.unattributed_senses[0].gloss == "grammaticalised"
    assert rec.unattributed_senses[0].concept_uuid == "976a96df-788e-40c3-9630-a579a83b7689"


def test_reader_keeps_multiple_heads_for_variants(tmp_path: Path):
    path = _write(tmp_path, dedent('''\
        <div type="taxchar" xml:id="uuid-44791268-7a93-45b9-b04b-44c1aeeeed7b" resp="CH" modified="2022-12-20T03:53:23.937+09:00">
          <head>為</head>
          <head>爲</head>
          <list/>
        </div>'''))
    rec = read_tax_chars(path)[0]
    assert rec.heads == ["為", "爲"]
    assert rec.pronunciations == []
    assert rec.unattributed_senses == []


def test_reader_handles_placeholder_target_and_missing_pron(tmp_path: Path):
    path = _write(tmp_path, dedent('''\
        <div type="taxchar" xml:id="uuid-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee">
          <head>不</head>
          <list>
            <item> <ref target="#">不 pi1</ref>
              <list>
                <item>=丕  <ref target="#uuid-4f5a4eb2-575b-4ec1-a41d-5977f43709e6">BIG</ref></item>
              </list>
            </item>
          </list>
        </div>'''))
    rec = read_tax_chars(path)[0]
    # No `<item type="pron">` → all top-level senses land in unattributed.
    assert rec.pronunciations == []
    assert len(rec.unattributed_senses) == 1
    parent = rec.unattributed_senses[0]
    # Placeholder target → concept_uuid dropped, label kept.
    assert parent.concept_uuid is None
    assert parent.concept_label == "不 pi1"
    assert parent.children[0].gloss == "=丕"
    assert parent.children[0].concept_uuid == "4f5a4eb2-575b-4ec1-a41d-5977f43709e6"
    assert parent.children[0].concept_label == "BIG"


def test_reader_falls_back_to_raw_on_unparseable_pron(tmp_path: Path):
    path = _write(tmp_path, dedent('''\
        <div type="taxchar" xml:id="uuid-deadbeef-dead-beef-dead-beefdeadbeef">
          <head>X</head>
          <list>
            <item type="pron">funky pronunciation line without the expected shape<list>
              <item>only sense  <ref target="#uuid-11111111-1111-1111-1111-111111111111">FOO</ref></item>
            </list></item>
          </list>
        </div>'''))
    pron = read_tax_chars(path)[0].pronunciations[0]
    assert pron.reading is None
    assert pron.raw is not None
    assert "funky pronunciation" in pron.raw
    assert pron.senses[0].concept_label == "FOO"
