from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig

from .conftest import write_bundle


def _client(corpus: Path, parallels_root: Path | None) -> TestClient:
    return TestClient(create_app(ServeConfig(
        corpus_root=corpus,
        index_path=corpus / "_corpus.bkkx",
        parallels_root=parallels_root,
    )))


def _write_asset(
    root: Path, textid: str, seq: int, name: str, markers: dict,
) -> None:
    directory = root / textid
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{textid}_{seq:03d}.{name}.parallels.yaml").write_text(
        yaml.safe_dump({"markers": markers}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_unconfigured_parallels_returns_empty(corpus: Path):
    client = _client(corpus, None)

    assert client.get("/api/server-info").json()["parallels_enabled"] is False
    response = client.get("/api/bundles/TEST0001/juan/1/parallels")
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_parallels_are_merged_filtered_paged_and_hydrated(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    write_bundle(corpus, "KR6q0001", "甲乙丙丁戊己庚辛壬癸", title="Local")
    remote_dir = write_bundle(
        corpus, "KR6q0002", "ABCDEFGHIJKLMNOP", title="Remote Title",
    )
    remote_dir_2 = write_bundle(
        corpus, "KR6q0003", "QRSTUVWXYZ", title="Second Remote",
    )
    remote_path = remote_dir / "KR6q0002_001.yaml"
    remote = yaml.safe_load(remote_path.read_text(encoding="utf-8"))
    remote["front"] = {"text": "前甲乙丙後", "hash": "sha256:0", "markers": []}
    remote_path.write_text(
        yaml.safe_dump(remote, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    remote_path_2 = remote_dir_2 / "KR6q0003_001.yaml"
    remote_2 = yaml.safe_load(remote_path_2.read_text(encoding="utf-8"))
    remote_2["front"] = {"text": "前甲乙", "hash": "sha256:1", "markers": []}
    remote_path_2.write_text(
        yaml.safe_dump(remote_2, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    root = tmp_path / "parallels"
    _write_asset(root, "KR6q0001", 1, "first", {
        "front": [],
        "body": [
            {
                "type": "parallel",
                "offset": 2,
                "length": 3,
                "ref": "6q2/1/@2+3",
                "edit_distance": 1,
                "toc_label": "Remote section",
            },
            {
                "type": "parallel",
                "offset": 7,
                "length": 1,
                "ref": "6q2/1/@8+1",
                "edit_distance": 0,
                "toc_label": "Remote section",
            },
            {
                "type": "parallel",
                "offset": 8,
                "length": 2,
                "ref": "6q999/1/@0+2",
                "edit_distance": 0,
                "toc_label": None,
            },
        ],
        "back": [],
    })
    _write_asset(root, "KR6q0001", 1, "second", {
        "front": [],
        "body": [{
            "type": "parallel",
            "offset": 7,
            "length": 1,
            "ref": "6q2/1/@8+1",
            "edit_distance": 0,
            "toc_label": "Front matter",
        }],
        "back": [],
    })
    _write_asset(root, "KR6q0001", 1, "third", {
        "front": [],
        "body": [{
            "type": "parallel",
            "offset": 9,
            "length": 2,
            "ref": "6q3/1/front@0+2",
            "edit_distance": 0,
            "toc_label": "Second remote",
        }],
        "back": [],
    })

    client = _client(corpus, root)
    assert client.get("/api/server-info").json()["parallels_enabled"] is True

    response = client.get(
        "/api/bundles/KR6q0001/juan/1/parallels",
        params={"offset": 0, "limit": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert body["source_title"] == "Local"
    assert body["source_char_count"] == 10
    assert body["sort"] == "local"
    assert body["remote_textid"] is None
    assert body["available_min_length"] == 1
    assert body["available_max_length"] == 3
    assert [item["textid"] for item in body["remote_texts"]] == ["KR6q0002", "KR6q0999", "KR6q0003"]
    assert len(body["locations"]) == 2
    first = body["locations"][0]
    assert first["source"] == "first"
    assert first["local_offset"] == 2
    assert first["local_text"] == "丙丁戊"
    assert first["textid"] == "KR6q0002"
    assert first["title"] == "Remote Title"
    assert first["text"] == "CDE"
    assert first["left"] == "AB"
    assert first["right"] == "FGHIJKLMNOP"
    assert first["available"] is True
    assert first["diff"]
    assert first["diff"][0][0] == "s"
    assert first["local_gap"] is None
    assert first["remote_gap"] is None
    second = body["locations"][1]
    assert second["textid"] == "KR6q0002"
    assert second["local_gap"] == 2
    assert second["remote_gap"] == 3

    remote_sorted = client.get(
        "/api/bundles/KR6q0001/juan/1/parallels",
        params={"sort": "remote"},
    ).json()
    assert remote_sorted["sort"] == "remote"
    assert remote_sorted["locations"][0]["textid"] == "KR6q0002"
    assert remote_sorted["locations"][1]["textid"] == "KR6q0002"
    assert remote_sorted["locations"][2]["textid"] == "KR6q0002"
    assert remote_sorted["locations"][3]["textid"] == "KR6q0003"
    assert remote_sorted["locations"][4]["textid"] == "KR6q0999"

    remote_filtered = client.get(
        "/api/bundles/KR6q0001/juan/1/parallels",
        params={"sort": "remote", "remote_textid": "KR6q0002"},
    ).json()
    assert remote_filtered["total"] == 3
    assert {loc["textid"] for loc in remote_filtered["locations"]} == {"KR6q0002"}

    filtered = client.get(
        "/api/bundles/KR6q0001/juan/1/parallels",
        params={"bucket": "body", "start": 3, "end": 4},
    ).json()
    assert filtered["total"] == 1
    assert filtered["locations"][0]["local_offset"] == 2

    length_filtered = client.get(
        "/api/bundles/KR6q0001/juan/1/parallels",
        params={"min_length": 2, "max_length": 2},
    ).json()
    assert length_filtered["total"] == 2
    assert length_filtered["available_min_length"] == 1
    assert length_filtered["available_max_length"] == 3
    assert {loc["local_length"] for loc in length_filtered["locations"]} == {2}

    second_page = client.get(
        "/api/bundles/KR6q0001/juan/1/parallels",
        params={"offset": 2, "limit": 2},
    ).json()
    assert second_page["locations"][0]["textid"] == "KR6q0002"
    assert second_page["locations"][1]["textid"] == "KR6q0999"
    assert second_page["locations"][1]["available"] is False


def test_malformed_markers_are_skipped(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    write_bundle(corpus, "KR6q0001", "甲乙丙")
    root = tmp_path / "parallels"
    _write_asset(root, "KR6q0001", 1, "bad", {
        "front": [],
        "body": [
            {"type": "parallel", "offset": 0, "length": 1, "ref": "bad"},
            {"type": "note", "offset": 0, "length": 1, "ref": "6q1/1/@0+1"},
        ],
        "back": [],
    })

    response = _client(corpus, root).get(
        "/api/bundles/KR6q0001/juan/1/parallels",
    )
    assert response.status_code == 200
    assert response.json()["locations"] == []


def test_length_filter_requires_both_bounds(tmp_path: Path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    write_bundle(corpus, "KR6q0001", "甲乙丙")
    root = tmp_path / "parallels"
    _write_asset(root, "KR6q0001", 1, "only", {
        "front": [],
        "body": [{
            "type": "parallel",
            "offset": 0,
            "length": 1,
            "ref": "6q1/1/@0+1",
        }],
        "back": [],
    })

    client = _client(corpus, root)
    response = client.get(
        "/api/bundles/KR6q0001/juan/1/parallels",
        params={"min_length": 1},
    )
    assert response.status_code == 400
