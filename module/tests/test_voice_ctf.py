"""Tests for citation tree fragment generation from heading voices."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import build_marker_asset
from bkk.voice.cli import _process_one_ctf, _run_ctf
from bkk.voice.ctf import (
    HeadingRecord,
    build_ctf_asset,
    build_ctf_nodes,
    collect_indent_heading_voices,
    collect_manifest_headings,
    ctf_hash,
    ctf_tsv_text,
)


TEXT_ID = "KR4c0022"


def _juan_hash(juan: dict) -> str:
    data = copy.deepcopy(juan)
    data["hash"] = ZERO_HASH
    return sha256_jcs(data)


def _write_ctf_bundle(
    bundle_dir: Path,
    *,
    text_id: str = TEXT_ID,
    body_text: str,
    markers: list[dict],
    toc: list[dict] | None = None,
    source_path: Path | None = None,
    source_repository: str = "cbeta",
) -> Path:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    body = {"text": body_text, "hash": sha256_text(body_text)}
    juan = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1/juan/1",
        "seq": 1,
        "body": body,
        "hash": ZERO_HASH,
    }
    juan["hash"] = _juan_hash(juan)
    juan_name = f"{text_id}_001.yaml"
    (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")

    marker_asset = build_marker_asset(
        text_id,
        1,
        None,
        {"body": markers},
    )
    marker_name = f"assets/{text_id}_001.markers.yaml"
    (bundle_dir / "assets").mkdir()
    (bundle_dir / marker_name).write_text(dump(marker_asset), encoding="utf-8")

    manifest = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1",
        "assets": {
            "parts": [
                marker_to_flow({
                    "seq": 1,
                    "filename": juan_name,
                    "hash": juan["hash"],
                }),
            ],
            "markers": [
                marker_to_flow({
                    "seq": 1,
                    "role": "markers",
                    "filename": marker_name,
                    "hash": marker_asset["hash"],
                }),
            ],
        },
        "metadata": {
            "title": "Test",
            "identifiers": {"krp": text_id},
            "edition": {"short": "bkk"},
        },
        "table_of_contents": toc or [],
        "hash": ZERO_HASH,
    }
    if source_path is not None:
        manifest["metadata"]["source"] = {
            "repository": source_repository,
            "path": str(source_path),
            "old_id": source_path.stem,
        }
        if source_repository == "cbeta":
            manifest["metadata"]["identifiers"]["cbeta"] = source_path.stem
            manifest["metadata"]["identifiers"]["cbeta_old_id"] = source_path.stem
    manifest["hash"] = manifest_hash(manifest)
    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    manifest_path.write_text(dump(manifest), encoding="utf-8")
    return manifest_path


def _lb(offset: int) -> dict:
    return {"type": "line-break", "offset": offset, "content": "", "id": ""}


def _indent(offset: int, depth: int) -> dict:
    return {"type": "indent", "offset": offset, "content": "\u3000" * depth, "id": ""}


def _heading(offset: int, length: int, depth: int, marker_id: str) -> dict:
    return {
        "type": "voice",
        "offset": offset,
        "length": length,
        "name": "head",
        "id": marker_id,
        "source": "indent-headings",
        "indent_depth": depth,
    }


def _citation_nodes(nodes: list[dict]) -> list[dict]:
    return [node for node in nodes if node.get("level") != 0]


def test_build_ctf_nodes_hierarchy_spans_and_parents() -> None:
    text = "x甲一xxx乙二xxx丙三xxxx丁四xxxx戊五xxxx"
    headings = [
        HeadingRecord(1, 2, 1, "h1", 0),
        HeadingRecord(6, 2, 2, "h2", 1),
        HeadingRecord(11, 2, 3, "h3", 2),
        HeadingRecord(17, 2, 2, "h4", 3),
        HeadingRecord(23, 2, 1, "h5", 4),
    ]

    nodes = build_ctf_nodes(
        text_id=TEXT_ID,
        seq=1,
        bucket_name="body",
        text=text,
        headings=headings,
    )

    assert nodes[0] == {
        "id": "KR4c0022/1",
        "label": "KR4c0022/1",
        "level": 0,
        "parent_id": TEXT_ID,
    }
    citations = _citation_nodes(nodes)
    assert citations[0]["parent_id"] == "KR4c0022/1"
    assert citations[0]["id"] == "KR4c0022/1/1/@1+2"
    assert citations[1]["id"] == "KR4c0022/1/1/1/@6+2"
    assert citations[1]["parent_id"] == "KR4c0022/1/1/@1+2"
    assert citations[2]["id"] == "KR4c0022/1/1/1/1/@11+2"
    assert citations[2]["parent_id"] == "KR4c0022/1/1/1/@6+2"
    assert citations[3]["id"] == "KR4c0022/1/1/2/@17+2"
    assert citations[3]["parent_id"] == "KR4c0022/1/1/@1+2"
    assert citations[4]["id"] == "KR4c0022/1/2/@23+2"
    assert citations[1]["span_ref"] == "KR4c0022/1/@6+11"
    assert citations[2]["span_ref"] == "KR4c0022/1/@11+6"
    assert citations[-1]["span_ref"] == f"KR4c0022/1/@23+{len(text) - 23}"
    assert all("path" not in node for node in nodes)


def test_build_ctf_nodes_short_refs() -> None:
    nodes = build_ctf_nodes(
        text_id=TEXT_ID,
        seq=1,
        bucket_name="body",
        text="王右丞集箋注卷一古詩正文",
        headings=[
            HeadingRecord(0, 8, 1, "h1", 0),
            HeadingRecord(8, 2, 2, "h2", 1),
        ],
        short_refs=True,
    )

    assert nodes == [
        {
            "id": "4c22/1",
            "label": "4c22/1",
            "level": 0,
            "parent_id": "4c22",
        },
        {
            "id": "4c22/1/@0+8",
            "label": "王右丞集箋注卷一",
            "level": 0,
            "parent_id": "4c22/1",
            "marker_id": "h1",
        },
        {
            "id": "4c22/1/1/@8+2",
            "label": "古詩",
            "level": 1,
            "parent_id": "4c22/1",
            "marker_id": "h2",
            "span_ref": "4c22/1/@8+4",
        },
    ]


def test_build_ctf_nodes_single_initial_level_one_uses_juan_starter() -> None:
    nodes = build_ctf_nodes(
        text_id=TEXT_ID,
        seq=2,
        bucket_name="body",
        text="卷二xx積雨xx丁四",
        headings=[
            HeadingRecord(0, 2, 1, "h1", 0),
            HeadingRecord(4, 2, 2, "h2", 1),
            HeadingRecord(8, 2, 2, "h3", 2),
        ],
    )

    assert [(node["id"], node["parent_id"], node["level"]) for node in nodes] == [
        ("KR4c0022/2", "KR4c0022", 0),
        ("KR4c0022/2/@0+2", "KR4c0022/2", 0),
        ("KR4c0022/2/1/@4+2", "KR4c0022/2", 1),
        ("KR4c0022/2/2/@8+2", "KR4c0022/2", 1),
    ]
    assert _citation_nodes(nodes)[0]["span_ref"] == "KR4c0022/2/@4+4"


def test_build_ctf_nodes_juan_starter_and_category_are_labels() -> None:
    nodes = build_ctf_nodes(
        text_id=TEXT_ID,
        seq=10,
        bucket_name="body",
        text="王右丞集箋注卷十近體詩二十六首奉和聖製從蓬萊",
        headings=[
            HeadingRecord(0, 8, 1, "h1", 0),
            HeadingRecord(8, 7, 1, "h2", 1),
            HeadingRecord(15, 10, 2, "h3", 2),
        ],
        juan_label="王右丞集箋注卷十\u3000近體詩二十六首",
    )

    assert [
        (node["id"], node["parent_id"], node["level"], node["label"])
        for node in nodes
    ] == [
        ("KR4c0022/10", "KR4c0022", 0, "王右丞集箋注卷十\u3000近體詩二十六首"),
        ("KR4c0022/10/@0+8", "KR4c0022/10", 0, "王右丞集箋注卷十"),
        ("KR4c0022/10/@8+7", "KR4c0022/10", 0, "近體詩二十六首"),
        ("KR4c0022/10/1/@15+10", "KR4c0022/10", 1, "奉和聖製從蓬萊"),
    ]


def test_build_ctf_node_sequence_path_does_not_reset_across_descendants() -> None:
    nodes = build_ctf_nodes(
        text_id=TEXT_ID,
        seq=12,
        bucket_name="body",
        text="x甲一xx乙二xx丙三xx丁四xx戊五",
        headings=[
            HeadingRecord(1, 2, 1, "h1", 0),
            HeadingRecord(5, 2, 2, "h2", 1),
            HeadingRecord(9, 2, 3, "h3", 2),
            HeadingRecord(13, 2, 2, "h4", 3),
            HeadingRecord(17, 2, 1, "h5", 4),
        ],
    )

    assert [node["id"] for node in _citation_nodes(nodes)] == [
        "KR4c0022/12/1/@1+2",
        "KR4c0022/12/1/1/@5+2",
        "KR4c0022/12/1/1/1/@9+2",
        "KR4c0022/12/1/2/@13+2",
        "KR4c0022/12/2/@17+2",
    ]


def test_build_ctf_asset_auto_uses_existing_voices_and_hashes() -> None:
    text = "王右丞集箋注卷一古詩十首登樓歌正文"
    asset = build_ctf_asset(
        text_id=TEXT_ID,
        seq=1,
        bucket_name="body",
        text=text,
        markers=[
            _heading(0, 8, 1, "h1"),
            _heading(8, 4, 1, "h2"),
            _heading(12, 3, 2, "h3"),
            _lb(0),
            _indent(0, 1),
        ],
        manifest_hash="sha256:manifest",
        bucket_hash="sha256:bucket",
        heading_source="auto",
    )

    assert asset["source"]["mode"] == "voices"
    assert [node["label"] for node in _citation_nodes(asset["nodes"])] == [
        "登樓歌",
    ]
    assert asset["nodes"][0]["id"] == "KR4c0022/1"
    assert asset["nodes"][0]["parent_id"] == TEXT_ID
    assert asset["nodes"][0]["label"] == "王右丞集箋注卷一\u3000古詩十首"
    assert _citation_nodes(asset["nodes"])[0]["parent_id"] == "KR4c0022/1"
    assert _citation_nodes(asset["nodes"])[0]["id"] == "KR4c0022/1/1/@12+3"
    assert asset["hash"] == ctf_hash(asset)


def test_build_ctf_asset_auto_ignores_stale_commentary_lemma_heading() -> None:
    text = "近體詩十六首春過賀遂員外藥園藥園唐李華賀遂員外藥園小山池記"
    asset = build_ctf_asset(
        text_id=TEXT_ID,
        seq=12,
        bucket_name="body",
        text=text,
        markers=[
            _heading(0, 6, 1, "h1"),
            _heading(6, 8, 2, "h2"),
            _heading(14, 2, 1, "h3"),
            {"type": "voice", "offset": 16, "length": 21, "name": "note", "id": "n1"},
        ],
        manifest_hash="sha256:manifest",
        bucket_hash="sha256:bucket",
        heading_source="auto",
    )

    assert [node["label"] for node in _citation_nodes(asset["nodes"])] == [
        "春過賀遂員外藥園",
    ]
    assert _citation_nodes(asset["nodes"])[0]["id"] == "KR4c0022/12/1/@6+8"
    assert _citation_nodes(asset["nodes"])[0]["parent_id"] == "KR4c0022/12"


def test_collect_indent_heading_voices_skips_heading_inside_note() -> None:
    markers = [
        _heading(2, 3, 3, "h1"),
        {"type": "voice", "offset": 0, "length": 5, "name": "note", "id": "n1"},
    ]

    assert collect_indent_heading_voices(markers, text_len=8) == []


def test_build_ctf_asset_preserves_derived_reconstructed_line_label() -> None:
    text = "前一篇正文前二篇正文同王十三維哭殷遙儲光羲正文"
    asset = build_ctf_asset(
        text_id=TEXT_ID,
        seq=1,
        bucket_name="body",
        text=text,
        markers=[
            _lb(0), _indent(0, 2),
            _lb(3),
            _lb(5), _indent(5, 2),
            _lb(8),
            _lb(10), _indent(10, 2), _indent(18, 5),
            _lb(21),
        ],
        manifest_hash=None,
        bucket_hash=None,
        heading_source="derive",
    )

    third = _citation_nodes(asset["nodes"])[2]
    assert third["id"] == f"{TEXT_ID}/1/3/@10+11"
    assert third["label"] == "同王十三維哭殷遙　　　　　儲光羲"


def test_build_ctf_asset_auto_uses_derived_when_existing_is_incomplete() -> None:
    text = "近體詩十六首春過賀遂員外藥園正文河南嚴尹弟見宿弊廬訪别人賦十韻本文"
    asset = build_ctf_asset(
        text_id=TEXT_ID,
        seq=12,
        bucket_name="body",
        text=text,
        markers=[
            _lb(0), _indent(0, 1), _heading(0, 6, 1, "h1"),
            _lb(6), _indent(6, 2), _heading(6, 8, 2, "h2"),
            _lb(14),
            _lb(16), _indent(16, 2),
            _lb(31),
        ],
        manifest_hash="sha256:manifest",
        bucket_hash="sha256:bucket",
        heading_source="auto",
    )

    assert asset["source"]["mode"] == "derive"
    assert [node["label"] for node in _citation_nodes(asset["nodes"])] == [
        "春過賀遂員外藥園",
        "河南嚴尹弟見宿弊廬訪别人賦十韻",
    ]
    assert [node["id"] for node in _citation_nodes(asset["nodes"])] == [
        "KR4c0022/12/1/@6+8",
        "KR4c0022/12/2/@16+15",
    ]
    assert all(node["parent_id"] == "KR4c0022/12" for node in _citation_nodes(asset["nodes"]))


def test_build_ctf_asset_manifest_source_ignores_broken_span_end() -> None:
    text = "類一子一正文子二正文類二正文"
    manifest = {
        "table_of_contents": [
            {
                "ref": {"seq": 2, "marker_id": "m1", "span": ["body", 0, 0]},
                "label": "類一",
                "type": "mulu",
                "level": 1,
            },
            {
                "ref": {"seq": 2, "marker_id": "m2", "span": ["body", 2, 2]},
                "label": "子一",
                "type": "mulu",
                "level": 2,
            },
            {
                "ref": {"seq": 2, "marker_id": "m3", "span": ["body", 6, 6]},
                "label": "子二",
                "type": "mulu",
                "level": 2,
            },
            {
                "ref": {"seq": 2, "marker_id": "m4", "span": ["body", 10, 10]},
                "label": "類二",
                "type": "mulu",
                "level": 1,
            },
        ],
    }

    headings = collect_manifest_headings(
        manifest,
        seq=2,
        bucket_name="body",
        text_len=len(text),
    )
    assert [(h.offset, h.length, h.level, h.label) for h in headings] == [
        (0, 2, 1, "類一"),
        (2, 2, 2, "子一"),
        (6, 2, 2, "子二"),
        (10, 2, 1, "類二"),
    ]

    asset = build_ctf_asset(
        text_id=TEXT_ID,
        seq=2,
        bucket_name="body",
        text=text,
        markers=[],
        manifest_hash="sha256:manifest",
        bucket_hash="sha256:bucket",
        manifest=manifest,
        heading_source="manifest",
    )

    assert asset["source"]["mode"] == "manifest"
    citations = _citation_nodes(asset["nodes"])
    assert [node["label"] for node in citations] == ["子一", "子二", "類二"]
    assert [node["span_ref"] for node in citations] == [
        f"{TEXT_ID}/2/@2+4",
        f"{TEXT_ID}/2/@6+4",
        f"{TEXT_ID}/2/@10+{len(text) - 10}",
    ]


def test_build_ctf_asset_labels_prefix_juan_starter_and_category() -> None:
    text = "王右丞集箋注卷十二仁和趙殿成撰近體詩十六首春過賀遂員外藥園正文"
    asset = build_ctf_asset(
        text_id=TEXT_ID,
        seq=12,
        bucket_name="body",
        text=text,
        markers=[
            _lb(15), _indent(15, 1),
            _lb(21), _indent(21, 2),
            _lb(29),
        ],
        manifest_hash="sha256:manifest",
        bucket_hash="sha256:bucket",
        heading_source="derive",
    )

    assert asset["label"] == "王右丞集箋注卷十二\u3000近體詩十六首"
    assert [
        (node["id"], node["parent_id"], node["level"], node["label"])
        for node in asset["nodes"]
    ] == [
        ("KR4c0022/12", TEXT_ID, 0, "王右丞集箋注卷十二\u3000近體詩十六首"),
        ("KR4c0022/12/@0+9", "KR4c0022/12", 0, "王右丞集箋注卷十二"),
        ("KR4c0022/12/@15+6", "KR4c0022/12", 0, "近體詩十六首"),
        ("KR4c0022/12/1/@21+8", "KR4c0022/12", 1, "春過賀遂員外藥園"),
    ]


def test_ctf_tsv_text_includes_text_root_and_heading_rows() -> None:
    text = ctf_tsv_text(
        text_id=TEXT_ID,
        text_label="王右丞集箋注",
        nodes=[
            {
                "id": "KR4c0022/1",
                "parent_id": TEXT_ID,
                "label": "王右丞集箋注卷一",
            },
            {
                "id": "KR4c0022/1/1/@0+8",
                "parent_id": "KR4c0022/1",
                "label": "王右丞集箋注卷一",
                "span_ref": "KR4c0022/1/@0+12",
            },
        ],
        juan_labels={1: "王右丞集箋注卷一"},
    )

    assert text.splitlines() == [
        "id\tparent_id\tlabel\tend",
        "KR4c0022\tKR4c\t王右丞集箋注\t",
        "KR4c0022/1\tKR4c0022\t王右丞集箋注卷一\t",
        "KR4c0022/1/1/@0+8\tKR4c0022/1\t王右丞集箋注卷一\t12",
    ]


def test_ctf_tsv_text_uses_implicit_juan_parent_for_short_refs() -> None:
    text = ctf_tsv_text(
        text_id=TEXT_ID,
        text_label=None,
        nodes=[
            {
                "id": "4c22/12",
                "parent_id": "4c22",
                "label": "王右丞集箋注卷十二\u3000近體詩十六首",
            },
            {
                "id": "4c22/12/1/@21+8",
                "parent_id": "4c22/12",
                "label": "春過賀遂員外藥園",
                "span_ref": "4c22/12/@21+906",
            },
        ],
        juan_labels={12: "王右丞集箋注卷十二\u3000近體詩十六首"},
        short_refs=True,
    )

    assert text.splitlines() == [
        "id\tparent_id\tlabel\tend",
        "4c22\t4c\tKR4c0022\t",
        "4c22/12\t4c22\t王右丞集箋注卷十二\u3000近體詩十六首\t",
        "4c22/12/1/@21+8\t4c22/12\t春過賀遂員外藥園\t927",
    ]


def test_process_one_ctf_derives_without_existing_voices(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path = _write_ctf_bundle(
        bundle,
        body_text="王右丞集箋注卷一仁和趙殿成撰古詩十首扶南曲歌詞五首",
        markers=[
            _lb(0), _indent(0, 1),
            _lb(8), _indent(8, 12),
            _lb(14), _indent(14, 1),
            _lb(18), _indent(18, 2),
        ],
    )

    stats = _process_one_ctf(
        bundle,
        manifest_path,
        TEXT_ID,
        out_dir=None,
        heading_source="derive",
        short_refs=False,
        force=False,
        dry_run=False,
    )

    assert stats["written"] == 1
    data = yaml.safe_load(
        (bundle / "assets" / f"{TEXT_ID}_001.ctf.yaml").read_text(encoding="utf-8")
    )
    assert data["source"]["mode"] == "derive"
    label_nodes = [node for node in data["nodes"] if node["level"] == 0]
    assert "古詩十首" in [node["label"] for node in label_nodes]
    labels = [node["label"] for node in _citation_nodes(data["nodes"])]
    assert "扶南曲歌詞五首" in labels
    first_poem = _citation_nodes(data["nodes"])[labels.index("扶南曲歌詞五首")]
    assert first_poem["id"] == f"{TEXT_ID}/1/1/@18+7"
    assert first_poem["parent_id"] == f"{TEXT_ID}/1"


def test_run_ctf_skip_force_and_dry_run(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path = _write_ctf_bundle(
        bundle,
        body_text="王右丞集箋注卷一古詩正文",
        markers=[
            _heading(0, 8, 1, "h1"),
            _heading(8, 2, 2, "h2"),
        ],
    )
    manifest_before = manifest_path.read_text(encoding="utf-8")

    assert _run_ctf(
        bundle,
        None,
        out_dir=None,
        tsv=False,
        tsv_out_root=None,
        heading_source="auto",
        short_refs=False,
        force=False,
        dry_run=False,
    ) == 0
    output = bundle / "assets" / f"{TEXT_ID}_001.ctf.yaml"
    assert output.exists()
    first = yaml.safe_load(output.read_text(encoding="utf-8"))

    assert _run_ctf(
        bundle,
        None,
        out_dir=None,
        tsv=False,
        tsv_out_root=None,
        heading_source="derive",
        short_refs=False,
        force=False,
        dry_run=False,
    ) == 0
    assert yaml.safe_load(output.read_text(encoding="utf-8")) == first

    assert _run_ctf(
        bundle,
        None,
        out_dir=None,
        tsv=False,
        tsv_out_root=None,
        heading_source="auto",
        short_refs=True,
        force=True,
        dry_run=False,
    ) == 0
    forced = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert forced["source"]["mode"] == "voices"
    assert forced["nodes"][0]["id"] == "4c22/1"
    assert _citation_nodes(forced["nodes"])[0]["id"].startswith("4c22/1/1/@")

    output.unlink()
    assert _run_ctf(
        bundle,
        None,
        out_dir=None,
        tsv=False,
        tsv_out_root=None,
        heading_source="auto",
        short_refs=False,
        force=False,
        dry_run=True,
    ) == 0
    assert not output.exists()
    assert manifest_path.read_text(encoding="utf-8") == manifest_before


def test_run_ctf_tsv_writes_whole_text_file_to_out_root(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    _write_ctf_bundle(
        bundle,
        body_text="王右丞集箋注卷一古詩正文",
        markers=[
            _heading(0, 8, 1, "h1"),
            _heading(8, 2, 2, "h2"),
        ],
    )
    tsv_root = tmp_path / "ctf"

    assert _run_ctf(
        bundle,
        None,
        out_dir=None,
        tsv=True,
        tsv_out_root=tsv_root,
        heading_source="auto",
        short_refs=False,
        force=False,
        dry_run=False,
    ) == 0

    output = tsv_root / "KR4c" / f"{TEXT_ID}.ctf.tsv"
    assert output.read_text(encoding="utf-8").splitlines() == [
        "id\tparent_id\tlabel\tend",
        f"{TEXT_ID}\tKR4c\tTest\t",
        f"{TEXT_ID}/1\t{TEXT_ID}\t王右丞集箋注卷一\t",
        f"{TEXT_ID}/1/@0+8\t{TEXT_ID}/1\t王右丞集箋注卷一\t",
        f"{TEXT_ID}/1/1/@8+2\t{TEXT_ID}/1\t古詩\t12",
    ]
    assert not (bundle / "assets" / f"{TEXT_ID}_001.ctf.yaml").exists()


def test_run_ctf_tsv_uses_global_ctf_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bundle = tmp_path / TEXT_ID
    _write_ctf_bundle(
        bundle,
        body_text="王右丞集箋注卷一正文",
        markers=[_heading(0, 8, 1, "h1")],
    )
    ctf_root = tmp_path / "configured-ctf"
    monkeypatch.setattr(
        "bkk.config.load_rc",
        lambda: {"global": {"ctf_root": ctf_root}},
    )

    assert _run_ctf(
        bundle,
        None,
        out_dir=None,
        tsv=True,
        tsv_out_root=None,
        heading_source="auto",
        short_refs=False,
        force=False,
        dry_run=False,
    ) == 0

    assert (ctf_root / "KR4c" / f"{TEXT_ID}.ctf.tsv").exists()


def test_repair_ctf_can_use_manifest_source_explicitly(tmp_path: Path) -> None:
    from bkk.repair.cli import run as repair_run

    bundle = tmp_path / TEXT_ID
    _write_ctf_bundle(
        bundle,
        body_text="類一子一正文子二正文",
        markers=[],
        toc=[
            {
                "ref": {"seq": 1, "marker_id": "m1", "span": ["body", 0, 0]},
                "label": "類一",
                "type": "mulu",
                "level": 1,
            },
            {
                "ref": {"seq": 1, "marker_id": "m2", "span": ["body", 2, 2]},
                "label": "子一",
                "type": "mulu",
                "level": 2,
            },
            {
                "ref": {"seq": 1, "marker_id": "m3", "span": ["body", 6, 6]},
                "label": "子二",
                "type": "mulu",
                "level": 2,
            },
        ],
    )

    assert repair_run([
        "ctf",
        "--bundle",
        str(bundle),
        "--heading-source",
        "manifest",
    ]) == 0

    output = bundle / "assets" / f"{TEXT_ID}_001.ctf.yaml"
    data = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert data["source"]["mode"] == "manifest"
    assert [node["label"] for node in _citation_nodes(data["nodes"])] == [
        "子一",
        "子二",
    ]


def test_repair_ctf_defaults_to_source_xml_when_manifest_toc_is_broken(
    tmp_path: Path,
) -> None:
    from bkk.repair.cli import run as repair_run

    xml = tmp_path / "T01n0001.xml"
    xml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"
     xmlns:cb="http://www.cbeta.org/ns/1.0"
     xml:id="T01n0001">
  <teiHeader>
    <fileDesc>
      <titleStmt><title xml:lang="zh-Hant">測試經</title></titleStmt>
      <publicationStmt><p/></publicationStmt>
      <sourceDesc><p/></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <cb:juan fun="open" n="1"/>
      <cb:div>
        <cb:mulu level="1" type="其他">類一</cb:mulu><head>類一</head>
        <cb:div>
          <cb:mulu level="2" type="其他">子一</cb:mulu><head>子一</head><p>正文</p>
          <cb:mulu level="2" type="其他">子二</cb:mulu><head>子二</head><p>正文</p>
        </cb:div>
      </cb:div>
      <cb:juan fun="close" n="1"/>
    </body>
  </text>
</TEI>
""",
        encoding="utf-8",
    )
    bundle = tmp_path / TEXT_ID
    _write_ctf_bundle(
        bundle,
        body_text="類一子一正文子二正文",
        markers=[],
        toc=[],
        source_path=xml,
    )

    assert repair_run(["ctf", "--bundle", str(bundle)]) == 0

    output = bundle / "assets" / f"{TEXT_ID}_001.ctf.yaml"
    data = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert data["source"]["mode"] == "source-xml"
    assert data["source"]["voice_source"] == "xml"
    citations = _citation_nodes(data["nodes"])
    assert [node["label"] for node in citations] == ["子一", "子二"]
    assert [node["span_ref"] for node in citations] == [
        f"{TEXT_ID}/1/@2+4",
        f"{TEXT_ID}/1/@6+4",
    ]


def test_repair_ctf_recovers_tls_relative_source_xml(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from bkk.repair.cli import run as repair_run

    text_id = "KR6q0002"
    tls_root = tmp_path / "tls-root"
    relative_source = Path("data/tls/KR6q0002.xml")
    xml = tls_root / "tls-texts" / relative_source
    xml.parent.mkdir(parents=True)
    xml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"
     xmlns:xml="http://www.w3.org/XML/1998/namespace"
     xml:id="KR6q0002">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Test</title></titleStmt>
      <publicationStmt><publisher>x</publisher></publicationStmt>
      <sourceDesc><p>x</p></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <div n="01" type="juan">
        <head><seg xml:id="KR6q0002_T_01-d1h1s1">卷一</seg></head>
        <p><seg xml:id="KR6q0002_T_01-d1d1p1s1">正文</seg></p>
        <div>
          <head><seg xml:id="KR6q0002_T_01-d1d2h1s1">章一</seg></head>
          <p><seg xml:id="KR6q0002_T_01-d1d2d1p1s1">細文</seg></p>
        </div>
      </div>
    </body>
  </text>
</TEI>
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "bkk.config.load_rc",
        lambda: {"global": {"tls_root": tls_root}},
    )

    bundle = tmp_path / text_id
    _write_ctf_bundle(
        bundle,
        text_id=text_id,
        body_text="卷一正文章一細文",
        markers=[],
        toc=[],
        source_path=relative_source,
        source_repository="tls-texts",
    )

    assert repair_run(["ctf", "--bundle", str(bundle)]) == 0

    output = bundle / "assets" / f"{text_id}_001.ctf.yaml"
    data = yaml.safe_load(output.read_text(encoding="utf-8"))
    assert data["source"]["mode"] == "source-xml"
    citations = _citation_nodes(data["nodes"])
    assert [node["label"] for node in citations] == ["章一"]
    assert citations[0]["span_ref"] == f"{text_id}/1/@4+4"


def test_repair_ctf_reports_missing_source_xml_without_traceback(
    tmp_path: Path,
    capsys,
) -> None:
    from bkk.repair.cli import run as repair_run

    bundle = tmp_path / TEXT_ID
    missing = tmp_path / "missing.xml"
    _write_ctf_bundle(
        bundle,
        body_text="類一子一正文",
        markers=[],
        toc=[],
        source_path=missing,
    )

    assert repair_run(["ctf", "--bundle", str(bundle)]) == 1

    captured = capsys.readouterr()
    assert "source XML not found" in captured.err
    assert "Traceback" not in captured.err
    assert not (bundle / "assets" / f"{TEXT_ID}_001.ctf.yaml").exists()


def test_repair_ctf_parser_defaults_heading_source_to_source_xml() -> None:
    from bkk.repair.cli import build_parser

    args = build_parser().parse_args([
        "ctf",
        "--bundle",
        "/tmp/KR4c0022",
        "--juan",
        "1",
        "--short",
        "--tsv",
        "--out",
        "/tmp/ctf",
        "--force",
        "--dry-run",
    ])

    assert args.op == "ctf"
    assert args.heading_source == "source-xml"
    assert args.short_refs is True
    assert args.tsv is True
    assert str(args.out_root) == "/tmp/ctf"


def test_ctf_parser_accepts_options() -> None:
    from bkk.voice.cli import build_parser

    args = build_parser().parse_args([
        "ctf",
        "--bundle",
        "/tmp/KR4c0022",
        "--juan",
        "1",
        "--heading-source",
        "derive",
        "--short",
        "--tsv",
        "--out",
        "/tmp/ctf",
        "--force",
        "--dry-run",
    ])

    assert args.op == "ctf"
    assert args.heading_source == "derive"
    assert args.short_refs is True
    assert args.tsv is True
    assert str(args.out_root) == "/tmp/ctf"
