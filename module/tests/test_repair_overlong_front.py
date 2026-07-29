from __future__ import annotations

import json
from pathlib import Path

from bkk.importer.hashing import ZERO_HASH
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.repair.cli import run as repair_run
from bkk.repair.overlong_front import find_overlong_front_buckets_in_bundle


def _write_bundle(
    root: Path,
    *,
    text_id: str = "KR0f0001",
    front_lengths: dict[int, int] | None = None,
) -> Path:
    front_lengths = front_lengths or {1: 8, 2: 5, 3: 3}
    bundle_dir = root / text_id
    bundle_dir.mkdir()
    parts = []
    for seq, front_len in sorted(front_lengths.items()):
        juan_name = f"{text_id}_{seq:03d}.yaml"
        juan = {
            "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/juan/{seq}",
            "seq": seq,
            "body": {
                "text": "正文",
                "hash": ZERO_HASH,
            },
            "metadata": {"title": "Overlong Front", "edition": {"short": "bkk"}},
            "hash": ZERO_HASH,
        }
        if front_len:
            juan["front"] = {
                "text": "甲" * front_len,
                "hash": ZERO_HASH,
            }
        (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")
        parts.append(marker_to_flow({
            "seq": seq,
            "filename": juan_name,
            "hash": ZERO_HASH,
        }))

    manifest = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1",
        "canonical_location": f"https://kanripo.org/bkk/{text_id}/v1",
        "canonical_set": {"identifier": "bkk:charset/cjk-v1", "hash": ZERO_HASH},
        "assets": {"parts": parts},
        "table_of_contents": [],
        "metadata": {"title": "Overlong Front", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    (bundle_dir / f"{text_id}.manifest.yaml").write_text(
        dump(manifest), encoding="utf-8",
    )
    return bundle_dir


def _jsonl_rows(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# bkk-overlong-front version=1"
    return [json.loads(line) for line in lines[1:] if line.strip()]


def test_overlong_front_skips_first_juan_by_default(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path)

    summary = find_overlong_front_buckets_in_bundle(bundle_dir, min_chars=4)

    assert summary["errors"] == []
    rows = summary["rows"]
    assert len(rows) == 1
    assert rows[0]["seq"] == 2
    assert rows[0]["chars"] == 5
    assert rows[0]["body_chars"] == 2
    assert rows[0]["path"] == "KR0f0001_002.yaml"


def test_overlong_front_can_include_first_juan(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path)

    summary = find_overlong_front_buckets_in_bundle(
        bundle_dir,
        min_chars=4,
        include_first=True,
    )

    assert [row["seq"] for row in summary["rows"]] == [1, 2]


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
    assert "wrote 1 overlong front bucket(s)" in out
    assert "scanned 1 bundles" in out
    rows = _jsonl_rows(report)
    assert len(rows) == 1
    assert rows[0]["textid"] == "KR0f0001"
    assert rows[0]["seq"] == 2


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
