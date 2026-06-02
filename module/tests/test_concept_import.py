"""End-to-end tests for ``bkk import concepts``."""

from __future__ import annotations

from pathlib import Path
import shutil

import yaml

from bkk.importer.cli import run
from bkk.importer.write.concept import relative_knowledge_link


REPO = Path(__file__).resolve().parents[1]
CONCEPT_INPUT = REPO / "input" / "core" / "concepts"
BIBLIOGRAPHY_INPUT = REPO / "input" / "core" / "bibliography"
GRAPH_INPUT = REPO / "input" / "core" / "graphs"
SYN_FUNC_INPUT = REPO / "input" / "core" / "syntactic-functions"
SEM_FEAT_INPUT = REPO / "input" / "core" / "semantic-features"
WORD_INPUT = REPO / "input" / "core" / "words"


ABLE_UUID = "3eb2c600-e234-4c6b-bb79-40e8eff9ee14"
WASTE_UUID = "cb68f425-4800-453d-aad6-4177b9106fb8"


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    assert len(parts) >= 3, f"missing YAML front matter in {path}"
    return yaml.safe_load(parts[1])


def test_import_concepts_positional_command_writes_sharded_notes(
    tmp_path: Path,
):
    out = tmp_path / "core"
    rc = run([
        "concepts",
        "--in", str(CONCEPT_INPUT),
        "--out", str(out),
        "--yes",
    ])
    assert rc == 0

    able = out / "concepts" / "3" / f"{ABLE_UUID}.md"
    waste = out / "concepts" / "c" / f"{WASTE_UUID}.md"
    assert able.is_file()
    assert waste.is_file()

    fm = _frontmatter(able)
    assert fm["uuid"] == ABLE_UUID
    assert fm["type"] == "concept"
    assert fm["concept"] == "ABLE"
    assert fm["labels"][0] == "CAPABLE OF"
    assert fm["zh"] == "能夠"
    assert fm["och"] == "能"

    body = able.read_text(encoding="utf-8")
    assert "# Concept: ABLE" in body
    assert "# Definition" in body
    assert "# Criteria and general notes" in body
    assert "néng [[能]]" in body
    assert "[UNABLE](../d/deb3cd81-03bc-4c7c-9125-a2a8837202c9.md)" in body
    assert "[HAVE](../f/fb02970d-7e8c-43ca-b0fd-fddc6055d130.md)" in body
    assert (
        "[BUCK 1988](../../bibliography/6/"
        "60d39cc0-d76b-4275-8490-886ace4204be.md)"
    ) in body
    assert "[[UNABLE]]" not in body


def test_import_concepts_format_command_and_empty_words_section(
    tmp_path: Path,
):
    out = tmp_path / "core"
    rc = run([
        "--format", "concepts",
        "--in", str(CONCEPT_INPUT),
        "--out", str(out),
        "--text-id", "WASTE",
        "--yes",
    ])
    assert rc == 0

    waste = out / "concepts" / "c" / f"{WASTE_UUID}.md"
    assert waste.is_file()
    body = waste.read_text(encoding="utf-8")
    assert body.rstrip().endswith("# Words")
    assert "[EXTRAVAGANT](../6/636d3620-76d1-47af-9231-37328a508b4a.md)" in body
    assert "hào [[耗]]" in body
    assert "liǎn [[斂]]" in body


def test_import_concepts_on_exists_skip_preserves_existing_note(
    tmp_path: Path,
):
    out = tmp_path / "core"
    assert run([
        "concepts",
        "--in", str(CONCEPT_INPUT),
        "--out", str(out),
        "--text-id", "ABLE",
        "--yes",
    ]) == 0

    able = out / "concepts" / "3" / f"{ABLE_UUID}.md"
    able.write_text("sentinel\n", encoding="utf-8")

    assert run([
        "concepts",
        "--in", str(CONCEPT_INPUT),
        "--out", str(out),
        "--text-id", "ABLE",
        "--on-exists", "skip",
        "--yes",
    ]) == 0
    assert able.read_text(encoding="utf-8") == "sentinel\n"


def test_relative_knowledge_link_policy():
    assert relative_knowledge_link(
        source_type="concepts",
        source_uuid="3eb2c600-e234-4c6b-bb79-40e8eff9ee14",
        target_type="concepts",
        target_uuid="3aaaaaaa-e234-4c6b-bb79-40e8eff9ee14",
    ) == "3aaaaaaa-e234-4c6b-bb79-40e8eff9ee14.md"
    assert relative_knowledge_link(
        source_type="concepts",
        source_uuid="3eb2c600-e234-4c6b-bb79-40e8eff9ee14",
        target_type="concepts",
        target_uuid="cb68f425-4800-453d-aad6-4177b9106fb8",
    ) == "../c/cb68f425-4800-453d-aad6-4177b9106fb8.md"
    assert relative_knowledge_link(
        source_type="concepts",
        source_uuid="3eb2c600-e234-4c6b-bb79-40e8eff9ee14",
        target_type="bibliography",
        target_uuid="60d39cc0-d76b-4275-8490-886ace4204be",
    ) == "../../bibliography/6/60d39cc0-d76b-4275-8490-886ace4204be.md"


def test_import_bibliography_writes_structured_sharded_notes(
    tmp_path: Path,
):
    out = tmp_path / "core"
    rc = run([
        "bibliography",
        "--in", str(BIBLIOGRAPHY_INPUT),
        "--out", str(out),
        "--yes",
    ])
    assert rc == 0

    buck_uuid = "60d39cc0-d76b-4275-8490-886ace4204be"
    comen_uuid = "6b8242ce-9fc6-4b7b-bd52-14c744308409"
    buck = out / "bibliography" / "6" / f"{buck_uuid}.md"
    comen = out / "bibliography" / "6" / f"{comen_uuid}.md"
    assert buck.is_file()
    assert comen.is_file()

    fm = _frontmatter(buck)
    assert fm["uuid"] == buck_uuid
    assert fm["type"] == "bibliography"
    assert fm["citation_label"] == "BUCK 1988"
    assert fm["ref_usage"] == "1008"
    assert fm["resource_type"] == "text"
    assert fm["genres"] == [{"value": "book", "authority": "marcgt"}]
    assert fm["titles"][0] == {
        "title": "A Dictionary of Selected Synonyms in the Principal Indo-European Languages",
        "lang": "eng",
        "script": "Latn",
    }
    assert fm["contributors"][0]["given"] == "Carl Darling"
    assert fm["contributors"][0]["family"] == "BUCK"
    assert fm["contributors"][0]["roles"] == ["author"]
    assert fm["origin"]["place"] == "Chicago"
    assert fm["origin"]["date_issued"] == "1988"
    assert fm["origin"]["date_encoding"] == "w3cdtf"
    assert fm["notes"] == [
        {"type": "general", "text": "Indispensable standard handbook."}
    ]
    assert fm["source"] == {"format": "MODS", "version": "3.6"}

    body = buck.read_text(encoding="utf-8")
    assert "# BUCK 1988" in body
    assert "## Contributors" in body
    assert "- Carl Darling BUCK, author" in body
    assert "Chicago: The University of Chicago Press, 1988." in body
    assert "Indispensable standard handbook." in body


def test_import_bibliography_format_command_preserves_subtitle(
    tmp_path: Path,
):
    out = tmp_path / "core"
    rc = run([
        "--format", "bibliography",
        "--in", str(BIBLIOGRAPHY_INPUT),
        "--out", str(out),
        "--text-id", "uuid-6b8242ce-9fc6-4b7b-bd52-14c744308409",
        "--yes",
    ])
    assert rc == 0

    comen_uuid = "6b8242ce-9fc6-4b7b-bd52-14c744308409"
    comen = out / "bibliography" / "6" / f"{comen_uuid}.md"
    assert comen.is_file()
    fm = _frontmatter(comen)
    assert fm["citation_label"] == "COMENIUS 1665"
    assert fm["titles"][0]["subtitle"] == "Lexicon Reale Pansophicum"
    assert fm["origin"]["edition"] == "reprint of 1665 edition"


def test_import_bibliography_discovers_sharded_input_tree(
    tmp_path: Path,
):
    nested_in = tmp_path / "in" / "bibliography" / "6"
    nested_in.mkdir(parents=True)
    for source in BIBLIOGRAPHY_INPUT.glob("*.xml"):
        shutil.copy2(source, nested_in / source.name)

    out = tmp_path / "core"
    rc = run([
        "bibliography",
        "--in", str(tmp_path / "in" / "bibliography"),
        "--out", str(out),
        "--text-id", "60d39cc0-d76b-4275-8490-886ace4204be",
        "--yes",
    ])
    assert rc == 0

    buck = (
        out / "bibliography" / "6"
        / "60d39cc0-d76b-4275-8490-886ace4204be.md"
    )
    assert buck.is_file()


def test_import_bibliography_preserves_chinese_and_pinyin_variants(
    tmp_path: Path,
):
    out = tmp_path / "core"
    rc = run([
        "bibliography",
        "--in", str(BIBLIOGRAPHY_INPUT),
        "--out", str(out),
        "--text-id", "0009ccda-306e-47bb-97e2-7da0c80b3302",
        "--yes",
    ])
    assert rc == 0

    uuid = "0009ccda-306e-47bb-97e2-7da0c80b3302"
    note = out / "bibliography" / "0" / f"{uuid}.md"
    assert note.is_file()

    fm = _frontmatter(note)
    assert fm["citation_label"] == "LU FENGPENG 1997"
    assert fm["genres"] == [{"value": "article", "authority": "marcgt"}]
    assert fm["titles"] == [
        {
            "title": "段玉裁的轉注論及其運用 段玉裁的轉注論及其運用",
            "script": "Hant",
        },
        {
            "title": "Duan Yucai de zhuan zhu lun ji qi lian yong",
            "type": "translated",
            "script": "Latn",
        },
    ]
    contributor = fm["contributors"][0]
    assert contributor["given"] == "Fengpeng"
    assert contributor["family"] == "Lu"
    assert contributor["script"] == "Latn"
    assert contributor["names"] == [
        {
            "script": "Latn",
            "transliteration": "chinese/ala-lc",
            "given": "Fengpeng",
            "family": "Lu",
        },
        {"script": "Hant", "given": "鳳鵬", "family": "盧"},
    ]

    body = note.read_text(encoding="utf-8").split("---", 2)[2]
    assert "**段玉裁的轉注論及其運用 段玉裁的轉注論及其運用**" in body
    assert "**Duan Yucai de zhuan zhu lun ji qi lian yong**" in body
    assert "- Fengpeng Lu / 盧鳳鵬, author" in body


def test_import_graphs_writes_structured_sharded_note(tmp_path: Path):
    out = tmp_path / "core"
    rc = run([
        "graphs",
        "--in", str(GRAPH_INPUT),
        "--out", str(out),
        "--yes",
    ])
    assert rc == 0

    uuid = "f35bd989-7850-4240-9751-87ca014d77b1"
    note = out / "graphs" / "f" / f"{uuid}.md"
    assert note.is_file()

    fm = _frontmatter(note)
    assert fm["uuid"] == uuid
    assert fm["type"] == "graph"
    assert fm["graphs"] == {
        "attested": "閑",
        "unemended": None,
        "emended": None,
        "standardised": None,
    }
    assert fm["gloss"] == "闌也防也禦也大也法也習也睱也戸間切九"
    assert fm["xiaoyun"] == {"headword": "閑", "graph_count": 9}
    assert fm["fanqie"] == {
        "shangzi": {"attested": "戶", "standard": None},
        "xiazi": {"attested": "閒", "standard": None},
    }
    assert fm["ids"] == {"guangyun_jiaoshi_id": "4981", "pan_wuyun_id": "5025"}
    assert fm["locations"]["guangyun_location"] == "129.15"
    assert fm["pronunciation"]["mandarin"]["jin"] == "xián"
    assert fm["pronunciation"]["middle_chinese"]["categories"]["聲"] == "匣"
    assert fm["pronunciation"]["old_chinese"]["pan_wuyun"]["oc"] == "ɢreen"

    body = note.read_text(encoding="utf-8").split("---", 2)[2]
    assert "# 閑" in body
    assert "## Fanqie\n戶閒" in body
    assert "## Mandarin\nxián" in body
    assert "闌也防也禦也" not in body
    assert "ɣɛn" not in body


def test_import_graphs_discovers_sharded_input_tree(tmp_path: Path):
    nested_in = tmp_path / "in" / "graphs" / "f"
    nested_in.mkdir(parents=True)
    for source in GRAPH_INPUT.glob("*.xml"):
        shutil.copy2(source, nested_in / source.name)

    out = tmp_path / "core"
    rc = run([
        "--format", "graphs",
        "--in", str(tmp_path / "in" / "graphs"),
        "--out", str(out),
        "--text-id", "f35bd989-7850-4240-9751-87ca014d77b1",
        "--yes",
    ])
    assert rc == 0

    note = (
        out / "graphs" / "f"
        / "f35bd989-7850-4240-9751-87ca014d77b1.md"
    )
    assert note.is_file()


def test_import_graphs_uses_standardized_display_when_attested_missing(
    tmp_path: Path,
):
    out = tmp_path / "core"
    rc = run([
        "--format", "graphs",
        "--in", str(GRAPH_INPUT),
        "--out", str(out),
        "--text-id", "ffade897-133a-4d1f-98e9-6226ccd434e5",
        "--yes",
    ])
    assert rc == 0

    uuid = "ffade897-133a-4d1f-98e9-6226ccd434e5"
    note = out / "graphs" / "f" / f"{uuid}.md"
    fm = _frontmatter(note)
    assert fm["graphs"]["attested"] is None
    assert fm["graphs"]["standardised"] == "舔"

    body = note.read_text(encoding="utf-8").split("---", 2)[2]
    assert "# 舔 (standardized)" in body
    assert "## Fanqie\n他玷" in body
    assert "## Mandarin\ntiǎn" in body


def test_import_syntactic_functions_extracts_divisions_from_single_xml(
    tmp_path: Path,
):
    out = tmp_path / "core"
    rc = run([
        "syntactic-functions",
        "--in", str(SYN_FUNC_INPUT),
        "--out", str(out),
        "--text-id", "e81e5db1-7207-4450-a18d-27a597c5fd67",
        "--yes",
    ])
    assert rc == 0

    uuid = "e81e5db1-7207-4450-a18d-27a597c5fd67"
    note = out / "syntactic-functions" / "e" / f"{uuid}.md"
    assert note.is_file()

    fm = _frontmatter(note)
    assert fm["uuid"] == uuid
    assert fm["type"] == "syntactic-function"
    assert fm["code"] == "npro.adNab"
    assert "descriptions" not in fm
    assert "notes" not in fm
    assert fm["relations"] == [
        {
            "type": "taxonymy",
            "refs": [
                {
                    "uuid": "8694d163-4347-4386-b028-e99017c8995b",
                    "label": "npro.adNPab{S}",
                }
            ],
        }
    ]

    body = note.read_text(encoding="utf-8").split("---", 2)[2]
    assert "# npro.adNab" in body
    assert "## Description" in body
    assert "pronoun preceding and modifying an abstract nominal" in body
    assert "## Notes" in body
    assert "## Links" in body
    assert (
        "[npro.adNPab{S}](../8/8694d163-4347-4386-b028-e99017c8995b.md)"
    ) in body


def test_import_syntactic_functions_can_filter_by_code(tmp_path: Path):
    out = tmp_path / "core"
    rc = run([
        "--format", "syntactic-functions",
        "--in", str(SYN_FUNC_INPUT),
        "--out", str(out),
        "--text-id", "npro.adNPab{S}",
        "--yes",
    ])
    assert rc == 0

    note = (
        out / "syntactic-functions" / "8"
        / "8694d163-4347-4386-b028-e99017c8995b.md"
    )
    assert note.is_file()
    assert _frontmatter(note)["code"] == "npro.adNPab{S}"


def test_import_semantic_features_extracts_divisions_from_single_xml(
    tmp_path: Path,
):
    out = tmp_path / "core"
    rc = run([
        "semantic-features",
        "--in", str(SEM_FEAT_INPUT),
        "--out", str(out),
        "--text-id", "e6526d79-b134-4e37-8bab-55b4884393bc",
        "--yes",
    ])
    assert rc == 0

    uuid = "e6526d79-b134-4e37-8bab-55b4884393bc"
    note = out / "semantic-features" / "e" / f"{uuid}.md"
    assert note.is_file()

    fm = _frontmatter(note)
    assert fm["uuid"] == uuid
    assert fm["type"] == "semantic-feature"
    assert fm["code"] == "graded"
    assert "descriptions" not in fm
    assert "notes" not in fm
    assert fm["source"] == {"source_file": "semantic-features.xml"}

    body = note.read_text(encoding="utf-8").split("---", 2)[2]
    assert "# graded" in body
    assert "## Description" in body
    assert "gradable, admitting of degrees" in body
    assert "## Notes" in body
    assert "comparative constructions" in body


def test_import_semantic_features_links_bibliography_references(
    tmp_path: Path,
):
    out = tmp_path / "core"
    rc = run([
        "--format", "semantic-features",
        "--in", str(SEM_FEAT_INPUT),
        "--out", str(out),
        "--text-id", "imp",
        "--yes",
    ])
    assert rc == 0

    uuid = "667a2e02-a4e1-4484-ae80-1382510681be"
    note = out / "semantic-features" / "6" / f"{uuid}.md"
    assert note.is_file()

    fm = _frontmatter(note)
    assert fm["code"] == "imp"
    assert fm["relations"] == [
        {
            "type": "source-references",
            "target_type": "bibliography",
            "refs": [
                {
                    "uuid": "574fc47b-68e2-4f99-a5c9-692ef8338357",
                    "label": "BROWN 2005",
                    "title": "Encyclopedia of Language and Linguistics. Second Edition",
                    "scope": "565",
                    "scope_unit": "page",
                }
            ],
        }
    ]

    body = note.read_text(encoding="utf-8").split("---", 2)[2]
    assert "# imp" in body
    assert "Imperative use of a verb" in body
    assert "## Links" in body
    assert "### Source References" in body
    assert (
        "[BROWN 2005](../../bibliography/5/"
        "574fc47b-68e2-4f99-a5c9-692ef8338357.md)"
    ) in body
    assert "Encyclopedia of Language and Linguistics. Second Edition" in body
    assert "page 565" in body


def test_import_words_writes_super_entry_and_entry_notes(tmp_path: Path):
    out = tmp_path / "core"
    rc = run([
        "words",
        "--in", str(WORD_INPUT),
        "--out", str(out),
        "--text-id", "703886f9-eb81-4985-b886-f9eb81598567",
        "--yes",
    ])
    assert rc == 0

    uuid = "703886f9-eb81-4985-b886-f9eb81598567"
    note = out / "super-entries" / "7" / f"{uuid}.md"
    delight_note = out / "words" / "d" / "d57eebf9-7218-46d5-95bc-4ac4591b81ed.md"
    assert note.is_file()
    assert delight_note.is_file()

    fm = _frontmatter(note)
    assert fm["uuid"] == uuid
    assert fm["type"] == "super-entry"
    assert fm["orth"] == "喜"
    assert fm["n"] == "4"
    assert fm["source"] == {
        "source_file": "uuid-703886f9-eb81-4985-b886-f9eb81598567.xml",
    }
    assert fm["forms"][0]["orth"] == "喜"
    assert fm["forms"][1]["graph_uuid"] == "c4711853-e554-4934-bdf2-97e5b33fbc53"
    assert fm["forms"][1]["pronunciations"] == [
        {"lang": "zh-Latn-x-pinyin", "value": "xǐ"},
        {"lang": "zh-x-oc", "value": "qhɯʔ"},
        {"lang": "zh-x-mc", "value": "hɨ"},
    ]

    assert [entry["concept"] for entry in fm["entries"]] == [
        "CUSTOM",
        "DELIGHT",
        "ENJOY",
        "HAPPY",
    ]
    delight = fm["entries"][1]
    assert delight["uuid"] == "d57eebf9-7218-46d5-95bc-4ac4591b81ed"
    assert delight["concept"] == "DELIGHT"
    assert delight["concept_uuid"] == "1c7bf322-c905-41e0-9145-7d4b01da86a1"
    assert delight["sense_count"] == 16

    body = note.read_text(encoding="utf-8").split("---", 2)[2]
    assert "# Super-entry: 喜" in body
    assert "## Forms" in body
    assert "- Orth: [喜](../../graphs/c/c4711853-e554-4934-bdf2-97e5b33fbc53.md)" in body
    assert "## Words" in body
    custom_pos = body.index("[CUSTOM](../../words/0/044ecd60-1d2f-40b2-a902-3c1384f4b2ca.md)")
    delight_pos = body.index("[DELIGHT](../../words/d/d57eebf9-7218-46d5-95bc-4ac4591b81ed.md)")
    enjoy_pos = body.index("[ENJOY](../../words/5/57102a8f-2ac7-483e-b9a2-d966689bbf86.md)")
    happy_pos = body.index("[HAPPY](../../words/3/338ddf66-a845-41e8-9101-64aa32a68ea3.md)")
    assert custom_pos < delight_pos < enjoy_pos < happy_pos
    assert "(16 senses, n=74)" in body
    assert "|" not in body

    entry_fm = _frontmatter(delight_note)
    assert entry_fm["uuid"] == "d57eebf9-7218-46d5-95bc-4ac4591b81ed"
    assert entry_fm["type"] == "word"
    assert entry_fm["super_entry_uuid"] == uuid
    assert entry_fm["super_entry_orth"] == "喜"
    assert entry_fm["concept"] == "DELIGHT"
    assert entry_fm["concept_uuid"] == "1c7bf322-c905-41e0-9145-7d4b01da86a1"
    assert entry_fm["bibliography"][0] == {
        "uuid": "2389c812-8053-4187-8f7a-19f6e856050f",
        "label": "FOGUANG",
        "title": "佛光大辭典 Fóguāng dàcídiǎn The Foguang Dictionary of Buddhism",
        "scope": "4899b",
        "scope_unit": "page",
    }
    assert "definition" not in entry_fm
    first_sense = entry_fm["senses"][0]
    assert first_sense["uuid"] == "45ddee60-d2a7-4973-9289-b93f0f921ac4"
    assert first_sense["body_number"] == 1
    assert first_sense["pos"] == "N"
    assert first_sense["syntactic_functions"] == [
        {
            "label": "nab.t",
            "uuid": "d128d787-1ecb-4c4f-8e89-5dd3edea91d1",
        }
    ]
    assert first_sense["semantic_features"] == [
        {
            "label": "psych",
            "uuid": "98e7674b-b362-466f-9568-d0c14470282a",
        }
    ]
    assert first_sense["usages"] == [
        {"value": "3", "type": "warring-states-currency"}
    ]
    assert "definition" not in first_sense

    entry_body = delight_note.read_text(encoding="utf-8").split("---", 2)[2]
    assert "# 喜: DELIGHT" in entry_body
    assert "- Super-entry: [喜](../../super-entries/7/703886f9-eb81-4985-b886-f9eb81598567.md)" in entry_body
    assert "- Concept: [DELIGHT](../../concepts/1/1c7bf322-c905-41e0-9145-7d4b01da86a1.md)" in entry_body
    assert "## Form" in entry_body
    assert "- Orth: [喜](../../graphs/c/c4711853-e554-4934-bdf2-97e5b33fbc53.md)" in entry_body
    assert "## Definition" in entry_body
    assert "Xǐ 喜 (ant. yōu 憂 \"worry\")" in entry_body
    assert "[FOGUANG](../../bibliography/2/2389c812-8053-4187-8f7a-19f6e856050f.md)" in entry_body
    assert "## Senses" in entry_body
    assert "1. delight (in someone N), joy about (something N)" in entry_body
    assert "   - Syntax: [nab.t](../../syntactic-functions/d/d128d787-1ecb-4c4f-8e89-5dd3edea91d1.md)" in entry_body
    assert "   - Semantic features: [psych](../../semantic-features/9/98e7674b-b362-466f-9568-d0c14470282a.md)" in entry_body
    assert "- 45ddee60-d2a7-4973-9289-b93f0f921ac4" not in entry_body
    assert "pos=N" not in entry_body
    assert "|" not in entry_body


def test_import_words_format_command_can_filter_by_orthograph(tmp_path: Path):
    out = tmp_path / "core"
    rc = run([
        "--format", "words",
        "--in", str(WORD_INPUT),
        "--out", str(out),
        "--text-id", "喜",
        "--yes",
    ])
    assert rc == 0

    note = (
        out / "super-entries" / "7"
        / "703886f9-eb81-4985-b886-f9eb81598567.md"
    )
    entry = (
        out / "words" / "0"
        / "044ecd60-1d2f-40b2-a902-3c1384f4b2ca.md"
    )
    assert note.is_file()
    assert entry.is_file()
    assert _frontmatter(note)["orth"] == "喜"


def test_import_words_can_filter_by_entry_uuid_and_concept(tmp_path: Path):
    out = tmp_path / "core"
    assert run([
        "words",
        "--in", str(WORD_INPUT),
        "--out", str(out),
        "--text-id", "57102a8f-2ac7-483e-b9a2-d966689bbf86",
        "--yes",
    ]) == 0

    parent = (
        out / "super-entries" / "7"
        / "703886f9-eb81-4985-b886-f9eb81598567.md"
    )
    enjoy = (
        out / "words" / "5"
        / "57102a8f-2ac7-483e-b9a2-d966689bbf86.md"
    )
    delight = (
        out / "words" / "d"
        / "d57eebf9-7218-46d5-95bc-4ac4591b81ed.md"
    )
    assert parent.is_file()
    assert enjoy.is_file()
    assert not delight.exists()
    assert _frontmatter(parent)["entries"] == [
        {
            "uuid": "57102a8f-2ac7-483e-b9a2-d966689bbf86",
            "sense_count": 1,
            "concept": "ENJOY",
            "concept_uuid": "b2d4f9f1-5b02-4cb3-bdb8-863eb65675af",
            "n": "2",
        }
    ]
    enjoy_fm = _frontmatter(enjoy)
    assert enjoy_fm["concept"] == "ENJOY"
    assert enjoy_fm["senses"][0]["provenance"] == {
        "resp": "#Valerie.Kiel",
        "updated": "2023-03-16T05:47:35.101+09:00",
        "created": "2019-10-31T03:57:30.023+09:00",
    }

    out2 = tmp_path / "core2"
    assert run([
        "--format", "words",
        "--in", str(WORD_INPUT),
        "--out", str(out2),
        "--text-id", "CUSTOM",
        "--yes",
    ]) == 0
    custom = (
        out2 / "words" / "0"
        / "044ecd60-1d2f-40b2-a902-3c1384f4b2ca.md"
    )
    assert custom.is_file()
    assert _frontmatter(custom)["concept"] == "CUSTOM"


def test_import_words_on_exists_skip_preserves_existing_note(tmp_path: Path):
    out = tmp_path / "core"
    assert run([
        "words",
        "--in", str(WORD_INPUT),
        "--out", str(out),
        "--text-id", "喜",
        "--yes",
    ]) == 0

    note = (
        out / "super-entries" / "7"
        / "703886f9-eb81-4985-b886-f9eb81598567.md"
    )
    entry = (
        out / "words" / "d"
        / "d57eebf9-7218-46d5-95bc-4ac4591b81ed.md"
    )
    note.write_text("sentinel\n", encoding="utf-8")
    entry.write_text("entry sentinel\n", encoding="utf-8")

    assert run([
        "words",
        "--in", str(WORD_INPUT),
        "--out", str(out),
        "--text-id", "喜",
        "--on-exists", "skip",
        "--yes",
    ]) == 0
    assert note.read_text(encoding="utf-8") == "sentinel\n"
    assert entry.read_text(encoding="utf-8") == "entry sentinel\n"
