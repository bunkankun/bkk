from __future__ import annotations

import json
from pathlib import Path

import yaml

from bkk.importer.hashing import ZERO_HASH
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.repair.cli import run as repair_run
from bkk.repair.overlong_front import find_overlong_front_buckets_in_bundle


def _write_bundle(
    root: Path,
    *,
    text_id: str = "KR0f0001",
    front_lengths: dict[int, int] | None = None,
    body_texts: dict[int, str] | None = None,
    editions: tuple[str, ...] = (),
) -> Path:
    front_lengths = front_lengths or {0: 8, 1: 5, 2: 3}
    body_texts = body_texts or {}
    bundle_dir = root / text_id
    bundle_dir.mkdir()

    def write_scope(scope: Path, edition: str | None = None) -> None:
        parts = []
        short = edition or "bkk"
        suffix = f"-{edition}" if edition else ""
        for seq, front_len in sorted(front_lengths.items()):
            juan_name = f"{text_id}_{seq:03d}{suffix}.yaml"
            body_text = body_texts.get(seq, "正文")
            juan = {
                "canonical_identifier": f"bkk:krp/{text_id}/{short}/v1/juan/{seq}",
                "seq": seq,
                "body": {
                    "text": body_text,
                    "hash": ZERO_HASH,
                },
                "metadata": {"title": "Overlong Front", "edition": {"short": short}},
                "hash": ZERO_HASH,
            }
            if front_len:
                juan["front"] = {
                    "text": "甲" * front_len,
                    "hash": ZERO_HASH,
                }
            (scope / juan_name).write_text(dump(juan), encoding="utf-8")
            parts.append(marker_to_flow({
                "seq": seq,
                "filename": juan_name,
                "hash": ZERO_HASH,
            }))

        manifest = {
            "canonical_identifier": f"bkk:krp/{text_id}/{short}/v1",
            "canonical_location": f"https://kanripo.org/bkk/{text_id}/v1",
            "canonical_set": {"identifier": "bkk:charset/cjk-v1", "hash": ZERO_HASH},
            "assets": {"parts": parts},
            "table_of_contents": [],
            "metadata": {"title": "Overlong Front", "edition": {"short": short}},
            "hash": ZERO_HASH,
        }
        manifest_name = (
            f"{text_id}-{edition}.manifest.yaml"
            if edition
            else f"{text_id}.manifest.yaml"
        )
        (scope / manifest_name).write_text(dump(manifest), encoding="utf-8")

    write_scope(bundle_dir)
    for edition in editions:
        scope = bundle_dir / "editions" / edition
        scope.mkdir(parents=True)
        write_scope(scope, edition)
    return bundle_dir


def _jsonl_rows(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# bkk-overlong-front version=1"
    return [json.loads(line) for line in lines[1:] if line.strip()]


def test_overlong_front_finds_longer_than_body_and_non_first_overlong(
    tmp_path: Path,
):
    bundle_dir = _write_bundle(
        tmp_path,
        front_lengths={0: 8, 1: 3, 2: 5, 3: 3},
        body_texts={0: "正文正文正文正文正文", 1: "正文", 2: "正文正文正文正文正文", 3: "正文正文"},
    )

    summary = find_overlong_front_buckets_in_bundle(bundle_dir, min_chars=4)

    assert summary["errors"] == []
    rows = summary["rows"]
    assert [row["seq"] for row in rows] == [1, 2]
    assert [row["reason"] for row in rows] == [
        "front-longer-than-body",
        "overlong-non-initial-front",
    ]
    assert rows[0]["chars"] == 3
    assert rows[0]["body_chars"] == 2
    assert rows[0]["path"] == "KR0f0001_001.yaml"


def test_overlong_front_can_include_first_juan(tmp_path: Path):
    bundle_dir = _write_bundle(
        tmp_path,
        front_lengths={0: 8, 1: 3},
        body_texts={0: "正文正文正文正文正文", 1: "正文正文"},
    )

    summary = find_overlong_front_buckets_in_bundle(
        bundle_dir,
        min_chars=4,
        include_first=True,
    )

    assert [row["seq"] for row in summary["rows"]] == [0]
    assert summary["rows"][0]["reason"] == "overlong-first-front"


def test_overlong_front_cli_writes_report_for_corpus_prefix(
    tmp_path: Path, capsys,
):
    _write_bundle(tmp_path, text_id="KR0f0001")
    _write_bundle(tmp_path, text_id="KR1f0001", front_lengths={1: 9, 2: 9})
    report = tmp_path / "overlong-front.jsonl"

    rc = repair_run([
        "overlong-front",
        "--out",
        str(tmp_path),
        "--text-prefix",
        "KR0f",
        "--min-chars",
        "4",
        "--report",
        str(report),
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote 3 overlong front target(s)" in out
    assert "scanned 1 bundles" in out
    rows = _jsonl_rows(report)
    assert [row["seq"] for row in rows] == [0, 1, 2]
    assert {row["textid"] for row in rows} == {"KR0f0001"}


def test_overlong_front_cli_rejects_prefix_with_single_bundle(
    tmp_path: Path, capsys,
):
    bundle_dir = _write_bundle(tmp_path)

    rc = repair_run([
        "overlong-front",
        "--bundle",
        str(bundle_dir),
        "--text-prefix",
        "KR0f",
    ])

    assert rc == 2
    assert "provide either --text-prefix or a single bundle/text id" in (
        capsys.readouterr().err
    )


def test_overlong_front_cli_write_updates_master_and_editions(tmp_path: Path):
    bundle_dir = _write_bundle(
        tmp_path,
        front_lengths={0: 4, 1: 5},
        body_texts={0: "正文正文正文", 1: "正文"},
        editions=("WYG",),
    )

    rc = repair_run([
        "overlong-front",
        "--bundle",
        str(bundle_dir),
        "--min-chars",
        "4",
        "--write",
    ])

    assert rc == 0
    master_0 = yaml.safe_load((bundle_dir / "KR0f0001_000.yaml").read_text("utf-8"))
    master_1 = yaml.safe_load((bundle_dir / "KR0f0001_001.yaml").read_text("utf-8"))
    edition_1 = yaml.safe_load(
        (bundle_dir / "editions" / "WYG" / "KR0f0001_001-WYG.yaml").read_text("utf-8")
    )
    assert "front" in master_0
    assert "front" not in master_1
    assert master_1["body"]["text"] == "甲甲甲甲甲正文"
    assert "front" not in edition_1
    assert edition_1["body"]["text"] == "甲甲甲甲甲正文"
